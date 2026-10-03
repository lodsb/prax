"""Surf: ask as a loop, the model using the library instead of being handed
one bundle.

The one-shot ask (``prax.answering.ask``) gives a model the best passage of each of
the top documents and the model can only write around what it was given:
an off-topic hit stays in the prompt, the page that answers is never seen
when another page of the same paper matched, and a thread in the graph
worth pulling is out of reach. Surfing gives the model the library for a
few steps: it searches again with better words, reads on where a passage
stopped, asks the graph what a document is connected to and walks from an
entity to the documents behind it, sets aside a hit that is beside the
point, and then the answer is written from what it kept. The steps are
the door's own reads (``store.search``, ``read_chunks``,
``document_facts``, ``traverse``, ``similar_documents``); nothing is
written. A figure the vision model has read is text like any other — its
description is its chunk's text — so it is searched, read and cited like
a paragraph; a figure nobody has read is an image line and a caption,
and stays out of the way.

A step is two lines, a note and an action, and a grammar holds a local
model to them (``grammar``): the passage numbers and document ids a step
may name are the ones the model has seen, so it can only point at what
exists. The prompt is one growing message — the question, then every
step and what it returned, in order — so a llama-server reuses its cache
of the prefix and a step costs its own tokens only. Two budgets bound the
loop, ``steps`` (0 is the one-shot ask) and ``tokens`` of reading, both
clamped to what the model's context holds (``clamp``). The answer is a
separate call with the ask prompt over the passages kept, under their
loop numbers, so a citation points at what was read. Every step is an
event (``on_event``) for a client that shows the trail as it happens, and
the trail comes back with the result.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from prax import config, packs, store
from prax.answering import ask

STEPS = 8  # the default number of steps; 0 is the one-shot ask
MAX_STEPS = 20
HIT_CHARS = 500  # a search hit's passage in the loop: enough to judge it
READ_CHARS = 1500  # what one "read" step returns
FACTS = 20  # graph facts per "facts" step
HIT_FACTS = 4  # graph facts shown with a search hit
WALK_EDGES = 25
# a walk's argument may say which thing it means: "apple (ingredient)"
_TYPED = re.compile(r"^(.+?)\s*\(([a-z_]+)\)$")
SECTIONS = 12  # sections named when a reading found nothing
SIMILAR = 6
NOTE_CHARS = 200
QUERY_CHARS = 100
TOOL_CHARS = 400  # a pack tool's argument: a derivation runs longer than a query
LOOK_CHARS = 60  # the words a read looks for inside a document
OVERHEAD_TOKENS = 2200  # the system prompt, the question, the step lines, the answer
MIN_TOKENS = 1000
LOCAL_TOKENS = 4000  # the default reading budget of a local model
CLAUDE_TOKENS = (16_000, 60_000)  # Claude's default and ceiling (cost, not context)

Event = Callable[[dict[str, Any]], None]

SYSTEM = """\
You are looking things up in a personal research library to answer a
question. You work in steps. Each step, write exactly two lines: a note
(one sentence: what the last result told you, or what you are after) and
one action from this list:

search: <words>     search the library again with better words
read: [n]           read on where passage n stopped
read: [n] <words>   the part of that passage's document about those words
read: [n] (2)       equation (2) of that passage's document, by its number
read: doc <id>      read a document a result named, from its start
read: doc <id> <words>   the part of that document about those words
facts: [n]          what the graph records about passage n's document
walk: <entity>      the graph around an entity: its relations, the documents behind
similar: [n]        documents like passage n's
drop: [n] [m]       set aside passages that do not bear on the question
answer              stop looking; the answer is written from the passages kept

Angle brackets above mark what you replace: never write a line that
still contains them. Every passage is numbered [n] and stays in the log;
the answer is written from the ones you keep. Read on when a passage
stops short of the point, search again when the hits miss the question,
walk the graph when the question is about how things relate, and drop
hits that are about something else. A document a walk or a similar
named is long and starts with a title page: read it with the words you
are after. A formula passage names the equations next to it by number;
when the question asks for an equation the passage only introduces,
read the one it names. Say "answer" as
soon as the passages kept answer the question; the steps and the reading
left are shown after each result.
Write nothing but the two lines."""


def reading_bounds(kind: str | None, n_ctx: int) -> tuple[int, int]:
    """A model's default and largest reading budget in tokens: what its
    context holds beyond the prompt's overhead for a local model, a cost
    ceiling for Claude."""
    if kind == "claude":
        return CLAUDE_TOKENS
    top = max(MIN_TOKENS, n_ctx - OVERHEAD_TOKENS)
    return min(LOCAL_TOKENS, top), top


def clamp(answerer: Any, steps: int | None, tokens: int | None) -> tuple[int, int]:
    """The budgets a request asked for, within what the answerer holds:
    ``steps`` in 0..MAX_STEPS (None: the host's ``steps.ask.steps``),
    ``tokens`` of reading in MIN_TOKENS..the answerer's ceiling (None: its
    default, or the host's ``steps.ask.tokens``)."""
    from prax import models

    lo, hi = getattr(answerer, "reading", (LOCAL_TOKENS, LOCAL_TOKENS))
    opts = models.settings("ask")
    if steps is None:
        steps = int(opts.get("steps", STEPS))
    if tokens is None:
        tokens = int(opts.get("tokens", lo))
    return max(0, min(int(steps), MAX_STEPS)), max(MIN_TOKENS, min(int(tokens), hi))


# ------------------------------------------------------------------ state


@dataclass
class Step:
    n: int
    action: str
    arg: str = ""
    note: str = ""
    result: str = ""  # one line for the trail
    added: list[int] = field(default_factory=list)  # passages it brought
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "action": self.action,
            "arg": self.arg,
            "note": self.note,
            "result": self.result,
            "added": list(self.added),
            "seconds": self.seconds,
        }


@dataclass
class Surf:
    """The working state: the passages read (numbered as they came), the
    documents a result named (what a later step may point at), the log
    the model sees, and what the budgets have left."""

    question: str
    query: str  # what the first search used
    history: list[dict[str, str]]
    doctype: str | None
    limit: int  # hits per search
    steps_left: int
    tokens_left: int
    note: str = ""  # beside the question, for the model only
    tools: bool = True  # the packs' tools this host runs (False: without them)
    mode: str = "grounded"  # or open: the model's own knowledge beside
    passages: list[ask.Passage] = field(default_factory=list)
    seq: dict[int, int] = field(default_factory=dict)  # passage n -> where to read on
    dropped: set[int] = field(default_factory=set)  # passage numbers
    docs: dict[int, str] = field(default_factory=dict)  # id -> title, named so far
    chunks: set[int] = field(default_factory=set)  # chunk ids shown already
    barren: dict[tuple[str, str], int] = field(default_factory=dict)  # what led nowhere
    log: list[str] = field(default_factory=list)
    worked: list[str] = field(default_factory=list)  # a pack tool's steps and answers
    steps: list[Step] = field(default_factory=list)
    usage: dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0}
    )

    @property
    def kept(self) -> list[ask.Passage]:
        return [p for p in self.passages if p.n not in self.dropped]

    def add_usage(self, usage: dict[str, Any]) -> None:
        """A call's token counts added to the surf's."""
        for k, v in usage.items():
            self.usage[k] = self.usage.get(k, 0) + int(v)

    def live(self) -> list[int]:
        return [p.n for p in self.kept]

    def by_n(self, arg: str) -> ask.Passage | None:
        m = re.fullmatch(r"\[(\d+)\]", arg.strip())
        if not m:
            return None
        return next((p for p in self.passages if p.n == int(m.group(1))), None)

    def message(self) -> str:
        parts = [f"Question: {self.question}"]
        if self.note:
            parts.append(f"Note: {self.note}")
        if self.history:
            parts += ["", "Earlier in this conversation:"]
            for turn in self.history:
                q = " ".join(turn["question"].split())
                a = " ".join(turn["answer"].split())[: ask.HISTORY_CHARS]
                parts += [f"Q: {q}", f"A: {a}"]
        parts.append("")
        parts += self.log
        parts.append(
            f"Now step {len(self.steps)}: {self.steps_left} steps and about"
            f" {self.tokens_left:,} tokens of reading left."
            + (" This is the last step." if self.steps_left == 1 else "")
        )
        return "\n".join(parts) + "\n"

    def spend(self, text: str) -> str:
        """Charge a result against the reading budget; cut it to what is
        left so the prompt never outgrows the context."""
        room = max(0, self.tokens_left * 4)
        if len(text) > room:
            text = text[:room].rstrip() + " …"
        self.tokens_left = max(0, self.tokens_left - len(text) // 4)
        return text

    def add(
        self, doc_id: int, chunk: dict[str, Any], text: str, title: str
    ) -> ask.Passage:
        p = ask.Passage(
            n=len(self.passages) + 1,
            doc_id=doc_id,
            chunk_id=chunk.get("chunk_id"),
            title=title,
            heading=list(chunk.get("heading") or []),
            page=chunk.get("page"),
            kind=chunk.get("kind"),
            text=text,
            figure=chunk.get("figure"),
            nearby=chunk.get("nearby"),
            published=chunk.get("published"),
        )
        self.passages.append(p)
        if chunk.get("seq") is not None:
            self.seq[p.n] = int(chunk["seq"])
        if p.chunk_id is not None:
            self.chunks.add(p.chunk_id)
        self.docs.setdefault(doc_id, title)
        return p


def _block(p: ask.Passage, tag: str = "") -> str:
    where = " › ".join(p.heading) if p.heading else ""
    if p.page:
        where = f"{where}, p. {p.page}" if where else f"p. {p.page}"
    head = f"[{p.n}] doc {p.doc_id}: {p.title or '(untitled)'}"
    if where:
        head += f" — {where}"
    if p.kind and p.kind != "text":
        head += f" [{p.kind}]"
    if tag:
        head += f" ({tag})"
    block = f"{head}\n{p.text}"
    if p.nearby:
        block += "\n" + p.nearby_line()
    return block


def _flat(text: str, kind: str | None) -> str:
    return text if kind in ("code", "table") else " ".join(text.split())


# ------------------------------------------------------------------ tools
# Each returns what the model is shown and the numbers of the passages it
# brought; the reads go through the store's read functions only.


def do_search(con: sqlite3.Connection, s: Surf, query: str) -> tuple[str, list[int]]:
    """Hybrid hits as new passages, one per document, skipping documents
    set aside and chunks already shown."""
    query = " ".join(query.split())
    if not query:
        return "search needs words", []
    hits = ask.evidence(con, store.search(con, query, s.limit * 3, doctype=s.doctype))
    dropped_docs = {p.doc_id for p in s.passages if p.n in s.dropped}
    blocks: list[str] = []
    added: list[int] = []
    seen: set[int] = set()
    for h in hits:
        did = h["doc_id"]
        if did in seen or did in dropped_docs or len(added) >= s.limit:
            continue
        chunk_id = h.get("chunk_id")
        if chunk_id is not None and chunk_id in s.chunks:
            continue
        seen.add(did)
        text = ""
        chunk: dict[str, Any] = {
            "chunk_id": chunk_id,
            "heading": h.get("heading"),
            "page": h.get("page"),
            "kind": h.get("kind"),
            "figure": h.get("figure"),
            "published": h.get("published"),
        }
        if chunk_id is not None:
            c = store.get_chunk(con, chunk_id)
            if c:
                text = c["text"]
                chunk["seq"] = c["seq"]
                chunk["nearby"] = ask.nearby_of(con, c["kind"], chunk_id)
        if not text:
            text = store.document_field(con, did) or h.get("snippet") or ""
        text = _flat(text, h.get("kind"))[:HIT_CHARS]
        p = s.add(did, chunk, text, h.get("title") or "")
        added.append(p.n)
        blocks.append(_block(p))
    if not blocks:
        return "no new hits", []
    # what the graph records about each hit's document rides along, as in
    # the one-shot bundle: the entity names are what a walk starts from
    facts = store.document_facts(con, list(seen), limit=HIT_FACTS)
    by_n = {p.n: p for p in s.passages}
    for i, n in enumerate(added):
        line = _facts_line(facts.get(by_n[n].doc_id) or [])
        if line:
            blocks[i] += f"\ngraph: {line}"
    return "\n\n".join(blocks), added


def _facts_line(facts: list[dict[str, Any]]) -> str:
    by_rel: dict[str, list[str]] = {}
    for f in facts:
        by_rel.setdefault(f["rel"], []).append(f["name"])
    return "; ".join(f"{r}: {', '.join(names)}" for r, names in by_rel.items())


_READ_ARG = re.compile(r"(?:\[(\d+)\]|doc\s+(\d+))(?:\s+(\S.*))?")
# "(2)", "eq. 2", "equation (2b)": the number a paper calls an equation by
_EQ_REF = re.compile(r"(?:eq(?:uation)?\.?\s*)?\(?(\d+[a-z]?)\)?", re.IGNORECASE)


def do_read(con: sqlite3.Connection, s: Surf, arg: str) -> tuple[str, list[int]]:
    """More of a document: what follows passage n, a document a result
    named, from its start — or, with words after either, the part of that
    document which holds them (``store.find_chunk``), which is the only
    way into a long paper the graph pointed at: its start is a title
    page. The chunks read become one passage placed at the first of them,
    so a citation points at where the reading began, and reading on
    continues after the last."""
    m = _READ_ARG.fullmatch(" ".join(arg.split()))
    if not m:
        return "read takes [n] or doc <id>, either with words to look for", []
    words = m.group(3) or ""
    if m.group(1):
        p = next((x for x in s.passages if x.n == int(m.group(1))), None)
        if p is None:
            return f"there is no passage [{m.group(1)}]", []
        # a document-field hit has no chunk: reading it starts at the top
        doc_id, title, after, tag = (
            p.doc_id,
            p.title,
            s.seq.get(p.n, -1),
            f"after [{p.n}]",
        )
    else:
        doc_id = int(m.group(2))
        if doc_id not in s.docs:
            titles = store.document_titles(con, [doc_id])
            if doc_id not in titles:
                return f"no document {doc_id}", []
            s.docs[doc_id] = titles[doc_id]
        title, after, tag = s.docs[doc_id], -1, "start"
    if words:
        by_number = _EQ_REF.fullmatch(words.strip())
        found = (
            store.formula_by_number(con, doc_id, by_number.group(1))
            if by_number
            else None
        )
        if by_number and found is None:
            missing = f"doc {doc_id} has no equation ({by_number.group(1)})"
            return missing + _equations(con, doc_id), []
        if found is None:
            found = store.find_chunk(con, doc_id, words[:LOOK_CHARS])
        if found is None:
            return f"doc {doc_id} has no text to read", []
        # land on that part, or go on from it when it has been read already
        seen = found["chunk_id"] in s.chunks
        after = found["seq"] if seen else found["seq"] - 1
        tag = f"{'after' if seen else 'on'} {words!r}"
    chunks = store.read_chunks(
        con, doc_id, after_seq=after, max_chars=READ_CHARS, skip=s.chunks
    )
    if not chunks and after > -1:
        # the end of the document, or a part read already: go back for
        # whatever of it is still unread, so a reading always moves on
        # (a small model asked the same spent read five times running)
        chunks = store.read_chunks(
            con, doc_id, after_seq=-1, max_chars=READ_CHARS, skip=s.chunks
        )
        tag = "the next part unread"
    if not chunks:
        where = f"[{m.group(1)}]" if m.group(1) else f"doc {doc_id}"
        return f"all of {where} has been read{_sections(con, doc_id)}", []
    return _read(s, doc_id, title, chunks, tag, con)


# "equation (5)", "eq. (14)", "equations (3) and (4)": an equation the
# question names by its number (AD2, step 5)
_NAMED_EQ = re.compile(r"\beq(?:uations?|s?\.)\s*\(\s*(\d+[a-z]?)\s*\)", re.IGNORECASE)
_MORE_EQ = re.compile(r"^\s*(?:,|and|or)\s*\(\s*(\d+[a-z]?)\s*\)", re.IGNORECASE)
NAMED_MAX = 4  # equations a question may bring in this way
NAMED_DOCS = 8  # the first search's documents an equation is looked for in


def named_numbers(question: str) -> list[str]:
    """The equation numbers a question names, in order, once each."""
    out: list[str] = []
    for m in _NAMED_EQ.finditer(question):
        out.append(m.group(1))
        rest = question[m.end() :]
        while (more := _MORE_EQ.match(rest)) is not None:
            out.append(more.group(1))
            rest = rest[more.end() :]
    return list(dict.fromkeys(out))[:NAMED_MAX]


def named_equations(
    con: sqlite3.Connection, s: Surf
) -> list[tuple[str, tuple[str, list[int]]]]:
    """The formulas a question names by number ("equation (5)"), each from
    the first of the first search's documents that has one by that number,
    read as passages before the model's first step. The resonator question
    failed in all 16 answers of two double runs because no step ever held
    Dattorro's (5); the model did not use ``read: [n] (5)``."""
    numbers = named_numbers(s.question)
    if not numbers:
        return []
    # one document for all the numbers a question names: every paper has an
    # equation (1), so the first that holds one is often another paper
    # (Helmholtz's (1) for Dattorro's, 2026-10-02). The search's earliest
    # document with the most of them; the wall has filtered these already
    best: tuple[int, dict[str, dict[str, Any]]] | None = None
    for doc_id in list(dict.fromkeys(p.doc_id for p in s.passages))[:NAMED_DOCS]:
        has = {n: f for n in numbers if (f := store.formula_by_number(con, doc_id, n))}
        if has and (best is None or len(has) > len(best[1])):
            best = (doc_id, has)
    if best is None:
        return []
    doc_id, has = best
    title = s.docs.get(doc_id) or next(
        (p.title for p in s.passages if p.doc_id == doc_id), ""
    )
    return [
        (n, _read(s, doc_id, title, [f], f"({n})", con))
        for n, f in has.items()
        if f["chunk_id"] not in s.chunks
    ]


def _equations(con: sqlite3.Connection, doc_id: int) -> str:
    """The numbers a document's equations go by, for a read that asked
    for one it has not got."""
    numbers = [
        str(c["data"].get("number"))
        for c in store.list_chunks(con, doc_id)
        if c.get("kind") == "formula" and c.get("data") and c["data"].get("number")
    ]
    if not numbers:
        return ""
    shown = ", ".join(f"({n})" for n in numbers[:SECTIONS])
    return f". Its numbered equations: {shown}" + (
        " …" if len(numbers) > SECTIONS else ""
    )


def _sections(con: sqlite3.Connection, doc_id: int) -> str:
    """What a document is made of, for a reading that found nothing: what
    was in it, rather than another guess at the same page."""
    heads = store.document_outline(con, doc_id, limit=SECTIONS)
    if not heads:
        return ""
    return f". Its sections: {'; '.join(heads)}"


def _read(
    s: Surf,
    doc_id: int,
    title: str,
    chunks: list[dict[str, Any]],
    tag: str,
    con: sqlite3.Connection | None = None,
) -> tuple[str, list[int]]:
    text = "\n\n".join(_flat(c["text"], c["kind"]) for c in chunks)
    first = dict(chunks[0])
    first["seq"] = chunks[-1]["seq"]
    if con is not None:
        first["nearby"] = ask.nearby_of(con, first.get("kind"), first.get("chunk_id"))
        first["published"] = store.published_dates(con, [doc_id]).get(doc_id)
    for c in chunks:
        s.chunks.add(c["chunk_id"])
    p = s.add(doc_id, first, text[: READ_CHARS + 200], title)
    return _block(p, tag), [p.n]


def do_facts(con: sqlite3.Connection, s: Surf, arg: str) -> str:
    p = s.by_n(arg)
    if p is None:
        return "facts takes a passage number [n]"
    facts = store.document_facts(con, [p.doc_id], limit=FACTS).get(p.doc_id) or []
    if not facts:
        return f"the graph records nothing about doc {p.doc_id}"
    return f"doc {p.doc_id} ({p.title or 'untitled'}) — {_facts_line(facts)}"


def do_walk(con: sqlite3.Connection, s: Surf, name: str) -> str:
    """One hop from an entity: its edges by relation, each with the
    document it came from, and those documents named so a later step may
    read one. An unknown name gets the names like it."""
    name = " ".join(name.split())
    if not name:
        return "walk takes an entity's name"
    # "apple (ingredient)": a name is several things, and the walk says
    # which it took and what else there is
    etype = None
    typed = _TYPED.match(name)
    if typed:
        name, etype = typed.group(1).strip(), typed.group(2).strip()
    found = store.traverse_map(con, name, 1, WALK_EDGES, type=etype)
    edges = found["edges"]
    others = [s for s in found.get("senses") or [] if not s.get("walked")]
    if not edges and others:
        return f"no {etype} called {name!r}; it names " + "; ".join(
            f"walk: {name} ({x['type']})" for x in others
        )
    if not edges:
        like = store.find_entities(con, name, limit=6)
        if not like:
            return f"no entity named {name!r} in the graph"
        names = ", ".join(
            f"{e['name']} ({e['type']}, {e['degree']} edges)" for e in like
        )
        return f"no entity named {name!r}; names like it: {names}"
    titles = store.document_titles(
        con, [e["source_doc"] for e in edges if e.get("source_doc")]
    )
    for i, t in titles.items():
        s.docs.setdefault(i, t)
    by_rel: dict[str, list[str]] = {}
    said: set[tuple[str, str]] = set()
    for e in edges:
        outward = e["src"].lower() == name.lower()
        item = ("→ " if outward else "← ") + (e["dst"] if outward else e["src"])
        if (e["rel"], item) in said:
            continue  # a fact once, however many documents state it
        said.add((e["rel"], item))
        more = int(e.get("support") or 1) - 1
        if e.get("source_doc"):
            item += f" (doc {e['source_doc']}" + (f" +{more} more)" if more else ")")
        by_rel.setdefault(e["rel"], []).append(item)
    lines = [f"{name}:"]
    for rel, items in by_rel.items():
        lines.append(f"- {rel}: " + "; ".join(dict.fromkeys(items)))
    if others:
        lines.append(
            f"{name!r} names other things too: "
            + "; ".join(
                f"walk: {name} ({x['type']}), {x['documents']} documents"
                for x in others
            )
        )
    if titles:
        lines.append(
            "documents behind these: "
            + "; ".join(f"doc {i}: {t or '(untitled)'}" for i, t in titles.items())
        )
    return "\n".join(lines)


def do_similar(con: sqlite3.Connection, s: Surf, arg: str) -> str:
    p = s.by_n(arg)
    if p is None:
        return "similar takes a passage number [n]"
    near = [
        d
        for d in store.similar_documents(con, p.doc_id, limit=SIMILAR + 1)
        if d["doc_id"] != p.doc_id
    ][:SIMILAR]
    if not near:
        return f"no documents near [{p.n}] (no vectors yet?)"
    for d in near:
        s.docs.setdefault(d["doc_id"], d["title"] or "")
    return f"documents like [{p.n}]: " + "; ".join(
        f"doc {d['doc_id']}: {d['title'] or '(untitled)'}" for d in near
    )


def do_drop(s: Surf, arg: str) -> str:
    live = set(s.live())
    ns = [int(x) for x in re.findall(r"\[(\d+)\]", arg) if int(x) in live]
    if not ns:
        return "drop takes the numbers [n] of passages kept"
    s.dropped.update(ns)
    return "set aside " + " ".join(f"[{n}]" for n in ns)


# --------------------------------------------------------------- protocol


def grammar(s: Surf) -> str:
    """GBNF for the next step: a note line and one action, with the
    passage numbers and document ids as literal alternatives of what has
    been seen, so a step can only point at what exists."""
    live = s.live()
    pn = " | ".join(f'"[{n}]"' for n in live) or '"[0]"'
    docs = " | ".join(f'"{i}"' for i in sorted(s.docs)) or '"0"'
    tools = PACK_TOOLS if s.tools else ()
    actions = ["search", "walk", "done", *tools]
    if live:
        actions += ["read", "facts", "similar", "drop"]
    elif s.docs:
        actions.append("read")
    where = '( pn | "doc " did )' if live else '"doc " did'
    return "\n".join(
        [
            "root ::= note action",
            f'note ::= "note: " char{{1,{NOTE_CHARS}}} "\\n"',
            "action ::= " + " | ".join(actions),
            f'search ::= "search: " char{{2,{QUERY_CHARS}}} "\\n"',
            *(
                PACK_GRAMMAR.get(t) or f'{t} ::= "{t}: " char{{2,{TOOL_CHARS}}} "\\n"'
                for t in tools
            ),
            f'read ::= "read: " {where} look? "\\n"',
            f'look ::= " " char{{2,{LOOK_CHARS}}}',
            'facts ::= "facts: " pn "\\n"',
            'walk ::= "walk: " char{1,80} "\\n"',
            'similar ::= "similar: " pn "\\n"',
            'drop ::= "drop: " pn (" " pn){0,5} "\\n"',
            'done ::= "answer\\n"',
            f"pn ::= {pn}",
            f"did ::= {docs}",
            "char ::= [^\\n\\r\\t]",
        ]
    )


# What a step may do, each to its handler: what it brings back, and the
# passages it added. The one place the actions are named; "answer" ends
# the surf and has no handler.
Handler = Callable[[sqlite3.Connection, Surf, str], tuple[str, list[int]]]
DO: dict[str, Handler] = {
    "search": do_search,
    "read": do_read,
    "facts": lambda con, s, arg: (do_facts(con, s, arg), []),
    "walk": lambda con, s, arg: (do_walk(con, s, arg), []),
    "similar": lambda con, s, arg: (do_similar(con, s, arg), []),
    "drop": lambda con, s, arg: (do_drop(s, arg), []),
}


# the tools of the packs this host runs (docs/packs.md): each takes the
# step's argument as text and answers with text, adding no passage
def _pack_tool(tool: Callable[[sqlite3.Connection, Surf, str], str]) -> Handler:
    def handle(con: sqlite3.Connection, s: Surf, arg: str) -> tuple[str, list[int]]:
        return tool(con, s, arg), []

    return handle


PACK_TOOLS: tuple[str, ...] = ()
for _name, _tool in packs.tools(config.host_packs()).items():
    DO[_name] = _pack_tool(_tool)
    PACK_TOOLS += (_name,)
PACK_HELP = packs.tool_help(config.host_packs())
# a tool's own step grammar (the maths step is a JSON object), by tool
PACK_GRAMMAR = packs.tool_grammar(config.host_packs())
# what the answer is told about the tools' results, when it has some
RESULTS_PROMPT = packs.answer_prompts(config.host_packs())
# what checks an answer written with the tools on, after it is written
ANSWER_CHECKS = packs.answer_checks(config.host_packs())


def system(tools: bool = True) -> str:
    """The prompt of a step: the core's actions, and before ``answer`` the
    lines of the packs' tools this host runs (none with ``tools`` off)."""
    if not PACK_HELP or not tools:
        return SYSTEM
    head, sep, tail = SYSTEM.partition("\nanswer  ")
    return head + "\n" + "\n".join(PACK_HELP) + sep + tail


_ACTION = re.compile(rf"^({'|'.join([*DO, 'answer'])})\b\s*:?\s*(.*)$", re.IGNORECASE)


def parse(text: str) -> tuple[str, str, str]:
    """A step's reply as (note, action, argument); a reply that names no
    action means the model is done looking."""
    note, action, arg = "", "answer", ""
    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("note:") and not note:
            note = line[5:].strip()[:NOTE_CHARS]
            continue
        m = _ACTION.match(line)
        if m:
            action, arg = m.group(1).lower(), m.group(2).strip()
            break
        if not note:
            note = line[:NOTE_CHARS]
    return note, action, arg


# ------------------------------------------------------------------- loop


def run(
    con: sqlite3.Connection,
    question: str,
    *,
    answerer: ask.Answerer,
    steps: int,
    tokens: int,
    limit: int = ask.PASSAGES,
    doctype: str | None = None,
    history: list[dict[str, str]] | None = None,
    on_event: Event | None = None,
    stop: threading.Event | None = None,
    note: str = "",
    mode: str = "grounded",
    tools: bool = True,
) -> dict[str, Any]:
    """Surf, then answer. Step 0 is the door's own search of the question
    (what the one-shot ask starts from); the model's steps follow, up to
    ``steps`` of them and ``tokens`` of reading; the answer is the ask
    prompt over the passages kept. The result is an ask result with the
    ``trail``, the number of ``steps`` taken, what was ``dropped`` and the
    ``reading_left``. A ``stop`` event set while surfing (the client went
    away) ends the loop at the next step and skips the answer."""
    history = list(history or [])
    s = Surf(
        question=" ".join(question.split()),
        query=ask.search_query(question, history),
        history=history,
        doctype=doctype,
        note=note,
        mode=mode,
        tools=tools,
        limit=max(1, min(limit, 20)),
        steps_left=steps,
        tokens_left=tokens,
    )
    emit = on_event or (lambda e: None)
    t0 = time.monotonic()

    def record(step: Step, result: str, added: list[int], since: float) -> None:
        step.result = _summary(s, step.action, result, added)
        step.added = added
        step.seconds = round(time.monotonic() - since, 1)
        s.steps.append(step)
        emit({"event": "step", "step": step.to_dict()})

    t = time.monotonic()
    result, added = do_search(con, s, s.query)
    s.log += [f"Step 0 · search: {s.query}", s.spend(result), ""]
    record(Step(0, "search", s.query), result, added, t)
    for number, (result, added) in named_equations(con, s):
        # the question named it: the formula, read before the model's turn
        s.log += [f"Step 0 · read: equation ({number})", s.spend(result), ""]
        record(Step(0, "read", f"equation ({number})"), result, added, t)
    if not s.passages and mode != "open":
        return _result(s, answerer, None, t0)  # nothing to surf from

    while s.steps_left > 0 and s.tokens_left * 4 > HIT_CHARS:
        if stop is not None and stop.is_set():
            return _result(s, answerer, None, t0)
        t = time.monotonic()
        try:
            text, usage = answerer.step(
                system(s.tools), s.message(), grammar=grammar(s)
            )
        except Exception as exc:  # noqa: BLE001 - answer from what was read
            record(Step(len(s.steps), "error"), f"the model failed: {exc}", [], t)
            break
        s.add_usage(usage)
        said, action, arg = parse(text)
        s.steps_left -= 1
        step = Step(len(s.steps), action, arg, said)
        if action == "answer":
            s.log += [f"Step {step.n} · note: {said}", "answer", ""]
            record(step, "enough read", [], t)
            break
        added = []
        key = (action, " ".join(arg.split()).lower())
        if key in s.barren:
            # the same move, the same nothing: a small model asked the same
            # dead end five times in a row on a twelve-step budget
            result = (
                f"step {s.barren[key]} asked that and it brought nothing;"
                " ask something else"
            )
        elif action in PACK_TOOLS and not s.tools:
            result, added = f"{action} is not available here", []
        else:
            result, added = DO[action](con, s, arg)
            if action in PACK_TOOLS:
                s.worked.append(f"{action}: {' '.join(arg.split())}\n  -> {result}")
        if not added:
            s.barren.setdefault(key, step.n)
        line = f"{action}: {arg}" if arg else action
        s.log += [f"Step {step.n} · note: {said}", line, s.spend(result), ""]
        record(step, result, added, t)

    kept = s.kept
    if (not kept and mode != "open") or (stop is not None and stop.is_set()):
        return _result(s, answerer, None, t0)
    emit({"event": "answering", "model": answerer.name, "passages": len(kept)})
    bundle = ask.Bundle(
        question=s.question,
        passages=kept,
        history=history,
        note=s.note,
        mode=mode,
        results=results_of(s),
        results_prompt=RESULTS_PROMPT,
    )
    bundle.facts = store.document_facts(
        con, list(dict.fromkeys(p.doc_id for p in kept)), limit=ask.FACTS_PER_DOC
    )
    text, usage = answerer.answer(bundle)
    s.add_usage(usage)
    return _result(s, answerer, text, t0, bundle)


def results_of(s: Surf) -> list[str]:
    """What the packs' tools worked out during the surf, the latest
    ``WORKED_KEPT``: the answering model saw none of the steps, so a result
    it is not given is a result lost (the maths run of 2026-10-01: seven
    checks made, none reached an answer). A section of the bundle of its
    own, apart from the caller's note."""
    return s.worked[-WORKED_KEPT:]


WORKED_KEPT = 6  # the latest tool results an answer is given


def _summary(s: Surf, action: str, result: str, added: list[int]) -> str:
    """One line for the trail: what a step brought."""
    if len(added) > 1:
        return f"{len(added)} passages " + " ".join(f"[{n}]" for n in added)
    if added:
        p = next(x for x in s.passages if x.n == added[0])
        where = p.heading[-1] if p.heading else ""
        if p.page:
            where = f"{where}, p. {p.page}" if where else f"p. {p.page}"
        title = _cut(p.title or "(untitled)", 80)
        return f"[{p.n}] {title}" + (f" — {_cut(where, 50)}" if where else "")
    if action == "walk" and "\n- " in result:
        rels = sum(1 for line in result.splitlines() if line.startswith("- "))
        tail = result.rsplit("documents behind these: ", 1)
        docs = tail[1].count("doc ") if len(tail) == 2 else 0
        return (
            f"{rels} relation{'s' if rels != 1 else ''},"
            f" {docs} document{'s' if docs != 1 else ''} behind"
        )
    first = " ".join(result.split("\n", 1)[0].split())
    return first[:160]


def _cut(text: str, n: int) -> str:
    """At most n characters, cut at a word."""
    if len(text) <= n:
        return text
    head = text[:n].rsplit(" ", 1)[0]
    return (head if len(head) > n // 2 else text[:n]) + "…"


def _result(
    s: Surf,
    answerer: ask.Answerer,
    text: str | None,
    t0: float,
    bundle: ask.Bundle | None = None,
) -> dict[str, Any]:
    from prax.ml import pricing

    if bundle is None:
        bundle = ask.Bundle(
            question=s.question,
            passages=s.kept,
            history=s.history,
            note=s.note,
            mode=s.mode,
            results=results_of(s),
            results_prompt=RESULTS_PROMPT,
        )
    out = bundle.to_dict()
    checks: list[dict[str, Any]] = []
    if text and s.tools:
        # what the answer claims, checked after it is written (AD2): the
        # model was asked to mark its own unchecked numbers and did not
        for check in ANSWER_CHECKS:
            try:
                text, found = check(
                    text,
                    question=s.question,
                    passages=[p.text for p in bundle.passages],
                    worked=s.worked,
                )
            except Exception as exc:  # noqa: BLE001 - an answer without its marks
                logging.getLogger("prax.surf").warning("answer check failed: %s", exc)
                continue
            checks += found
    out.update(
        checks=checks,
        answer=text.strip() if text else None,
        model=answerer.name if text else None,
        citations=ask.citations(text, bundle) if text else [],
        usage=s.usage,
        seconds=round(time.monotonic() - t0, 1),
        cost_usd=round(pricing.cost_usd(answerer.name, s.usage), 5),
        trail=[st.to_dict() for st in s.steps],
        steps=len(s.steps) - 1,
        dropped=[
            {"n": p.n, "doc_id": p.doc_id, "title": p.title}
            for p in s.passages
            if p.n in s.dropped
        ],
        reading_left=s.tokens_left,
    )
    return out
