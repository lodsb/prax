"""Graph enrichment: typed triples extracted from documents by Claude.

For each document the extractor gets a compact input (title, creators,
date, venue, abstract, then the text's first chunks up to a character
budget) and returns triples against the *current* ontology, each with a
confidence and a short quote as evidence, plus triples it could not fit
(``unmapped``) and a one-paragraph summary. ``apply`` writes the fitting
triples through ``store.link`` (invariant 3), parks the rest in the review
queue (invariant 9), stores the summary in ``meta.summary`` and stamps
``meta.extraction`` so runs are incremental per ontology version.

The prompt is built from ``ontology.yaml`` itself, so a version bump changes
what the model is asked for and re-selects every document. The Claude call
uses structured output (a JSON schema the response must satisfy) and a
cached system prompt shared by all documents. ``PRAX_EXTRACT_MODEL``
chooses the model (default ``claude-opus-5``); ``PRAX_EXTRACT_EFFORT`` the
effort (default ``medium``). A stub extractor exists for tests.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from prax import ontology, store

DEFAULT_MODEL = "claude-opus-5"
CALL_TIMEOUT = 180.0  # seconds; a call takes under 90, the SDK default is 600
INPUT_CHARS = 12_000  # of document text after the metadata header
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
_SELF_NAMES = frozenset({"paper", "this paper", "the paper", "document"})


def supports_effort(model: str) -> bool:
    """The ``effort`` output setting exists on the Opus and Sonnet lines from
    4.5/4.6 on; Haiku rejects it with a 400."""
    return not model.startswith("claude-haiku")


# Prices per million tokens (input, output), for the running cost estimate.
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


# ------------------------------------------------------------------ input


@dataclass
class DocumentInput:
    doc_id: int
    title: str
    header: str  # metadata lines
    text: str  # the text budget of the document

    def as_message(self) -> str:
        return f"{self.header}\n\n---\n\n{self.text}"


def build_input(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    max_chars: int = INPUT_CHARS,
    tail_chars: int = TAIL_CHARS,
) -> DocumentInput:
    doc = store.get_document(con, doc_id, max_chars=0)
    if doc is None:
        raise KeyError(f"no such document: {doc_id}")
    meta = doc["meta"] or {}
    lines = [f"Title: {doc['title'] or '(untitled)'}"]
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
    # the text: chunks in order, figure captions and code skipped, up to the
    # head budget; then the closing sections that fell past it, up to the tail
    chunks = [
        c
        for c in store.list_chunks(con, doc_id)
        if c["kind"] not in ("figure", "code") and c["text"].strip()
    ]
    parts: list[str] = []
    used = 0
    cut = len(chunks)
    for i, c in enumerate(chunks):
        piece = c["text"].strip()
        if used + len(piece) > max_chars:
            piece = piece[: max(0, max_chars - used)]
        if piece:
            parts.append(piece)
        used += len(piece) + 2
        if used >= max_chars:
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
    return DocumentInput(doc_id, doc["title"] or "", "\n".join(lines), text)


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
        (
            "The document itself is a paper entity named exactly by its Title line,"
            " or a page or project entity when the header has a Kind line saying"
            " so. Every triple about the document uses that name."
        ),
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
            "confidence: EXTRACTED when the text states it, INFERRED when it clearly"
            " follows, AMBIGUOUS when you are unsure. evidence: a short verbatim quote"
            " (under 200 characters) from the text that supports the triple; for"
            " authored_by and published_in quote the header line."
        ),
        (
            "If a relationship matters but no relation or type fits, put it in unmapped"
            " with a one-line reason instead of forcing it."
        ),
        (
            "summary: two or three sentences a reader would use to decide whether to"
            " open the document."
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
        f"Entity types (ontology version {onto.version}):",
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
    # Ontology keeps entity descriptions in the YAML; the parsed object holds
    # only names, so read the file's mapping form again for the prompt.
    return _entity_descriptions().get(name, "")


def _entity_descriptions() -> dict[str, str]:
    import yaml

    data = yaml.safe_load(ontology.path().read_text(encoding="utf-8")) or {}
    section = data.get("entity_types") or {}
    if isinstance(section, dict):
        return {
            str(k): str((v or {}).get("description", "")).strip()
            for k, v in section.items()
        }
    return {}


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
    _onto: ontology.Ontology | None = field(default=None, init=False, repr=False)

    @property
    def name(self) -> str:
        return self.model

    def _ensure(self) -> None:
        if self.client is None:
            import anthropic

            self.client = anthropic.Anthropic(timeout=CALL_TIMEOUT, max_retries=3)
        if self._onto is None:
            self._onto = ontology.current()

    def params(self, doc: DocumentInput) -> dict[str, Any]:
        """The request parameters (shared by the sync and the batch path)."""
        self._ensure()
        assert self._onto is not None
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": output_schema(self._onto)}
        }
        if supports_effort(self.model):
            output_config["effort"] = self.effort
        return {
            "model": self.model,
            "max_tokens": 8000,
            "system": [
                {
                    "type": "text",
                    "text": system_prompt(self._onto),
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
    """What ``LocalExtractor`` needs from a model: ``prax.local_llm.LlamaRuntime``
    or a test double."""

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
    _onto: ontology.Ontology | None = field(default=None, init=False, repr=False)
    _system: str = field(default="", init=False, repr=False)
    _grammar: str = field(default="", init=False, repr=False)

    @property
    def name(self) -> str:
        return self.runtime.name

    def _ensure(self) -> None:
        if self._onto is None:
            from prax import lineformat

            self._onto = ontology.current()
            self._system = system_prompt(
                self._onto, output="lines", max_triples=self.max_triples
            )
            self._grammar = lineformat.grammar(self._onto, max_triples=self.max_triples)

    def extract(self, doc: DocumentInput) -> Extraction:
        from prax import lineformat

        self._ensure()
        text, usage = self.runtime.chat(
            self._system,
            doc.as_message(),
            grammar=self._grammar,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            repeat_penalty=self.repeat_penalty,
        )
        result = lineformat.parse(text)
        for t in result.triples:  # the document is named by its title
            if t.src.lower() in _SELF_NAMES and t.src_type == "paper":
                t.src = doc.title
            if t.dst.lower() in _SELF_NAMES and t.dst_type == "paper":
                t.dst = doc.title
        result.usage.update(usage)
        return result


def current() -> Extractor:
    setting = os.environ.get("PRAX_EXTRACT", DEFAULT_MODEL)
    if setting == "stub":
        return StubExtractor()
    if setting == "local":
        path = os.environ.get("PRAX_LOCAL_MODEL")
        if not path:
            raise RuntimeError("PRAX_EXTRACT=local needs PRAX_LOCAL_MODEL=<model.gguf>")
        from prax import local_llm

        ctx = int(os.environ.get("PRAX_LOCAL_CTX", local_llm.DEFAULT_CTX))
        return LocalExtractor(local_llm.shared_runtime(path, n_ctx=ctx))
    return ClaudeExtractor(
        model=os.environ.get("PRAX_EXTRACT_MODEL", setting),
        effort=os.environ.get("PRAX_EXTRACT_EFFORT", "medium"),
    )


# ------------------------------------------------------------------ apply


@dataclass
class ApplyReport:
    linked: int = 0
    existing: int = 0
    queued: int = 0
    rejected: int = 0


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
    ``meta.extraction`` stamp."""
    onto = ontology.current()
    report = ApplyReport()
    page_titles = store.page_titles(con)
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
            continue
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
    for u in extraction.unmapped:
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
    if extraction.summary:
        meta["summary"] = extraction.summary
    if meta.get("extraction"):  # every model that has read the document
        history = meta.setdefault("extraction_history", [])
        history.append(meta["extraction"])
    meta["extraction"] = {
        "extractor": extractor,
        "ontology_version": onto.version,
        "run": run,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "linked": report.linked,
        "existing": report.existing,
        "queued": report.queued,
        "rejected": report.rejected,
        **extraction.usage,
    }
    store.set_meta(con, doc_id, meta)
    return report


def price(model: str) -> tuple[float, float]:
    """USD per million input and output tokens; local models cost nothing."""
    if model.startswith("local:") or model == "stub":
        return (0.0, 0.0)
    return PRICES.get(model, (5.0, 25.0))


def cost_usd(model: str, usage: dict[str, int]) -> float:
    """Rough cost of one call from its usage, cache reads at a tenth."""
    price_in, price_out = price(model)
    plain = usage.get("input_tokens", 0)
    cached = usage.get("cache_read_input_tokens", 0)
    written = usage.get("cache_creation_input_tokens", 0)
    out = usage.get("output_tokens", 0)
    return (
        plain * price_in + cached * price_in * 0.1 + written * price_in * 1.25
    ) / 1e6 + out * price_out / 1e6
