"""Ask: a question answered from the library, with citations.

Three steps, the first two the store's. ``gather`` runs the hybrid search,
takes the best chunk of each of the top documents as a numbered passage,
and adds what the graph records about those documents (their extracted
relations, canonical names), everything bounded in characters: the
bundle is the whole context a model gets, and it fits a 7B model with an
8 K window. ``answer`` hands the bundle to a generation backend, chosen
per host with ``PRAX_ASK``: ``local`` runs a GGUF model in process through
``prax.local_llm`` (the desktop with the GPU; ``PRAX_LOCAL_MODEL``),
``claude`` calls the API (``PRAX_ASK_MODEL``), ``none`` returns the bundle
alone, for a client that is itself a model (Claude Code over MCP) and for
the Pi-class serving host, which loads no model (invariant 7). The
default is ``local`` when a local model is configured, else ``none``.

The answer cites passages as ``[n]``. ``citations`` resolves the numbers
back to chunk and document ids, so the UI links them, and ``save``
appends the answer to a page as the agent, with the sources listed and
``annotates`` edges to the documents it rests on: an answer worth keeping
becomes part of a topic page, and the graph knows what it drew on.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from prax import extraction, local_llm, store

PASSAGES = 8  # documents per bundle; one passage each
PASSAGE_CHARS = 1200
FACTS_PER_DOC = 6
ANSWER_TOKENS = 700
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"
BACKENDS = ("local", "claude", "none", "stub")

SYSTEM = """\
You answer questions from a personal research library. Use only the numbered
passages and the graph facts given below the question; you have no other
knowledge of these documents. After each claim, cite the passages that
support it in square brackets, like [2] or [1][3]; cite nothing else and
never invent a number. When the passages do not answer the question, say so
in one sentence and say what they do cover. Answer in plain Markdown under
250 words, without a heading, and name documents by their titles."""


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
        }


@dataclass
class Bundle:
    question: str
    passages: list[Passage] = field(default_factory=list)
    facts: dict[int, list[dict[str, Any]]] = field(default_factory=dict)

    def as_message(self) -> str:
        n = len(self.passages)
        parts = [f"Question: {self.question.strip()}", "", f"Passages (1 to {n}):"]
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
        }


def gather(
    con: sqlite3.Connection,
    question: str,
    *,
    limit: int = PASSAGES,
    doctype: str | None = None,
    passage_chars: int = PASSAGE_CHARS,
) -> Bundle:
    """The context for a question: one passage per document from the
    hybrid search (the matched chunk, or the document field for a hit that
    came from the field alone) and the graph facts about those documents."""
    bundle = Bundle(question=question.strip())
    if not bundle.question:
        return bundle
    # hybrid search fuses at document level; the FTS-only fallback does not,
    # so one passage per document is enforced here and the search over-fetches
    hits = store.search(con, bundle.question, limit * 3, doctype=doctype)
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
            )
        )
    ids = list(dict.fromkeys(p.doc_id for p in bundle.passages))
    bundle.facts = store.document_facts(con, ids, limit=FACTS_PER_DOC)
    return bundle


# ---------------------------------------------------------------- backends


class Answerer(Protocol):
    name: str

    def answer(self, bundle: Bundle) -> tuple[str, dict[str, int]]: ...


@dataclass
class LocalAnswerer:
    """A GGUF model in process; a little temperature keeps a 7B model from
    the loops greedy decoding falls into, the repeat penalty stays mild so
    citations can repeat."""

    runtime: extraction.Runtime
    max_tokens: int = ANSWER_TOKENS
    temperature: float = 0.2
    repeat_penalty: float = 1.05

    @property
    def name(self) -> str:
        return self.runtime.name

    def answer(self, bundle: Bundle) -> tuple[str, dict[str, int]]:
        return self.runtime.chat(
            SYSTEM,
            bundle.as_message(),
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            repeat_penalty=self.repeat_penalty,
        )


@dataclass
class ClaudeAnswerer:
    model: str = DEFAULT_CLAUDE_MODEL
    effort: str = "low"
    client: Any = None

    @property
    def name(self) -> str:
        return self.model

    def answer(self, bundle: Bundle) -> tuple[str, dict[str, int]]:
        if self.client is None:
            import anthropic

            self.client = anthropic.Anthropic(
                timeout=extraction.CALL_TIMEOUT, max_retries=3
            )
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 2 * ANSWER_TOKENS,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": bundle.as_message()}],
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


class StubAnswerer:
    """Tests: cites the first passage, names the question."""

    name = "stub"

    def answer(self, bundle: Bundle) -> tuple[str, dict[str, int]]:
        cite = "[1]" if bundle.passages else ""
        return (
            f"Stub answer to '{bundle.question}' {cite}.".replace(" .", "."),
            {"input_tokens": len(bundle.as_message()) // 4, "output_tokens": 12},
        )


def answerer_named(backend: str) -> Answerer | None:
    """The backend for a name; ``none`` is None. ``local`` needs
    ``PRAX_LOCAL_MODEL`` (RuntimeError otherwise)."""
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}")
    if backend == "none":
        return None
    if backend == "stub":
        return StubAnswerer()
    if backend == "claude":
        return ClaudeAnswerer(
            model=os.environ.get("PRAX_ASK_MODEL", DEFAULT_CLAUDE_MODEL)
        )
    path = os.environ.get("PRAX_LOCAL_MODEL")
    if not path:
        raise RuntimeError("the local backend needs PRAX_LOCAL_MODEL=<model.gguf>")
    ctx = int(os.environ.get("PRAX_LOCAL_CTX", local_llm.DEFAULT_CTX))
    return LocalAnswerer(local_llm.shared_runtime(path, n_ctx=ctx))


def default_backend() -> str:
    setting = os.environ.get("PRAX_ASK")
    if setting:
        return setting
    return "local" if os.environ.get("PRAX_LOCAL_MODEL") else "none"


def current() -> Answerer | None:
    return answerer_named(default_backend())


def describe() -> dict[str, Any]:
    """What the door would answer with: for the UI's backend choice."""
    backend = default_backend()
    local = os.environ.get("PRAX_LOCAL_MODEL")
    return {
        "default": backend,
        "local_model": local_llm.LlamaRuntime(local).name if local else None,
        "claude_model": os.environ.get("PRAX_ASK_MODEL", DEFAULT_CLAUDE_MODEL),
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
) -> dict[str, Any]:
    """Gather, then answer with ``answerer`` (None: the bundle alone, the
    caller's model answers). The store is only held while gathering; the
    model runs outside the lock."""
    bundle = gather(con, question, limit=limit, doctype=doctype)
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
