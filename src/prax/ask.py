"""Ask: a question answered from the library, with citations.

Three steps, the first two the store's. ``gather`` runs the hybrid search,
takes the best chunk of each of the top documents as a numbered passage,
and adds what the graph records about those documents (their extracted
relations, canonical names), everything bounded in characters: the
bundle is the whole context a model gets, and it fits a 7B model with an
8 K window. ``answer`` hands the bundle to the model of the ``ask`` step
in ``prax.yaml`` (``prax.models``; ``PRAX_ASK`` overrides it for a run): a
GGUF model in process, an OpenAI-compatible server, Claude, or ``none``,
which returns the bundle alone, for a client that is itself a model
(Claude Code over MCP) and for the Pi-class serving host, which loads no
model (invariant 7). Without a file the default is ``none``: the bundle
comes back for the caller's own model.

The answer cites passages as ``[n]``. ``citations`` resolves the numbers
back to chunk and document ids, so the UI links them, and ``save``
appends the answer to a page as the agent, with the sources listed and
``annotates`` edges to the documents it rests on: an answer worth keeping
becomes part of a topic page, and the graph knows what it drew on.

With ``steps`` the model surfs before it answers (``prax.surf``): it
searches again, reads on, walks the graph and drops what is beside the
point, for as many steps and as much reading as asked, and the answer is
written from what it kept; the trail of steps comes back with the
answer and is kept with it on the page.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from prax import extraction, models, store

PASSAGES = 8  # documents per bundle; one passage each
PASSAGE_CHARS = 1200
FACTS_PER_DOC = 6
ANSWER_TOKENS = 700
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"

SYSTEM = """\
You answer questions from a personal research library. Use only the numbered
passages and the graph facts given below the question; you have no other
knowledge of these documents. After each claim, cite the passages that
support it in square brackets, like [2] or [1][3]; cite nothing else and
never invent a number. When the passages do not answer the question, say so
in one sentence and say what they do cover. Answer in plain Markdown under
250 words, without a heading, and name documents by their titles. When
earlier turns of the conversation are given, the question may refer to
them ("that method", "the second one"); answer the new question in their
light, but cite only the passages numbered below it, never an earlier
turn's."""

HISTORY_TURNS = 6  # what a follow-up carries along, at most
HISTORY_CHARS = 1500  # of each earlier answer
_FOLLOW_UP = re.compile(
    r"\b(it|its|that|this|these|those|they|them|their|the same|the other|"
    r"the second|the first|the latter|the former|he|she|his|her|also|too|"
    r"instead|why|how so|more)\b",
    re.IGNORECASE,
)


@dataclass
class Passage:
    n: int
    doc_id: int
    chunk_id: int | None
    title: str
    heading: list[str]
    page: int | None
    kind: str | None
    text: str
    figure: str | None = None  # a figure chunk's reference: the image beside it

    def label(self) -> str:
        bits = [self.title or "(untitled)"]
        if self.heading:
            bits.append(" › ".join(self.heading))
        if self.page:
            bits.append(f"p. {self.page}")
        s = " — ".join(bits)
        if self.kind and self.kind != "text":
            s += f" [{self.kind}]"
        return s

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "title": self.title,
            "heading": self.heading,
            "page": self.page,
            "kind": self.kind,
            "text": self.text,
            "figure": self.figure,
        }


@dataclass
class Bundle:
    question: str
    passages: list[Passage] = field(default_factory=list)
    facts: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)  # earlier turns

    def as_message(self) -> str:
        n = len(self.passages)
        parts: list[str] = []
        if self.history:
            parts.append("Earlier in this conversation:")
            for turn in self.history:
                q = " ".join(str(turn.get("question", "")).split())
                a = " ".join(str(turn.get("answer", "")).split())[:HISTORY_CHARS]
                parts += ["", f"Q: {q}", f"A: {a}"]
            parts.append("")
        contiguous = [p.n for p in self.passages] == list(range(1, n + 1))
        numbered = f"Passages (1 to {n}):" if contiguous else "Passages:"
        parts += [f"Question: {self.question.strip()}", "", numbered]
        for p in self.passages:
            parts += ["", f"[{p.n}] {p.label()}", p.text]
        lines = []
        for p in self.passages:
            facts = self.facts.get(p.doc_id) or []
            if not facts:
                continue
            by_rel: dict[str, list[str]] = {}
            for f in facts:
                by_rel.setdefault(f["rel"], []).append(f["name"])
            rels = "; ".join(f"{r}: {', '.join(n)}" for r, n in by_rel.items())
            lines.append(f"- [{p.n}] {p.title or '(untitled)'}: {rels}")
        if lines:
            parts += ["", "What the library's graph records about these documents:"]
            parts += lines
        return "\n".join(parts) + "\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "passages": [p.to_dict() for p in self.passages],
            "facts": {str(k): v for k, v in self.facts.items()},
            "turns_before": len(self.history),
        }


def clean_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """The last few turns, question and answer only, each a string."""
    turns = []
    for t in history or []:
        if not isinstance(t, dict):
            continue
        q = str(t.get("question") or "").strip()
        a = str(t.get("answer") or "").strip()
        if q and a:
            turns.append({"question": q, "answer": a})
    return turns[-HISTORY_TURNS:]


def search_query(question: str, history: list[dict[str, str]]) -> str:
    """What to search for: the question, and when it leans on the
    conversation — short, or pointing back with "it", "that", "the second
    one" — the previous question's words too, so the passages come from
    the same neighbourhood."""
    q = " ".join(question.split())
    if not history:
        return q
    if len(q.split()) <= 5 or _FOLLOW_UP.search(q):
        return f"{q} {history[-1]['question']}"
    return q


def gather(
    con: sqlite3.Connection,
    question: str,
    *,
    limit: int = PASSAGES,
    doctype: str | None = None,
    passage_chars: int = PASSAGE_CHARS,
    history: list[dict[str, str]] | None = None,
) -> Bundle:
    """The context for a question: one passage per document from the
    hybrid search (the matched chunk, or the document field for a hit that
    came from the field alone) and the graph facts about those documents.
    ``history`` (earlier turns) rides along for the model and widens the
    search when the question leans on it."""
    bundle = Bundle(question=question.strip(), history=list(history or []))
    if not bundle.question:
        return bundle
    # hybrid search fuses at document level; the FTS-only fallback does not,
    # so one passage per document is enforced here and the search over-fetches
    query = search_query(bundle.question, bundle.history)
    hits = store.search(con, query, limit * 3, doctype=doctype)
    seen: set[int] = set()
    for h in hits:
        if h["doc_id"] in seen or len(bundle.passages) >= limit:
            continue
        seen.add(h["doc_id"])
        n = len(bundle.passages) + 1
        chunk_id = h.get("chunk_id")
        text = ""
        if chunk_id is not None:
            chunk = store.get_chunk(con, chunk_id)
            text = chunk["text"] if chunk else ""
        if not text:
            text = store.document_field(con, h["doc_id"]) or h.get("snippet") or ""
        text = (
            " ".join(text.split()) if h.get("kind") not in ("code", "table") else text
        )
        bundle.passages.append(
            Passage(
                n=n,
                doc_id=h["doc_id"],
                chunk_id=chunk_id,
                title=h.get("title") or "",
                heading=list(h.get("heading") or []),
                page=h.get("page"),
                kind=h.get("kind"),
                text=text[:passage_chars],
                figure=h.get("figure"),
            )
        )
    ids = list(dict.fromkeys(p.doc_id for p in bundle.passages))
    bundle.facts = store.document_facts(con, ids, limit=FACTS_PER_DOC)
    return bundle


# ---------------------------------------------------------------- backends


STEP_TOKENS = 160  # a surfing step's two lines


class Answerer(Protocol):
    """A model that answers from a bundle and, surfing, takes a step: two
    lines under a grammar for a local model (``prax.surf``). ``reading``
    is its default and largest reading budget in tokens."""

    name: str
    reading: tuple[int, int]

    def answer(self, bundle: Bundle) -> tuple[str, dict[str, int]]: ...

    def step(
        self, system: str, user: str, *, grammar: str | None = None
    ) -> tuple[str, dict[str, int]]: ...


@dataclass
class LocalAnswerer:
    """A GGUF model in process; a little temperature keeps a 7B model from
    the loops greedy decoding falls into, the repeat penalty stays mild so
    citations can repeat."""

    runtime: extraction.Runtime
    max_tokens: int = ANSWER_TOKENS
    temperature: float = 0.2
    repeat_penalty: float = 1.05
    reading: tuple[int, int] = (4000, 4000)

    @property
    def name(self) -> str:
        return self.runtime.name

    def answer(self, bundle: Bundle) -> tuple[str, dict[str, int]]:
        try:
            return self._answer(bundle)
        except RuntimeError as exc:
            # the slot cannot hold what was read: a token is nearer three
            # characters than four in dense text, so a surf that spent its
            # whole reading budget can overrun the context the budget was
            # derived from. Every passage keeps its number and its head,
            # shorter, so the answer still cites what it rests on.
            fit = extraction._fits_after(str(exc))
            if fit is None:
                raise
            shorter = replace(
                bundle,
                passages=[
                    replace(p, text=p.text[: max(200, int(len(p.text) * fit))])
                    for p in bundle.passages
                ],
            )
            text, usage = self._answer(shorter)
            usage["cut"] = 1
            return text, usage

    def _answer(self, bundle: Bundle) -> tuple[str, dict[str, int]]:
        return self.runtime.chat(
            SYSTEM,
            bundle.as_message(),
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            repeat_penalty=self.repeat_penalty,
        )

    def step(
        self, system: str, user: str, *, grammar: str | None = None
    ) -> tuple[str, dict[str, int]]:
        return self.runtime.chat(
            system,
            user,
            grammar=grammar,
            max_tokens=STEP_TOKENS,
            temperature=self.temperature,
            repeat_penalty=self.repeat_penalty,
        )


@dataclass
class ClaudeAnswerer:
    model: str = DEFAULT_CLAUDE_MODEL
    effort: str = "low"
    client: Any = None
    reading: tuple[int, int] = (16_000, 60_000)

    @property
    def name(self) -> str:
        return self.model

    def _chat(
        self, system: str, user: str, max_tokens: int
    ) -> tuple[str, dict[str, int]]:
        if self.client is None:
            import anthropic

            self.client = anthropic.Anthropic(
                timeout=extraction.CALL_TIMEOUT, max_retries=3
            )
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if extraction.supports_effort(self.model):
            params["output_config"] = {"effort": self.effort}
        response = self.client.messages.create(**params)
        text = "".join(b.text for b in response.content if b.type == "text")
        u = response.usage
        usage = {
            "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
        }
        return text, usage

    def answer(self, bundle: Bundle) -> tuple[str, dict[str, int]]:
        return self._chat(SYSTEM, bundle.as_message(), 2 * ANSWER_TOKENS)

    def step(
        self, system: str, user: str, *, grammar: str | None = None
    ) -> tuple[str, dict[str, int]]:
        # Claude keeps to the two lines without a grammar; the reply is
        # parsed leniently and free text counts as being done
        return self._chat(system, user, STEP_TOKENS)


class StubAnswerer:
    """Tests: cites the first passage, names the question; surfing, plays
    the ``script`` of step replies given (an empty one answers at once)."""

    name = "stub"
    reading = (4000, 6000)

    def __init__(self, script: list[str] | None = None) -> None:
        self.script = list(script or [])
        self.steps: list[dict[str, Any]] = []

    def answer(self, bundle: Bundle) -> tuple[str, dict[str, int]]:
        cite = "[1]" if bundle.passages else ""
        return (
            f"Stub answer to '{bundle.question}' {cite}.".replace(" .", "."),
            {"input_tokens": len(bundle.as_message()) // 4, "output_tokens": 12},
        )

    def step(
        self, system: str, user: str, *, grammar: str | None = None
    ) -> tuple[str, dict[str, int]]:
        self.steps.append({"system": system, "user": user, "grammar": grammar})
        reply = self.script.pop(0) if self.script else "note: enough\nanswer"
        return reply, {"input_tokens": len(user) // 4, "output_tokens": 20}


def answerer_for(spec: models.ModelSpec | None) -> Answerer | None:
    """The answerer for a model spec, with the reading budget its context
    holds (``prax.surf.reading_bounds``); None for none."""
    from prax import surf

    if spec is None:
        return None
    if spec.kind == "stub":
        stub = StubAnswerer()
        stub.reading = surf.reading_bounds("stub", spec.n_ctx)
        return stub
    if spec.kind == "claude":
        assert spec.model is not None
        return ClaudeAnswerer(
            model=spec.model,
            effort=spec.effort or "low",
            reading=surf.reading_bounds("claude", spec.n_ctx),
        )
    return LocalAnswerer(
        models.runtime(spec), reading=surf.reading_bounds(spec.kind, spec.n_ctx)
    )


def answerer_named(name: str) -> Answerer | None:
    """The answerer for a model name (``prax.yaml`` or an implicit name);
    ``none`` is None; an unknown name is a ValueError."""
    if name in ("none", ""):
        return None
    spec = models.spec(name)
    if spec is None:
        raise ValueError(f"no model named {name!r}; known: {models.names()}")
    return answerer_for(spec)


def current() -> Answerer | None:
    """The ``ask`` step's answerer (``prax.yaml``, ``PRAX_ASK``)."""
    return answerer_for(models.resolve("ask"))


def default_steps() -> int:
    """How many surfing steps an ask takes when the request does not say:
    the host's ``steps.ask.steps`` (``prax.surf.STEPS`` otherwise)."""
    from prax import surf

    return max(
        0, min(int(models.settings("ask").get("steps", surf.STEPS)), surf.MAX_STEPS)
    )


def describe() -> dict[str, Any]:
    """What the door answers with and what it could use, for the UI, with
    the surfing budgets: the default number of steps and, per model, the
    default and largest reading budget in tokens."""
    from prax import surf

    d = models.describe("ask")
    opts = models.settings("ask")
    reading: dict[str, dict[str, int]] = {}
    for name in dict.fromkeys([*d["models"], d["model"]]):
        s = models.spec(name)
        if s is None:
            continue
        lo, hi = surf.reading_bounds(s.kind, s.n_ctx)
        reading[name] = {"default": min(hi, int(opts.get("tokens", lo))), "max": hi}
    return {
        "default": d["model"],
        "runtime": d["runtime"],
        "models": d["models"],
        "error": d["error"],
        "steps": {"default": default_steps(), "max": surf.MAX_STEPS},
        "reading": reading,
    }


# ------------------------------------------------------------------- ask

_CITE = re.compile(r"\[(\d+)\]")


def citations(text: str, bundle: Bundle) -> list[dict[str, Any]]:
    """The passages an answer cites, in order of first mention."""
    by_n = {p.n: p for p in bundle.passages}
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for m in _CITE.finditer(text):
        n = int(m.group(1))
        if n in by_n and n not in seen:
            seen.add(n)
            p = by_n[n]
            out.append(
                {
                    "n": n,
                    "doc_id": p.doc_id,
                    "chunk_id": p.chunk_id,
                    "title": p.title,
                    "heading": p.heading,
                    "page": p.page,
                }
            )
    return out


def ask(
    con: sqlite3.Connection,
    question: str,
    *,
    limit: int = PASSAGES,
    doctype: str | None = None,
    answerer: Answerer | None = None,
    history: list[dict[str, Any]] | None = None,
    steps: int = 0,
    tokens: int | None = None,
    on_event: Any = None,
    stop: Any = None,
) -> dict[str, Any]:
    """Gather, then answer with ``answerer`` (None: the bundle alone, the
    caller's model answers). ``history`` is the conversation so far, as
    the client kept it: the model sees the earlier turns, the search
    widens for a follow-up, and the answer cites only this turn's
    passages. With ``steps`` and an answerer the model surfs first
    (``prax.surf.run``: that many steps, ``tokens`` of reading, each step
    an ``on_event``, ``stop`` an event that ends the surf early). The
    store is only held while gathering; the model runs outside the
    lock."""
    if steps > 0 and answerer is not None:
        from prax import surf

        steps, tokens = surf.clamp(answerer, steps, tokens)
        return surf.run(
            con,
            question,
            answerer=answerer,
            steps=steps,
            tokens=tokens,
            limit=limit,
            doctype=doctype,
            history=clean_history(history),
            on_event=on_event,
            stop=stop,
        )
    bundle = gather(
        con, question, limit=limit, doctype=doctype, history=clean_history(history)
    )
    out = bundle.to_dict()
    out.update(answer=None, model=None, citations=[], usage={}, seconds=0.0)
    if answerer is None or not bundle.passages:
        return out
    t0 = time.monotonic()
    text, usage = answerer.answer(bundle)
    out.update(
        answer=text.strip(),
        model=answerer.name,
        citations=citations(text, bundle),
        usage=usage,
        seconds=round(time.monotonic() - t0, 1),
        cost_usd=round(extraction.cost_usd(answerer.name, usage), 5),
    )
    return out


def trail_lines(result: dict[str, Any]) -> list[str]:
    """A surfing result's steps as short lines, for the page an answer is
    kept on (the door's own first search left out)."""
    out = []
    for st in result.get("trail") or []:
        if not isinstance(st, dict) or not st.get("n"):
            continue
        action = str(st.get("action") or "")
        line = f"{action} {st.get('arg') or ''}".strip()
        if action == "answer":
            line = "enough read"
        if st.get("note"):
            line += f" — {st['note']}"
        out.append(line)
    return out


def save(
    con: sqlite3.Connection,
    result: dict[str, Any],
    slug: str,
    *,
    heading: str | None = None,
    create: str | None = None,
) -> dict[str, Any]:
    """Append an answer to a page as the agent: the question as heading,
    the answer, a source list linking the cited documents (all passages
    when nothing was cited), and ``annotates`` edges to those documents
    (``synthesizes`` on a synthesis page). ``create`` names a page kind to
    create when no page has the slug: a new synthesis page is an answer
    kept as the seed of a write-up across its sources."""
    answer = (result.get("answer") or "").strip()
    if not answer:
        raise ValueError("nothing to save: the result has no answer")
    cited = result.get("citations") or []
    if not cited:
        cited = [
            {
                k: p.get(k)
                for k in ("n", "doc_id", "chunk_id", "title", "heading", "page")
            }
            for p in result.get("passages") or []
        ]
    lines = []
    for c in cited:
        where = " › ".join(c.get("heading") or [])
        if c.get("page"):
            where = f"{where}, p. {c['page']}" if where else f"p. {c['page']}"
        title = c.get("title") or f"document {c['doc_id']}"
        lines.append(
            f"- [{c['n']}] [{title}](#doc/{c['doc_id']})"
            + (f" — {where}" if where else "")
        )
    section = answer + "\n\nSources:\n\n" + "\n".join(lines) if lines else answer
    trail = trail_lines(result)
    if trail:
        section += "\n\nHow it was found:\n\n" + "\n".join(f"- {t}" for t in trail)
    model = result.get("model") or "?"
    docs = list(dict.fromkeys(c["doc_id"] for c in cited if c.get("title")))
    if create and store.get_page(con, store.slugify(slug)) is None:
        title = heading or result.get("question") or slug
        text = f"# {title}\n\n{section}\n"
        return store.write_page(
            con,
            slug,
            text,
            title=title,
            kind=create,
            author="agent",
            note=f"ask: {model}",
            annotates=docs,
        )
    return store.append_page(
        con,
        slug,
        section,
        heading=heading or result.get("question"),
        author="agent",
        note=f"ask: {model}",
        annotates=docs,
    )
