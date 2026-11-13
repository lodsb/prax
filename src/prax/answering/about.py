"""What the library knows about a subject of a project, and what is open on
it (plan item AU, step 4): one read for "what about the period index?".

A project's register of open questions is synced as one document a question
(``prax.text.register``, ``prax.capture.projects``), each carrying its state.
``about`` takes a project and a question in a person's words and answers in
about forty lines:

- the questions on it, open ones first, then retracted (believed, acted on,
  found false: what stands on one is suspect), then answered, then retired;
  each with its state and date, its pointer, the line that settles or
  answered it (``test``, ``answer``, ``what is true``) and its ``why not``
  lines, what was tried or ruled out;
- three to five pointers to the project's other documents the library's
  search ranks first for it (journal entries, notes).

A question is on the subject when the query's words are in it, each word
weighed by how few of the project's questions hold it (a word every question
holds, "open" in each status line, says nothing) and its heading counting
double, with half the rarest word's weight more when the search ranks it
among the project's first ``SEARCH_FIRST``; it is shown when it scores at
least ``RELEVANT_SHARE`` of the best. The status is the repository's: nothing here
writes. The fields are cut to ``FIELD_CHARS``.
"""

from __future__ import annotations

import math
import re
import sqlite3
from typing import Any

from prax import store
from prax.errors import NotFound
from prax.text import register

QUESTIONS_SHOWN = 8  # questions in an answer; ``left_out`` counts the rest
POINTERS_SHOWN = 5  # the project's other documents in an answer
SEARCH_DEPTH = 60  # hits of the library's search read for the project's
SEARCH_FIRST = 10  # a question the search ranks this high is on the subject
RELEVANT_SHARE = 0.5  # of the best question's score, to be shown
FIELD_CHARS = 240
# what each state shows of a question: the first of these fields it has
SAYS = {
    "open": ("test",),
    "retracted": ("what is true", "status"),
    "answered": ("answer", "status"),
    "retired": ("status",),
}
_WORD = re.compile(r"[^\W_]+")


def _words(text: str) -> list[str]:
    return [
        w
        for w in _WORD.findall(text.lower())
        if len(w) > 2 and w not in store.STOPWORDS
    ]


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= FIELD_CHARS else text[: FIELD_CHARS - 1] + "…"


def _project(con: sqlite3.Connection, name: str) -> str:
    """A project's name as kept, from its name or the name's slug."""
    if store.project_named(con, name) is not None:
        return name
    want = store.slugify(name)
    for p in store.list_projects(con):
        if store.slugify(str(p["name"])) == want:
            return str(p["name"])
    raise NotFound(f"no project {name!r}")


def about(con: sqlite3.Connection, project: str, query: str) -> dict[str, Any]:
    """The questions of ``project`` on what ``query`` asks about, and the
    project's documents to read on it (the module's docstring)."""
    if not query.strip():
        raise ValueError("about what? the query is empty")
    name = _project(con, project)
    docs = store.project_documents(con, name)
    questions: list[tuple[int, str, register.Question]] = []
    for v in docs.values():
        held = v.get("question")
        if not held:
            continue
        doc_id = int(v["doc_id"])
        try:
            doc = store.get_document(con, doc_id)
        except NotFound:
            continue  # behind the wall for this viewer
        text = str(doc.get("text") or "") if doc else ""
        q = register.one(text, held.get("section"))
        if q is None:
            continue
        questions.append((doc_id, str(held.get("state") or q.state or ""), q))
    ours = {int(v["doc_id"]) for v in docs.values()}
    ranked = [
        h["doc_id"]
        for h in store.search(con, query, SEARCH_DEPTH)
        if h["doc_id"] in ours
    ]
    first = set(ranked[:SEARCH_FIRST])
    asked = set(_words(query))
    held_by = {i: set(_words(q.text)) for i, _, q in questions}
    weight = {
        w: math.log(
            (len(questions) + 1) / (0.5 + sum(w in h for h in held_by.values()))
        )
        for w in asked
    }
    scored = []
    for doc_id, state, q in questions:
        head = set(_words(q.heading))
        score = sum(
            weight[w] * (2 if w in head else 1) for w in asked & held_by[doc_id]
        )
        if doc_id in first:
            score += max(weight.values(), default=1.0) / 2
        if score > 0:
            scored.append((score, doc_id, state, q))
    best = max((s for s, *_ in scored), default=0.0)
    order = {s: i for i, s in enumerate(register.STATES)}
    on = sorted(
        (x for x in scored if x[0] >= RELEVANT_SHARE * best),
        key=lambda x: (order.get(x[2], len(order)), -x[0]),
    )
    shown = on[:QUESTIONS_SHOWN]
    out_q = []
    for _score, doc_id, state, q in shown:
        item: dict[str, Any] = {
            "question": q.heading,
            "state": state,
            "pointer": store.pointer_of(con, doc_id),
        }
        if q.since:
            item["since"] = q.since
        if q.asked:
            item["asked"] = q.asked
        says = next(
            ((k, q.field(k)) for k in SAYS.get(state, ("status",)) if q.field(k)),
            None,
        )
        if says:
            item["says"] = f"{says[0]}: {_cut(str(says[1]))}"
        if q.why_not:
            item["why_not"] = [f"{k}: {_cut(v)}" for k, v in q.why_not]
        out_q.append(item)
    register_ids = {i for i, _, _ in questions}
    read: list[dict[str, Any]] = []
    for doc_id in ranked:
        if doc_id in register_ids or len(read) >= POINTERS_SHOWN:
            continue
        doc = store.get_document(con, doc_id)
        read.append(
            {
                "pointer": store.pointer_of(con, doc_id),
                "title": doc.get("title") if doc else None,
            }
        )
    return {
        "project": name,
        "about": query,
        "questions": out_q,
        "left_out": len(on) - len(shown),
        "read": read,
    }
