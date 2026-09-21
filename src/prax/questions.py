"""Standing questions, and the briefing: the pages that keep themselves.

A question page (kind ``question``) holds a question and the answer the
library gave it, kept the way "keep on page" keeps one: the answer, its
sources with their citations, the trail. What makes it stand is a cheap
check that needs no model: the page remembers the documents its answer
drew on (and their text hashes), the top of the search it was built
from, and how far the library had got (the highest document id). On
the door's clock (``schedule: questions: "06:30"``) every question page
is checked — the search run again, the entities of its sources looked
up — and when a document that arrived since ranks in that top set, or
shares two of the answer's entities, or a source was read again, the
question is asked again. The new answer is a new revision by the agent,
its note naming what changed; the sections a person added under the
answer are kept where they are. So the page's history is what the
library learned about the question, one revision per change.

The briefing is one page a day, "What arrived": the documents that
came since the last one, with the first line of their summaries, and
the questions whose answer moved. No model is asked for it.

Nothing here opens the database: everything goes through ``prax.store``
and ``prax.ask``, as a job on the door (``POST /questions/run``,
``prax questions``).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from prax import ask as ask_mod
from prax import store

log = logging.getLogger("prax.questions")

KIND = "question"
BRIEFING_KIND = "briefing"
TOP = 20  # how much of the search a question remembers
SHARED_ENTITIES = 2  # a new document sharing this many of the answer's entities
MAX_NOTE = 160

_SECTION = re.compile(r"\n(?=## )")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug_of(question: str) -> str:
    words = re.findall(r"[a-z0-9]+", question.lower())
    return "q-" + "-".join(words[:8])[:70].strip("-")


# ------------------------------------------------------------------ pages


def _previous_answer(text: str) -> str:
    """The answer alone out of a question page's text: after the title,
    before the source list and the trail."""
    own, _ = _own_part(text)
    body = own.split("\n", 1)[1] if own.startswith("#") and "\n" in own else own
    for marker in ("\n\nSources:\n", "\n\nHow it was found:\n"):
        cut = body.find(marker)
        if cut >= 0:
            body = body[:cut]
    return body.strip()


def _asked_again(new: list[dict[str, Any]], reread: list[int]) -> str:
    """The word to the model on a re-ask, beside the question (never in
    the search): what the library holds now that it did not, and the
    kind of change asked for — replace where the new evidence changes
    the answer, add where it adds, say so where it disagrees — so the
    model revises rather than starts over or clings to its earlier text."""
    if new:
        titles = "; ".join(str(n.get("title") or "") for n in new[:4])
        what = f"the library now also holds {titles}"
    elif reread:
        what = "a source of the earlier answer was read again and its text changed"
    else:
        what = "the question is asked again"
    return (
        f"{what}. Your earlier answer is above. Where the new evidence"
        " changes it, replace; where it adds, add; where it disagrees, say"
        " so; keep what still holds, and cite only the passages here."
    )


def _own_part(text: str) -> tuple[str, str]:
    """A question page's text split into the agent's part (title, answer,
    sources, trail) and what a person appended under it (every ``## ``
    section from the first on)."""
    m = _SECTION.search(text)
    if m is None:
        return text, ""
    return text[: m.start()], text[m.start() :]


def _fingerprint(
    con: sqlite3.Connection, question: str, options: dict[str, Any]
) -> dict[str, Any]:
    """The top of the search and the library's high-water mark."""
    hits = store.search(
        con,
        question,
        TOP,
        doctype=options.get("doctype"),
        domain=options.get("domain"),
    )
    top = list(dict.fromkeys(int(h["doc_id"]) for h in hits))
    since = con.execute("SELECT coalesce(max(id), 0) FROM documents").fetchone()[0]
    return {"top": top, "since_id": int(since)}


def _hashes(con: sqlite3.Connection, ids: list[int]) -> dict[str, str]:
    out: dict[str, str] = {}
    for doc_id in ids:
        row = con.execute(
            "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is not None:
            out[str(doc_id)] = row["text_hash"] or ""
    return out


def _titles(con: sqlite3.Connection, ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for doc_id in ids:
        row = con.execute(
            "SELECT title FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is not None:
            out[doc_id] = row["title"] or f"document {doc_id}"
    return out


def create(
    con: sqlite3.Connection,
    result: dict[str, Any],
    *,
    slug: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep an ask's result as a standing question: a page of kind
    ``question`` with the answer, and ``meta.question`` remembering what
    it was built from. ``options`` are what the ask was asked with
    (steps, tokens, doctype, domain, limit, backend), used again on a
    re-ask."""
    question = str(result.get("question") or "").strip()
    if not question:
        raise ValueError("the result names no question")
    if not (result.get("answer") or "").strip():
        raise ValueError("nothing to keep: the result has no answer")
    slug = store.slugify(slug or _slug_of(question))
    if store.get_page(con, slug) is not None:
        raise ValueError(f"a page {slug!r} exists already")
    section, docs = ask_mod.section_of(result)
    text = f"# {question}\n\n{section}\n"
    model = str(result.get("model") or "?")
    written = store.write_page(
        con,
        slug,
        text,
        title=question,
        kind=KIND,
        author="agent",
        note=f"ask: {model}",
        annotates=docs,
    )
    opts = {
        k: v
        for k, v in (options or {}).items()
        if k in ("steps", "tokens", "doctype", "domain", "limit", "backend")
        and v not in (None, "")
    }
    print_ = _fingerprint(con, question, opts)
    meta = store.get_meta(con, written["doc_id"])
    meta["question"] = {
        "question": question,
        "options": opts,
        "asked_at": _now(),
        "model": model,
        "revision": written["revision"],
        "sources": docs,
        "source_hashes": _hashes(con, docs),
        "top": print_["top"],
        "since_id": print_["since_id"],
        "history": [],
    }
    store.set_meta(con, written["doc_id"], meta)
    return {**written, "question": question}


def question_pages(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """The question pages with what they remember (``meta.question``)."""
    out = []
    for row in store.list_pages(con, kind=KIND):
        meta = store.get_meta(con, row["doc_id"])
        q = meta.get("question") or {}
        out.append({**row, "question": q})
    return out


# ------------------------------------------------------------------ check


def check(con: sqlite3.Connection, page: dict[str, Any]) -> dict[str, Any]:
    """Has the library learned something the question's answer did not
    see? Reads only, no model: the search run again for documents that
    arrived since and rank in its top ``TOP``; documents that arrived
    since and share ``SHARED_ENTITIES`` entities with the answer's
    sources; sources whose text was read again. Returns ``{due, new,
    reread, why}`` — ``new`` the documents as ``{doc_id, title}``."""
    q = page.get("question") or {}
    question = q.get("question") or ""
    if not question:
        return {"due": False, "new": [], "reread": [], "why": "no question kept"}
    since = int(q.get("since_id") or 0)
    known = {int(d) for d in q.get("top") or []} | {
        int(d) for d in q.get("sources") or []
    }
    fresh: dict[int, str] = {}
    print_ = _fingerprint(con, question, q.get("options") or {})
    for doc_id in print_["top"]:
        if doc_id not in known and doc_id > since and doc_id != page.get("doc_id"):
            fresh[doc_id] = "ranks in the search now"
    sources = [int(d) for d in q.get("sources") or []]
    if sources:
        marks = ",".join("?" * len(sources))
        entities = [
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT dst FROM edges WHERE source_doc IN ({marks})"
                " AND valid_to IS NULL",
                tuple(sources),
            )
        ]
        if entities:
            emarks = ",".join("?" * len(entities))
            for r in con.execute(
                f"SELECT source_doc, count(DISTINCT dst) AS n FROM edges"
                f" WHERE dst IN ({emarks}) AND valid_to IS NULL AND source_doc > ?"
                " GROUP BY source_doc HAVING n >= ?",
                (*entities, since, SHARED_ENTITIES),
            ):
                doc_id = int(r["source_doc"])
                if doc_id != page.get("doc_id") and doc_id not in fresh:
                    fresh[doc_id] = f"shares {r['n']} of the answer's entities"
    reread = []
    hashes = q.get("source_hashes") or {}
    for doc_id, old in hashes.items():
        row = con.execute(
            "SELECT text_hash FROM documents WHERE id = ?", (int(doc_id),)
        ).fetchone()
        if row is not None and (row["text_hash"] or "") != old:
            reread.append(int(doc_id))
    # a retired document is no news
    live = {
        doc_id for doc_id in fresh if not store.is_retired(store.get_meta(con, doc_id))
    }
    titles = _titles(con, list(live))
    new = [
        {"doc_id": d, "title": titles.get(d, f"document {d}"), "why": fresh[d]}
        for d in sorted(live)
    ]
    why = []
    if new:
        why.append(f"{len(new)} new document{'s' if len(new) > 1 else ''}")
    if reread:
        why.append(f"{len(reread)} source{'s' if len(reread) > 1 else ''} read again")
    return {
        "due": bool(new or reread),
        "new": new,
        "reread": reread,
        "why": ", ".join(why),
    }


# ---------------------------------------------------------------- refresh


def refresh(
    con: sqlite3.Connection,
    page: dict[str, Any],
    *,
    force: bool = False,
    answerer: Any = None,
    on_event: Any = None,
) -> dict[str, Any]:
    """Ask the question again when the check says so (or ``force``): the
    same options as before, the host's ask model unless the page names
    one; the answer written as a new agent revision, the person's
    sections kept, ``meta.question`` remembering the new state and the
    change in its history. Returns what happened."""
    q = page.get("question") or {}
    question = q.get("question") or ""
    slug = page["slug"]
    if not question:
        return {"slug": slug, "refreshed": False, "why": "no question kept"}
    seen = check(con, page)
    if not seen["due"] and not force:
        return {"slug": slug, "refreshed": False, "why": "nothing new"}
    opts = dict(q.get("options") or {})
    if answerer is None:
        answerer = (
            ask_mod.answerer_named(opts["backend"])
            if opts.get("backend")
            else ask_mod.current()
        )
    if answerer is None:
        return {"slug": slug, "refreshed": False, "why": "no model to ask"}
    steps = opts.get("steps")
    if steps is None:
        steps = ask_mod.default_steps()
    # the earlier answer is the conversation so far: the model revises it
    # in the light of what is new (named in the question) and may cite
    # only this turn's passages, so nothing stale comes back by name
    current = store.get_page(con, slug)
    previous = _previous_answer(current["text"] if current else "")
    history = [{"question": question, "answer": previous}] if previous else None
    result = ask_mod.ask(
        con,
        question,
        limit=int(opts.get("limit") or ask_mod.PASSAGES),
        doctype=opts.get("doctype"),
        answerer=answerer,
        history=history,
        steps=max(0, int(steps)),
        tokens=opts.get("tokens"),
        on_event=on_event,
        note=_asked_again(seen["new"], seen["reread"]),
    )
    if not (result.get("answer") or "").strip():
        return {"slug": slug, "refreshed": False, "why": "the model gave no answer"}
    section, docs = ask_mod.section_of(result)
    _, yours = _own_part(current["text"] if current else "")
    text = f"# {question}\n\n{section}\n" + (
        ("\n" + yours.lstrip("\n")) if yours.strip() else ""
    )
    model = str(result.get("model") or "?")
    changed = ", ".join(n["title"] for n in seen["new"][:3])
    if len(seen["new"]) > 3:
        changed += f" and {len(seen['new']) - 3} more"
    note = f"ask: {model}"
    if changed:
        note += f"; new: {changed}"
    elif seen["reread"]:
        note += "; a source was read again"
    elif force:
        note += "; asked again"
    note = note[:MAX_NOTE]
    written = store.write_page(
        con,
        slug,
        text,
        kind=KIND,
        author="agent",
        note=note,
        annotates=docs,
        force=True,  # the answer is the agent's; a person's sections were kept above
    )
    print_ = _fingerprint(con, question, opts)
    meta = store.get_meta(con, written["doc_id"])
    kept = dict(meta.get("question") or {})
    history = list(kept.get("history") or [])
    history.append(
        {
            "revision": written["revision"],
            "at": _now(),
            "model": model,
            "new": [{"doc_id": n["doc_id"], "title": n["title"]} for n in seen["new"]],
            "reread": seen["reread"],
            "forced": bool(force and not seen["due"]),
        }
    )
    kept.update(
        question=question,
        options=opts,
        asked_at=_now(),
        model=model,
        revision=written["revision"],
        sources=docs,
        source_hashes=_hashes(con, docs),
        top=print_["top"],
        since_id=print_["since_id"],
        history=history[-50:],
    )
    meta["question"] = kept
    store.set_meta(con, written["doc_id"], meta)
    return {
        "slug": slug,
        "refreshed": True,
        "revision": written["revision"],
        "doc_id": written["doc_id"],
        "new": seen["new"],
        "reread": seen["reread"],
        "model": model,
    }


def refresh_all(
    con: sqlite3.Connection,
    *,
    force: bool = False,
    only: str | None = None,
    answerer: Any = None,
    job: Any = None,
) -> dict[str, Any]:
    """Every question page checked and, when due, asked again; ``only``
    names one slug. Returns per page what happened."""
    pages = question_pages(con)
    if only:
        pages = [p for p in pages if p["slug"] == store.slugify(only)]
    out: dict[str, Any] = {"checked": len(pages), "refreshed": 0, "pages": {}}
    if job is not None:
        job.update(total=len(pages), done=0, note="questions")
    for n, page in enumerate(pages, 1):
        if job is not None:
            job.update(note=f"questions: {page['slug']}")
        try:
            rep = refresh(con, page, force=force, answerer=answerer)
        except Exception as exc:
            log.exception("question %s failed", page["slug"])
            rep = {"slug": page["slug"], "refreshed": False, "why": f"failed: {exc}"}
        out["pages"][page["slug"]] = rep
        if rep.get("refreshed"):
            out["refreshed"] += 1
        if job is not None:
            job.update(done=n)
    return out


# --------------------------------------------------------------- briefing


def _first_sentence(text: str, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    m = re.search(r"[.!?](?=\s|$)", text)
    out = text[: m.end()] if m and m.end() <= limit else text[:limit]
    return out.strip()


def _last_briefing_until(con: sqlite3.Connection) -> str | None:
    for row in store.list_pages(con, kind=BRIEFING_KIND, limit=1):
        meta = store.get_meta(con, row["doc_id"])
        until = (meta.get("briefing") or {}).get("until")
        if until:
            return str(until)
    return None


def briefing(
    con: sqlite3.Connection, *, day: str | None = None, job: Any = None
) -> dict[str, Any]:
    """The day's page, "What arrived": the documents that came since the
    last briefing (else the last day), each with the first line of its
    summary; the questions whose answer moved since. Written as the
    agent, once per day (the day's page is replaced by a new revision
    when run again)."""
    now = datetime.now(UTC)
    day = day or now.strftime("%Y-%m-%d")
    since = _last_briefing_until(con) or (now - timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if job is not None:
        job.update(note="briefing")
    rows = con.execute(
        "SELECT id, title, mime, meta FROM documents WHERE added_at > ?"
        " AND added_at <= ? AND json_extract(meta, '$.retired') IS NULL"
        " AND json_extract(meta, '$.page') IS NULL ORDER BY id",
        (since, until),
    ).fetchall()
    arrived = []
    for r in rows:
        meta = store.get_meta(con, r["id"])
        line = f"- [{r['title'] or 'document ' + str(r['id'])}](#doc/{r['id']})"
        bits = []
        if meta.get("video"):
            bits.append("video")
        elif r["mime"] == "application/pdf":
            bits.append("PDF")
        elif (r["mime"] or "").startswith("image/"):
            bits.append("image")
        elif r["mime"] in ("text/html", "application/xhtml+xml"):
            bits.append("web page")
        if meta.get("domains"):
            bits.append(", ".join(meta["domains"]))
        if bits:
            line += f" — {'; '.join(bits)}"
        summary = _first_sentence(meta.get("summary") or "")
        if summary:
            line += f": {summary}"
        arrived.append(line)
    moved = []
    for page in question_pages(con):
        q = page["question"]
        for h in q.get("history") or []:
            if since < str(h.get("at") or "") <= until:
                what = ", ".join(n["title"] for n in h.get("new") or [])
                moved.append(
                    f"- [{q.get('question')}](#doc/{page['doc_id']}) — revision"
                    f" {h.get('revision')}" + (f": new: {what}" if what else "")
                )
    text = f"# What arrived, {day}\n\n"
    text += (
        f"{len(arrived)} document{'s' if len(arrived) != 1 else ''} since"
        f" {since[:16].replace('T', ' ')}.\n\n" + "\n".join(arrived) + "\n"
        if arrived
        else f"Nothing arrived since {since[:16].replace('T', ' ')}.\n"
    )
    if moved:
        text += "\n## Questions that moved\n\n" + "\n".join(moved) + "\n"
    slug = f"briefing-{day}"
    written = store.write_page(
        con,
        slug,
        text,
        title=f"What arrived, {day}",
        kind=BRIEFING_KIND,
        author="agent",
        note=f"{len(arrived)} documents, {len(moved)} questions moved",
        force=True,
    )
    meta = store.get_meta(con, written["doc_id"])
    meta["briefing"] = {
        "day": day,
        "since": since,
        "until": until,
        "documents": len(arrived),
        "moved": len(moved),
    }
    store.set_meta(con, written["doc_id"], meta)
    return {**written, "documents": len(arrived), "moved": len(moved)}
