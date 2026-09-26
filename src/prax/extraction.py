"""Graph enrichment: typed triples extracted from documents by Claude.

For each document the extractor gets a compact input (title, creators,
date, venue, abstract, then the text's first chunks up to a character
budget) and returns triples against the *current* ontology, each with a
confidence and a short quote as evidence, plus triples it could not fit
(``unmapped``) and a one-paragraph summary. ``apply`` writes the fitting
triples through ``store.link`` (invariant 3), parks the rest in the review
queue (invariant 9), stores the summary in ``meta.summary`` and stamps
``meta.extraction`` so runs are incremental per ontology version.

The prompt is built from the composed ontology itself, so a version bump changes
what the model is asked for and re-selects every document. The Claude call
uses structured output (a JSON schema the response must satisfy) and a
cached system prompt shared by all documents. The ``extract`` step of
``prax.yaml`` (``prax.models``) chooses the model, ``PRAX_EXTRACT`` overrides
it for a run (default ``claude-opus-5``); ``PRAX_EXTRACT_EFFORT`` the
effort (default ``medium``). A stub extractor exists for tests.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Protocol

from prax import models, ontology, store, summaries

DEFAULT_MODEL = "claude-opus-5"
CALL_TIMEOUT = 180.0  # seconds; a call takes under 90, the SDK default is 600
INPUT_CHARS = 12_000  # of document text after the metadata header
# …and of its UTF-8, for the same reason the sections pass has one: a
# character costs what its script makes it cost, and 12,000 characters of
# Arabic was 23,834 tokens against a 16,384-token slot (2026-09-25).
INPUT_BYTES = 12_000
# After the head budget, the closing sections (conclusion, discussion) are
# appended up to this many characters: that is where a paper states what it
# showed, and the head alone stops in the middle of the method.
TAIL_CHARS = 4_000
_TAIL_HEADING = re.compile(
    r"conclu|discussion|summary|future work|outlook|limitations", re.IGNORECASE
)
TAIL_MARK = "\n\n[...]\n\n"
MAX_TRIPLES = 20  # the models rarely need more; halves output tokens (docs/eval)
CONFIDENCES = ("EXTRACTED", "INFERRED", "AMBIGUOUS")
# Names that are only a number or a bracketed reference ("[12]") are never
# entities; small models produce them for "cites".
_REFERENCE_NUMBER = re.compile(r"^\W*\d+(\W+\d+)*\W*$")
# What a small model writes for the document itself instead of its title.
_SELF_NAMES = frozenset(
    {
        "paper",
        "this paper",
        "the paper",
        "document",
        "this document",
        "the document",
        "this manual",
        "the manual",
        "this datasheet",
        "this article",
    }
)
# "Turner & Sahani, 2014", "Smith and Goto", "Solin et al., 2018": a citation
# without a title, which the review queue cannot resolve either
_AUTHOR_YEAR = re.compile(
    r"(\bet al\b|\b(19|20)\d\d[a-z]?\b|^[A-Z][\w'\-]+( (and|&) [A-Z][\w'\-]+)?$)"
)
# an unmapped item whose reason admits the text does not say it ("does not
# mention any dataset", "likely published in") is a guess, not a misfit
_HEDGED = re.compile(
    r"\b(does not|doesn't|do not|not) (mention|state|specify|name|say|provide)"
    r"|\b(likely|probably|presumably|possibly|may be|might be|appears to)\b",
    re.IGNORECASE,
)


def unmapped_is_noise(item: dict[str, Any]) -> bool:
    """Unmapped items the review queue should never see: citations by
    number or author-year, and guesses the model itself hedges."""
    dst = str(item.get("dst", ""))
    if item.get("rel") == "cites" and (
        _REFERENCE_NUMBER.match(dst) or _AUTHOR_YEAR.search(dst)
    ):
        return True
    return bool(_HEDGED.search(str(item.get("reason", ""))))


def supports_effort(model: str) -> bool:
    """The ``effort`` output setting exists on the Opus and Sonnet lines from
    4.5/4.6 on; Haiku rejects it with a 400."""
    return not model.startswith("claude-haiku")


# USD per million tokens (input, output), from the published table
# (platform.claude.com/docs/en/about-claude/pricing, read 2026-09-22).
# A model id may carry a date (claude-haiku-4-5-20251001); ``price`` looks
# up the longest name that matches, so a dated id is priced as its model.
# An unknown Claude model is charged at Opus rates, which is the safe way
# to be wrong when a budget depends on the number.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5-1": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-3-5": (0.8, 4.0),
}
# What a cached input token costs, as a share of the input price. The
# standard is a tenth; two models read their cache cheaper.
CACHE_READ = {
    "claude-fable-5-1": 0.025,
    "claude-mythos-5-1": 0.025,
    "claude-opus-5-5": 0.05,
}
CACHE_READ_SHARE = 0.1
# A cache write: prax asks for the five-minute cache (``ephemeral`` with no
# ttl), which is 1.25 times the input price; the hour's cache would be 2.
CACHE_WRITE_SHARE = 1.25


# ------------------------------------------------------------------ input


@dataclass
class DocumentInput:
    doc_id: int
    title: str
    header: str  # metadata lines
    text: str  # the text budget of the document
    domains: list[str] | None = None  # the ontology modules to read it against

    def as_message(self) -> str:
        return f"{self.header}\n\n---\n\n{self.text}"

    def ontology(self) -> ontology.Ontology:
        """The ontology this document is extracted against: the core plus
        its domains, or the whole when it has no domain set."""
        return ontology.current().for_domains(self.domains)


def self_types(onto: ontology.Ontology) -> tuple[str, ...]:
    """What the document itself may be in this ontology: the modules'
    self types (a paper; a manual, datasheet, schematic or article), or a
    plain document when no module says."""
    return onto.self_types or ("document",)


def _self_rule(onto: ontology.Ontology) -> str:
    kinds = self_types(onto)
    if len(kinds) == 1:
        what = f"a {kinds[0]} entity"
    else:
        what = (
            "an entity of whichever of these types fits the text best:"
            f" {', '.join(kinds)}"
        )
    return (
        f"The document itself is {what}, named exactly by its Title line, or"
        " a page or project entity when the header has a Kind line saying so."
        " Every triple about the document uses that name."
    )


def build_input(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    max_chars: int = INPUT_CHARS,
    max_bytes: int = INPUT_BYTES,
    tail_chars: int = TAIL_CHARS,
) -> DocumentInput:
    doc = store.get_document(con, doc_id, max_chars=0)
    if doc is None:
        raise KeyError(f"no such document: {doc_id}")
    meta = doc["meta"] or {}
    domains = list(meta["domains"]) if meta.get("domains") else None
    lines = [f"Title: {doc['title'] or '(untitled)'}"]
    if domains:
        lines.append("Domains: " + ", ".join(domains))
    page = meta.get("page") or {}
    if page.get("kind"):
        kind = page["kind"]
        lines.append("Kind: project" if kind == "project" else f"Kind: page ({kind})")
    creators = [c.get("name") for c in meta.get("creators", []) if c.get("name")]
    if creators:
        lines.append("Authors: " + ", ".join(creators))
    if meta.get("date"):
        lines.append(f"Date: {meta['date']}")
    fields = meta.get("fields", {})
    venue = (
        fields.get("publicationTitle")
        or fields.get("proceedingsTitle")
        or fields.get("conferenceName")
        or fields.get("publisher")
    )
    if venue:
        lines.append(f"Venue: {venue}")
    if meta.get("doi"):
        lines.append(f"DOI: {meta['doi']}")
    if meta.get("abstract"):
        lines.append(f"Abstract: {meta['abstract']}")
    # the text: chunks in order, up to the head budget; then the closing
    # sections that fell past it, up to the tail. Figure captions and code
    # are skipped, and so are the regions of a capture that are not the
    # document: its advertising and what its readers wrote under it
    skip = ("figure", "code", "ad", "comment")
    chunks = [
        c
        for c in store.list_chunks(con, doc_id)
        if c["kind"] not in skip and c["text"].strip()
    ]
    parts: list[str] = []
    used = 0
    used_bytes = 0
    cut = len(chunks)
    for i, c in enumerate(chunks):
        piece = c["text"].strip()
        room = max(0, max_chars - used)
        room_bytes = max(0, max_bytes - used_bytes)
        if len(piece) > room or len(piece.encode("utf-8")) > room_bytes:
            piece = models.fits(piece, chars=room, byts=room_bytes)
        if piece:
            parts.append(piece)
        used += len(piece) + 2
        used_bytes += len(piece.encode("utf-8")) + 2
        if used >= max_chars or used_bytes >= max_bytes:
            cut = i + 1
            break
    tail: list[str] = []
    tail_used = 0
    for c in chunks[cut:]:
        heading = c.get("heading") or []
        if not (heading and _TAIL_HEADING.search(heading[-1])):
            continue
        piece = c["text"].strip()
        if tail_used + len(piece) > tail_chars:
            piece = piece[: max(0, tail_chars - tail_used)]
        if piece:
            tail.append(piece)
        tail_used += len(piece) + 2
        if tail_used >= tail_chars:
            break
    text = "\n\n".join(parts)
    if tail:
        text += TAIL_MARK + "\n\n".join(tail)
    return DocumentInput(
        doc_id, doc["title"] or "", "\n".join(lines), text, domains=domains
    )


# ----------------------------------------------------------------- output


@dataclass
class Triple:
    src: str
    src_type: str
    rel: str
    dst: str
    dst_type: str
    confidence: str
    evidence: str
    # the names as the document printed them, where the graph's name is
    # another (a common noun written in the library's language); empty
    # when they are the same
    src_as: str = ""
    dst_as: str = ""


@dataclass
class Extraction:
    triples: list[Triple] = field(default_factory=list)
    unmapped: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    usage: dict[str, int] = field(default_factory=dict)


def output_schema(onto: ontology.Ontology) -> dict[str, Any]:
    """The JSON schema the model's answer must satisfy."""
    types = sorted(onto.entity_types)
    rels = sorted(onto.relations)
    entity = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "type": {"type": "string", "enum": types},
            # the name as the document printed it, only where it differs;
            # the schema says so itself, since the shared rules must not
            # name one output format's fields
            "as": {
                "type": "string",
                "description": "the name as the document prints it, where"
                " name is written in another language; omitted otherwise",
            },
        },
        "required": ["name", "type"],
        "additionalProperties": False,
    }
    triple = {
        "type": "object",
        "properties": {
            "src": entity,
            "rel": {"type": "string", "enum": rels},
            "dst": entity,
            "confidence": {"type": "string", "enum": list(CONFIDENCES)},
            "evidence": {"type": "string"},
        },
        "required": ["src", "rel", "dst", "confidence", "evidence"],
        "additionalProperties": False,
    }
    unmapped = {
        "type": "object",
        "properties": {
            "src": {"type": "string"},
            "rel": {"type": "string"},
            "dst": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["src", "rel", "dst", "reason"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "triples": {"type": "array", "items": triple},
            "unmapped": {"type": "array", "items": unmapped},
        },
        "required": ["summary", "triples", "unmapped"],
        "additionalProperties": False,
    }


def _written_in() -> str:
    """The language this library is written in, named (`graph.language`).

    The prompt is cached across calls, so this is part of the cache key
    in effect: a host that changes the setting gets a different prompt
    and the cache is cold once.
    """
    from prax import language

    return language.name(language.canonical()) or "English"


def system_prompt(
    onto: ontology.Ontology,
    *,
    output: str = "json",
    max_triples: int = MAX_TRIPLES,
) -> str:
    """Built from the ontology file, so the prompt and the validator agree.

    ``output`` is ``json`` (the schema of ``output_schema``, Claude) or
    ``lines`` (the tab-separated format of ``prax.lineformat``, local
    models). The json text must stay stable: it is cached across calls.
    """
    if output not in ("json", "lines"):
        raise ValueError(f"unknown output format {output!r}")
    ent = "\n".join(
        f"- {name}: {_desc(onto, name)}" for name in sorted(onto.entity_types)
    )
    rel = "\n".join(
        f"- {r.name} ({', '.join(sorted(r.domain)) or 'any'} -> "
        f"{', '.join(sorted(r.range)) or 'any'}): {r.description}"
        for r in (onto.relations[n] for n in sorted(onto.relations))
    )
    rules = [
        _self_rule(onto),
        (
            f"Emit at most {max_triples} triples. Prefer the few that a reader"
            " searching this library would want: what the paper is about (2-6"
            " concepts), what it proposes, what methods, tools and datasets it uses,"
            " its listed authors and venue, its central claims (at most 3), and"
            " other papers it names by title."
        ),
        (
            "Entity names are canonical: full author names as printed; concepts and"
            " methods as established lowercase noun phrases, singular, acronyms"
            " expanded once if the text does; do not invent entities the text does"
            " not support."
        ),
        (
            # Names are not translated here. From 2026-09-24 to -26 this rule
            # asked for common names in the library's language, and a new
            # entity then kept no trace of the word the document printed —
            # which is the label a query in that language crosses on. The
            # watched `vocabulary` step translates the common names instead
            # (which types those are is the ontology's `naming:` key) and
            # keeps the printed word as a label every time it does
            # (docs/eval/apfelkuchen-2026-09-26.md). A model asked to write
            # both names on one line wrote neither, or wrote junk.
            f"Write the summary in {_written_in()} even where the document is not."
            " Write every name as the document prints it, in the document's"
            " language, accents and all — never translate a name; the library"
            " puts the common ones into its own language afterwards and keeps"
            " the word you wrote beside them."
        ),
        (
            "confidence: EXTRACTED when the text states it, INFERRED when it clearly"
            " follows, AMBIGUOUS when you are unsure. evidence: a short verbatim quote"
            " (under 200 characters) from the text that supports the triple; for"
            " authored_by and published_in quote the header line."
        ),
        (
            # Without this the container becomes a `paper` and then a hub:
            # one proceedings volume reached degree 1,951 in this library,
            # the largest node in it (docs/eval/
            # traverse-neighbourhood-2026-09-25.md)
            "A proceedings volume, journal, conference series or book that a work"
            " appeared *in* is a venue, not a paper. Name it with published_in and"
            " never as something the document cites; cite the individual work by"
            " its own title."
        ),
        (
            "If a relationship matters but no relation or type fits, put it in unmapped"
            " with a one-line reason instead of forcing it."
        ),
        (
            f"summary: two or three sentences in {_written_in()} that a reader would"
            " use to decide whether to open the document."
        ),
    ]
    answer = (
        "Return JSON matching the schema."
        if output == "json"
        else "Answer in the line format given at the end."
    )
    parts = [
        (
            "You extract a small knowledge graph from one research document at a"
            " time, for a personal research library about audio, signal processing"
            f" and music. Work only from the text given. {answer}"
        ),
        "",
        f"Entity types (ontology {onto.version}):",
        ent,
        "",
        "Relation types, with the allowed source -> target types:",
        rel,
        "",
        "Rules:",
        *(f"- {r}" for r in rules),
    ]
    if output == "lines":
        from prax import lineformat

        parts += ["", lineformat.prompt_section(max_triples=max_triples)]
    return "\n".join(parts)


def _desc(onto: ontology.Ontology, name: str) -> str:
    """The type's description for the prompt, with its parent when it has
    one ("a kind of person"), so the model knows the family it belongs to."""
    text = onto.describe(name)
    parent = onto.parent(name)
    return f"{text} (a kind of {parent})" if parent else text


# -------------------------------------------------------------- extractors


class Extractor(Protocol):
    name: str

    def extract(self, doc: DocumentInput) -> Extraction: ...


def parse_output(data: dict[str, Any]) -> Extraction:
    triples = []
    for t in data.get("triples", []):
        triples.append(
            Triple(
                src=str(t["src"]["name"]).strip(),
                src_type=str(t["src"]["type"]),
                rel=str(t["rel"]),
                dst=str(t["dst"]["name"]).strip(),
                dst_type=str(t["dst"]["type"]),
                confidence=str(t.get("confidence", "AMBIGUOUS")),
                evidence=str(t.get("evidence", ""))[:300],
                src_as=str(t["src"].get("as") or "").strip(),
                dst_as=str(t["dst"].get("as") or "").strip(),
            )
        )
    return Extraction(
        triples=triples,
        unmapped=list(data.get("unmapped", [])),
        summary=str(data.get("summary", "")).strip(),
    )


@dataclass
class ClaudeExtractor:
    """One ``messages.create`` per document with a JSON-schema output format
    and the ontology prompt cached across calls."""

    model: str = DEFAULT_MODEL
    effort: str = "medium"
    client: Any = None
    # prompt and schema per ontology subset (a document's domains), keyed by
    # the subset's version so the cached prompt text stays identical
    _prompts: dict[str, tuple[str, dict[str, Any]]] = field(
        default_factory=dict, init=False, repr=False
    )

    @property
    def name(self) -> str:
        return self.model

    def _ensure(self) -> None:
        if self.client is None:
            import anthropic

            self.client = anthropic.Anthropic(timeout=CALL_TIMEOUT, max_retries=3)

    def _prompt(self, doc: DocumentInput) -> tuple[str, dict[str, Any]]:
        onto = doc.ontology()
        if onto.version not in self._prompts:
            self._prompts[onto.version] = (system_prompt(onto), output_schema(onto))
        return self._prompts[onto.version]

    def params(self, doc: DocumentInput) -> dict[str, Any]:
        """The request parameters (shared by the sync and the batch path)."""
        self._ensure()
        system, schema = self._prompt(doc)
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": schema}
        }
        if supports_effort(self.model):
            output_config["effort"] = self.effort
        return {
            "model": self.model,
            "max_tokens": 8000,
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": doc.as_message()}],
            "output_config": output_config,
        }

    def extract(self, doc: DocumentInput) -> Extraction:
        self._ensure()
        response = self.client.messages.create(**self.params(doc))
        return self.from_message(response)

    @staticmethod
    def from_message(response: Any) -> Extraction:
        if response.stop_reason == "refusal":
            raise RuntimeError(f"refused: {getattr(response, 'stop_details', None)}")
        text = next(b.text for b in response.content if b.type == "text")
        out = parse_output(json.loads(text))
        usage = response.usage
        out.usage = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_input_tokens": int(
                getattr(usage, "cache_read_input_tokens", 0) or 0
            ),
            "cache_creation_input_tokens": int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
        }
        return out


_WORD = re.compile(r"[A-Za-z][A-Za-z-]{3,}")


@dataclass
class StubExtractor:
    """Deterministic triples from the metadata header, for tests: authors,
    venue, the two most frequent long words as concepts, one unmapped."""

    name: str = "stub"

    def extract(self, doc: DocumentInput) -> Extraction:
        triples: list[Triple] = []
        for line in doc.header.splitlines():
            if line.startswith("Authors: "):
                for a in line[9:].split(", "):
                    triples.append(
                        Triple(
                            doc.title,
                            "paper",
                            "authored_by",
                            a,
                            "author",
                            "EXTRACTED",
                            line,
                        )
                    )
            if line.startswith("Venue: "):
                triples.append(
                    Triple(
                        doc.title,
                        "paper",
                        "published_in",
                        line[7:],
                        "venue",
                        "EXTRACTED",
                        line,
                    )
                )
        counts: dict[str, int] = {}
        for w in _WORD.findall(doc.text.lower()):
            counts[w] = counts.get(w, 0) + 1
        for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:2]:
            triples.append(
                Triple(doc.title, "paper", "about", w, "concept", "INFERRED", w)
            )
        return Extraction(
            triples=triples,
            unmapped=[
                {
                    "src": doc.title,
                    "rel": "funded_by",
                    "dst": "?",
                    "reason": "no relation",
                }
            ],
            summary=f"Stub summary of {doc.title}.",
            usage={"input_tokens": len(doc.text) // 4, "output_tokens": 50},
        )


class Runtime(Protocol):
    """What ``LocalExtractor`` needs from a model: ``prax.models.OpenAIRuntime``
    (llama-server honours the grammar) or a test double."""

    name: str

    def chat(
        self,
        system: str,
        user: str,
        *,
        grammar: str | None = None,
        max_tokens: int = 2000,
        temperature: float = 0.0,
        repeat_penalty: float = 1.0,
        stop: list[str] | None = None,
    ) -> tuple[str, dict[str, int]]: ...


@dataclass
class LocalExtractor:
    """A GGUF model in process answering in the line format of
    ``prax.lineformat`` under a grammar that bounds the output (small models
    do not stop on their own; see ``docs/eval/local-llm-2026-09-08.md``)."""

    runtime: Runtime
    max_triples: int = 20
    max_tokens: int = 2000
    # Greedy decoding loops on the rigid line format; a little temperature
    # and a repeat penalty keep a 7B model moving (benchmark in docs/eval).
    temperature: float = 0.3
    repeat_penalty: float = 1.1
    _prompts: dict[str, tuple[str, str]] = field(
        default_factory=dict, init=False, repr=False
    )

    @property
    def name(self) -> str:
        return self.runtime.name

    def _prompt(self, doc: DocumentInput) -> tuple[ontology.Ontology, str, str]:
        from prax import lineformat

        onto = doc.ontology()
        if onto.version not in self._prompts:
            self._prompts[onto.version] = (
                system_prompt(onto, output="lines", max_triples=self.max_triples),
                lineformat.grammar(onto, max_triples=self.max_triples),
            )
        system, grammar = self._prompts[onto.version]
        return onto, system, grammar

    def extract(self, doc: DocumentInput) -> Extraction:
        from prax import lineformat

        onto, system, grammar = self._prompt(doc)
        cut = 0
        try:
            text, usage = self._chat(system, doc, grammar)
        except RuntimeError as exc:
            # the server's slot is smaller than this prompt (a schematic's
            # symbols, a dense script tokenize far past four chars a
            # token): cut the text to the ratio the server states and ask
            # once more, rather than fail every pass
            fit = _fits_after(str(exc))
            if fit is None:
                raise
            doc = DocumentInput(
                doc.doc_id,
                doc.title,
                doc.header,
                doc.text[: max(500, int(len(doc.text) * fit))],
                doc.domains,
            )
            cut = 1
            text, usage = self._chat(system, doc, grammar)
        result = lineformat.parse(text)
        if cut:
            result.usage["cut"] = cut
        me = set(self_types(onto))
        for t in result.triples:  # the document is named by its title
            if t.src.lower() in _SELF_NAMES and t.src_type in me:
                t.src = doc.title
            if t.dst.lower() in _SELF_NAMES and t.dst_type in me:
                t.dst = doc.title
        result.usage.update(usage)
        return result

    def _chat(
        self, system: str, doc: DocumentInput, grammar: str
    ) -> tuple[str, dict[str, Any]]:
        return self.runtime.chat(
            system,
            doc.as_message(),
            grammar=grammar,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            repeat_penalty=self.repeat_penalty,
        )


_EXCEEDS = re.compile(
    r"request \((\d+) tokens\) exceeds the available context size \((\d+) tokens\)"
)


def _fits_after(error: str) -> float | None:
    """The share of the text to keep after llama-server refused a prompt as
    too long (its message carries both numbers), with room for the answer;
    None when the error is another kind."""
    m = _EXCEEDS.search(error)
    if not m:
        return None
    asked, ctx = int(m.group(1)), int(m.group(2))
    return max(0.1, min(0.9, (ctx * 0.85) / asked))


def note_failure(
    con: sqlite3.Connection, doc_id: int, error: str, *, extractor: str
) -> None:
    """Remember that ``extractor`` could not read this document under the
    current ontology (``meta.extraction_error``), so the selection leaves
    it out until the ontology moves or a reading succeeds; the
    ``extraction-failed`` ailment lists them."""
    onto = ontology.current().for_domains(store.document_domains(con, doc_id))
    meta = store.get_meta(con, doc_id)
    meta["extraction_error"] = {
        "extractor": extractor,
        "ontology_version": onto.version,
        "at": store.now(),
        "error": error[:500],
    }
    store.set_meta(con, doc_id, meta)


def current(step: str = "extract") -> Extractor:
    """The extractor for a step of ``prax.yaml`` (``extract``, or ``promote``
    for the expensive pass over flagged documents; ``PRAX_<STEP>`` overrides):
    Claude with the JSON schema, a stub, or a local or served model with the
    line format and grammar."""
    from prax import models

    spec = models.resolve(step)
    if spec is None:
        raise RuntimeError(
            f"extraction is off: steps.{step}.model / PRAX_{step.upper()} is none"
        )
    if spec.kind == "stub":
        return StubExtractor()
    opts = models.settings(step)
    if spec.kind == "claude":
        assert spec.model is not None
        return ClaudeExtractor(
            model=spec.model, effort=str(opts.get("effort") or spec.effort or "medium")
        )
    return LocalExtractor(
        models.runtime(spec), max_triples=int(opts.get("max_triples", MAX_TRIPLES))
    )


# ------------------------------------------------------------------ apply


def _placeholder(name: str) -> bool:
    """A name the model copied out of its own prompt instead of reading it
    from the text: "source name", "<name>", "unknown". The heal pass
    (`store.repair`) mends what older passes wrote; this keeps new ones
    from writing it at all."""
    plain = name.strip().lower()
    return plain in store.PLACEHOLDER_NAMES or not store.clean_name(name)


@dataclass
class ApplyReport:
    retired: int = 0  # the producer's earlier reading under another version
    linked: int = 0
    existing: int = 0
    queued: int = 0
    rejected: int = 0
    printed: int = 0  # words kept as the document printed them


def apply(
    con: sqlite3.Connection,
    doc_id: int,
    extraction: Extraction,
    *,
    extractor: str,
    run: str | None = None,
) -> ApplyReport:
    """Write an extraction: fitting triples become edges (once), misfits go
    to the review queue, the document gets ``meta.summary`` and the
    ``meta.extraction`` stamp. The ontology is the document's own subset
    (its domains), and the stamp carries that subset's version."""
    onto = ontology.current().for_domains(store.document_domains(con, doc_id))
    report = ApplyReport()
    # the same producer read this document before under another subset
    # or module version: that reading is superseded by this one
    report.retired = store.retire_reading(
        con, doc_id, producer=extractor, except_version=onto.version
    )
    stale = store.get_meta(con, doc_id).get("extraction_stale")
    if stale and (stale.get("extractor") == extractor or stale.get("requested")):
        # the text this producer read has been replaced since (a parser
        # that reads the mathematics, OCR over a scan), or a person asked
        # for the document to be read again: this producer's whole
        # earlier reading of the document goes, whatever version it was
        # under, and this one is written afresh — history kept (invariant 8)
        report.retired += store.retire_reading(
            con, doc_id, producer=extractor, except_version=""
        )
    page_titles = store.page_titles(con)
    lang = store.get_meta(con, doc_id).get("lang")
    for t in extraction.triples:
        edge = store.Edge(t.src, t.src_type, t.rel, t.dst, t.dst_type)
        # page and project entities exist only as pages in the store; a
        # model that names one from paper text (a research consortium, a
        # web page) parks the triple for a person (rationale R15)
        stray = [
            n
            for n, ty in ((t.src, t.src_type), (t.dst, t.dst_type))
            if ty in ("page", "project") and n not in page_titles
        ]
        if stray:
            store.queue_review(
                con,
                src=t.src,
                src_type=t.src_type,
                rel=t.rel,
                dst=t.dst,
                dst_type=t.dst_type,
                reason=f"{stray[0]!r} is not a page in the store",
                source_doc=doc_id,
                evidence=t.evidence,
            )
            report.queued += 1
            continue
        if (
            not t.src
            or not t.dst
            or t.confidence not in CONFIDENCES
            or _REFERENCE_NUMBER.match(t.src)
            or _REFERENCE_NUMBER.match(t.dst)
            or _placeholder(t.src)
            or _placeholder(t.dst)
        ):
            report.rejected += 1
            continue
        try:
            onto.check_edge(t.src_type, t.rel, t.dst_type)
        except ValueError as exc:
            store.queue_review(
                con,
                src=t.src,
                src_type=t.src_type,
                rel=t.rel,
                dst=t.dst,
                dst_type=t.dst_type,
                reason=str(exc),
                source_doc=doc_id,
                evidence=t.evidence,
            )
            report.queued += 1
            continue
        if store.find_edges(con, edge):
            report.existing += 1
        else:
            store.link(
                con,
                edge,
                confidence=t.confidence,
                source_doc=doc_id,
                ontology_version=onto.version,
                evidence=t.evidence or None,
                producer=extractor,
                run=run,
            )
            report.linked += 1
        # the word the document printed, kept whether or not the edge was
        # new: the label is about the thing, not about this fact
        for name, etype, said in (
            (t.src, t.src_type, t.src_as),
            (t.dst, t.dst_type, t.dst_as),
        ):
            if said:
                report.printed += store.keep_printed(
                    con,
                    name,
                    etype,
                    said,
                    lang=lang,
                    source_doc=doc_id,
                    producer=extractor,
                    run=run,
                )
    for u in extraction.unmapped:
        if unmapped_is_noise(u):
            report.rejected += 1
            continue
        store.queue_review(
            con,
            src=str(u.get("src", "")),
            src_type=None,
            rel=str(u.get("rel", "")),
            dst=str(u.get("dst", "")),
            dst_type=None,
            reason="unmapped: " + str(u.get("reason", "")),
            source_doc=doc_id,
        )
        report.queued += 1
    meta = store.get_meta(con, doc_id)
    meta.pop("extraction_error", None)  # a reading that worked
    stale_now = meta.get("extraction_stale") or {}
    if stale_now.get("extractor") == extractor or stale_now.get("requested"):
        meta.pop("extraction_stale")
    if extraction.summary:
        summaries.keep(meta, extraction.summary)
    if meta.get("extraction"):  # every model that has read the document
        history = meta.setdefault("extraction_history", [])
        history.append(meta["extraction"])
    meta["extraction"] = {
        "extractor": extractor,
        "ontology_version": onto.version,
        "run": run,
        "at": store.now(),
        "linked": report.linked,
        "existing": report.existing,
        "queued": report.queued,
        "rejected": report.rejected,
        **extraction.usage,
    }
    store.set_meta(con, doc_id, meta)
    if report.queued:
        # the rules never invent; what they can settle from the names and
        # the document's title need not wait in the queue for a pass
        from prax import review

        rules = review.apply_typing_rules(
            con, onto=onto, source_doc=doc_id, run=f"typing-after-{run or extractor}"
        )
        report.queued -= rules.linked + rules.existing + rules.dropped
        report.linked += rules.linked
    return report


def _known(model: str) -> str | None:
    """The name of ``PRICES`` this model id is: the longest one it starts
    with, so ``claude-haiku-4-5-20251001`` is Haiku 4.5 and not a stranger."""
    matches = [name for name in PRICES if model.startswith(name)]
    return max(matches, key=len) if matches else None


def price(model: str) -> tuple[float, float]:
    """USD per million input and output tokens: ``prax.yaml`` may price a
    served model; local models and unpriced servers cost nothing; Claude
    models come from the table, unknown ones at Opus rates."""
    from prax import models

    priced = models.price_of(model)
    if priced:
        return priced
    if model.startswith("local:") or model == "stub" or "@" in model:
        return (0.0, 0.0)
    known = _known(model)
    return PRICES[known] if known else (5.0, 25.0)


def cost_usd(model: str, usage: dict[str, int]) -> float:
    """What one call cost, from what it used: the input and output prices
    of the model, a cache write at ``CACHE_WRITE_SHARE`` of the input
    price and a cache read at this model's share of it."""
    price_in, price_out = price(model)
    read_share = CACHE_READ.get(_known(model) or model, CACHE_READ_SHARE)
    plain = usage.get("input_tokens", 0)
    cached = usage.get("cache_read_input_tokens", 0)
    written = usage.get("cache_creation_input_tokens", 0)
    out = usage.get("output_tokens", 0)
    return (
        plain * price_in
        + cached * price_in * read_share
        + written * price_in * CACHE_WRITE_SHARE
    ) / 1e6 + out * price_out / 1e6
