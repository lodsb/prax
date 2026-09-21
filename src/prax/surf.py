"""Surf: ask as a loop, the model using the library instead of being handed
one bundle.

The one-shot ask (``prax.ask``) gives a model the best passage of each of
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

import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from prax import ask, store

STEPS = 8  # the default number of steps; 0 is the one-shot ask
MAX_STEPS = 20
HIT_CHARS = 500  # a search hit's passage in the loop: enough to judge it
READ_CHARS = 1500  # what one "read" step returns
FACTS = 20  # graph facts per "facts" step
HIT_FACTS = 4  # graph facts shown with a search hit
WALK_EDGES = 25
SECTIONS = 12  # sections named when a reading found nothing
SIMILAR = 6
NOTE_CHARS = 200
QUERY_CHARS = 100
LOOK_CHARS = 60  # the words a read looks for inside a document
STEP_TOKENS = 160  # what a step's two lines may take
OVERHEAD_TOKENS = 2200  # the system prompt, the question, the step lines, the answer
MIN_TOKENS = 1000
LOCAL_TOKENS = 4000  # the default reading budget of a local model
CLAUDE_TOKENS = (16_000, 60_000)  # Claude's default and ceiling (cost, not context)
ACTIONS = ("search", "read", "facts", "walk", "similar", "drop", "answer")

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
    passages: list[ask.Passage] = field(default_factory=list)
    seq: dict[int, int] = field(default_factory=dict)  # passage n -> where to read on
    dropped: set[int] = field(default_factory=set)  # passage numbers
    docs: dict[int, str] = field(default_factory=dict)  # id -> title, named so far
    chunks: set[int] = field(default_factory=set)  # chunk ids shown already
    barren: dict[tuple[str, str], int] = field(default_factory=dict)  # what led nowhere
    log: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    usage: dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0}
    )

    @property
    def kept(self) -> list[ask.Passage]:
        return [p for p in self.passages if p.n not in self.dropped]

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
    hits = store.search(con, query, s.limit * 3, doctype=s.doctype)
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
    edges = store.traverse(con, name, hops=1)[:WALK_EDGES]
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
    for e in edges:
        outward = e["src"].lower() == name.lower()
        item = ("→ " if outward else "← ") + (e["dst"] if outward else e["src"])
        if e.get("source_doc"):
            item += f" (doc {e['source_doc']})"
        by_rel.setdefault(e["rel"], []).append(item)
    lines = [f"{name}:"]
    for rel, items in by_rel.items():
        lines.append(f"- {rel}: " + "; ".join(dict.fromkeys(items)))
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
    actions = ["search", "walk", "done"]
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


_ACTION = re.compile(
    r"^(search|read|facts|walk|similar|drop|answer)\b\s*:?\s*(.*)$", re.IGNORECASE
)


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
    if not s.passages:
        return _result(s, answerer, None, t0)  # nothing to surf from

    while s.steps_left > 0 and s.tokens_left * 4 > HIT_CHARS:
        if stop is not None and stop.is_set():
            return _result(s, answerer, None, t0)
        t = time.monotonic()
        try:
            text, usage = answerer.step(SYSTEM, s.message(), grammar=grammar(s))
        except Exception as exc:  # noqa: BLE001 - answer from what was read
            record(Step(len(s.steps), "error"), f"the model failed: {exc}", [], t)
            break
        for k, v in usage.items():
            s.usage[k] = s.usage.get(k, 0) + int(v)
        note, action, arg = parse(text)
        s.steps_left -= 1
        step = Step(len(s.steps), action, arg, note)
        if action == "answer":
            s.log += [f"Step {step.n} · note: {note}", "answer", ""]
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
        elif action == "search":
            result, added = do_search(con, s, arg)
        elif action == "read":
            result, added = do_read(con, s, arg)
        elif action == "facts":
            result = do_facts(con, s, arg)
        elif action == "walk":
            result = do_walk(con, s, arg)
        elif action == "similar":
            result = do_similar(con, s, arg)
        else:
            result = do_drop(s, arg)
        if not added:
            s.barren.setdefault(key, step.n)
        line = f"{action}: {arg}" if arg else action
        s.log += [f"Step {step.n} · note: {note}", line, s.spend(result), ""]
        record(step, result, added, t)

    kept = s.kept
    if not kept or (stop is not None and stop.is_set()):
        return _result(s, answerer, None, t0)
    emit({"event": "answering", "model": answerer.name, "passages": len(kept)})
    bundle = ask.Bundle(
        question=s.question, passages=kept, history=history, note=s.note
    )
    bundle.facts = store.document_facts(
        con, list(dict.fromkeys(p.doc_id for p in kept)), limit=ask.FACTS_PER_DOC
    )
    text, usage = answerer.answer(bundle)
    for k, v in usage.items():
        s.usage[k] = s.usage.get(k, 0) + int(v)
    return _result(s, answerer, text, t0, bundle)


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
    from prax import extraction

    if bundle is None:
        bundle = ask.Bundle(
            question=s.question, passages=s.kept, history=s.history, note=s.note
        )
    out = bundle.to_dict()
    out.update(
        answer=text.strip() if text else None,
        model=answerer.name if text else None,
        citations=ask.citations(text, bundle) if text else [],
        usage=s.usage,
        seconds=round(time.monotonic() - t0, 1),
        cost_usd=round(extraction.cost_usd(answerer.name, s.usage), 5),
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
