"""Pages: the notes that are documents too.

A page is a document with a slug and a revision history; the agent may add
to what a person wrote but never replace it. Writing one goes through the
same ingest as any other document, and its ``annotates`` and ``part_of``
edges go through the same graph.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Final

from prax.text import blocks, markup

from .base import _read_archive, _reading, _serialized, document_hidden
from .documents import _set_promote, get_meta, index_text, register, set_meta
from .graph import (
    Edge,
    document_node,
    find_edges,
    invalidate_edge,
    link,
    rename_entity,
)
from .retrieval import staleness

# Living Markdown documents (migration 0006): notes on a document, ongoing
# projects, topic pages. A page is a document, so everything that applies
# to documents applies; its identity is the slug, every save is a new text
# artifact with an append-only revision row, and its relationships to other
# documents are edges. An agent never overwrites human text: ``write_page``
# refuses an agent revision over a human one unless told to; ``append_page``
# adds a section instead, ``fill_blocks`` replaces the interiors of the
# page's ask blocks (``prax.text.blocks``) and nothing outside them. A
# ``question`` page is a standing question the door keeps answered
# (``prax.answering.questions``), a ``briefing`` the day's page of what arrived;
# both are the agent's, a person adds sections under them.

PAGE_KINDS = ("addendum", "project", "synthesis", "topic", "question", "briefing")


PAGE_AUTHORS = ("human", "agent")


_SLUG_CHARS = re.compile(r"[^a-z0-9]+")
# a link to a library document in a page's own prose, ``[title](#doc/12)``:
# the page annotates that document, an edge kept while the link stands
_DOC_LINK = markup.DOC_LINK
_LINK_EVIDENCE = "link in page"


def slugify(text: str) -> str:
    slug = _SLUG_CHARS.sub("-", text.lower()).strip("-")
    return slug[:80] or "page"


def _page_original(slug: str, text: str) -> bytes:
    """The archived original of a page: the first revision with an identity
    line, so two pages with the same opening text stay distinct documents."""
    return f"<!-- prax page: {slug} -->\n{text}".encode()


@_reading
def page_titles(con: sqlite3.Connection) -> set[str]:
    """Titles of the documents that are pages: the only names a page or
    project entity may carry."""
    return {
        r[0]
        for r in con.execute(
            "SELECT d.title FROM pages p JOIN documents d ON d.id = p.doc_id"
        )
        if r[0]
    }


@_reading
def get_page(con: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    """A page with its current text and revision list, or None."""
    row = con.execute(
        "SELECT p.doc_id, p.slug, p.kind, d.title, d.text_hash, d.meta"
        " FROM pages p JOIN documents d ON d.id = p.doc_id WHERE p.slug = ?",
        (slug,),
    ).fetchone()
    if row is None or document_hidden(con, int(row["doc_id"])):
        return None
    meta = json.loads(row["meta"] or "{}")
    text = _read_archive(row["text_hash"]).decode("utf-8") if row["text_hash"] else ""
    revisions = [
        dict(r)
        for r in con.execute(
            "SELECT revision, author, note, created_at, text_hash FROM page_revisions"
            " WHERE doc_id = ? ORDER BY revision",
            (row["doc_id"],),
        )
    ]
    return {
        "doc_id": row["doc_id"],
        "slug": row["slug"],
        "kind": row["kind"],
        "title": row["title"],
        "text": text,
        "revision": revisions[-1]["revision"] if revisions else 0,
        "author": revisions[-1]["author"] if revisions else None,
        "revisions": revisions,
        "meta": meta,
        "blocks": page_blocks(text, meta),
        **_lifecycle(con, int(row["doc_id"]), text),
    }


PAGE_SOURCES = 200  # sources of one page looked at for staleness


def _lifecycle(con: sqlite3.Connection, doc_id: int, text: str) -> dict[str, Any]:
    """Whether what the page rests on still holds: ``lifecycle`` is
    ``stale`` when one of its sources (the documents its links name, and
    those it annotates or synthesizes) is no longer current
    (``staleness``: superseded, retired, invalid), else ``active``; and
    ``stale_sources`` names them with what replaced each. A page is never
    changed for it: the person or the agent decides what follows."""
    ids = list(dict.fromkeys(linked_documents(text)))
    for r in con.execute(
        "SELECT DISTINCT t.name FROM edges x JOIN entities t ON t.id = x.dst"
        " WHERE x.source_doc = ? AND x.valid_to IS NULL"
        " AND x.rel IN ('annotates', 'synthesizes')",
        (doc_id,),
    ):
        got = con.execute(
            "SELECT id FROM documents WHERE title = ? ORDER BY id LIMIT 1", (r[0],)
        ).fetchone()
        if got is not None and int(got[0]) not in ids:
            ids.append(int(got[0]))
    ids = [i for i in ids if i != doc_id][:PAGE_SOURCES]
    if not ids:
        return {"lifecycle": "active", "stale_sources": []}
    marks = ",".join("?" * len(ids))
    hits = [
        {"doc_id": int(r[0]), "title": r[1]}
        for r in con.execute(
            f"SELECT id, title FROM documents WHERE id IN ({marks})", ids
        )
        if not document_hidden(con, int(r[0]))
    ]
    stale = staleness(con, hits)
    sources = [
        {"doc_id": h["doc_id"], "title": h["title"], **stale[h["doc_id"]]}
        for h in hits
        if h["doc_id"] in stale
    ]
    return {"lifecycle": "stale" if sources else "active", "stale_sources": sources}


def page_blocks(text: str, meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The ask blocks of a page's text as data, each with what the page's
    ``meta.asks`` remembers of it (when it was asked, by which model, its
    sources, whether the pass found it edited by hand and left it)."""
    asks = (meta or {}).get("asks") or {}
    out = []
    for b in blocks.blocks(text):
        kept = asks.get(b.id) or {}
        out.append(
            {
                "id": b.id,
                "question": b.question,
                "options": dict(b.options),
                "filled": b.filled,
                "held": blocks.held(b, text),
                "asked": b.asked,
                "run": b.tail_attrs.get("run"),
                "asked_at": kept.get("asked_at"),
                "model": kept.get("model"),
                "sources": kept.get("sources") or [],
                "checked_at": kept.get("checked_at"),
                "history": len(kept.get("history") or []),
                "left": kept.get("held"),  # {at, why} when the pass left it
            }
        )
    return out


def linked_documents(text: str) -> list[int]:
    """The library documents a page's text links to, ``[title](#doc/N)``,
    in order of first mention."""
    return list(dict.fromkeys(int(m.group(1)) for m in _DOC_LINK.finditer(text or "")))


@_reading
def page_revision_text(con: sqlite3.Connection, slug: str, revision: int) -> str:
    row = con.execute(
        "SELECT r.text_hash FROM page_revisions r JOIN pages p ON p.doc_id = r.doc_id"
        " WHERE p.slug = ? AND r.revision = ?",
        (slug, revision),
    ).fetchone()
    if row is None:
        raise KeyError(f"no revision {revision} of page {slug!r}")
    return _read_archive(row["text_hash"]).decode("utf-8")


@_reading
def list_pages(
    con: sqlite3.Connection, *, kind: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    where = "WHERE p.kind = ?" if kind else ""
    args: tuple[Any, ...] = (kind, limit) if kind else (limit,)
    rows = con.execute(
        f"""
        SELECT p.doc_id, p.slug, p.kind, d.title,
               (SELECT max(revision) FROM page_revisions r WHERE r.doc_id = p.doc_id)
                   AS revision,
               (SELECT author FROM page_revisions r WHERE r.doc_id = p.doc_id
                ORDER BY revision DESC LIMIT 1) AS author,
               (SELECT max(created_at) FROM page_revisions r WHERE r.doc_id = p.doc_id)
                   AS updated_at
        FROM pages p JOIN documents d ON d.id = p.doc_id {where}
        ORDER BY updated_at DESC LIMIT ?
        """,
        args,
    ).fetchall()
    return [dict(r) for r in rows]


def write_page(
    con: sqlite3.Connection,
    slug: str,
    text: str,
    *,
    title: str | None = None,
    kind: str = "topic",
    author: str = "human",
    note: str | None = None,
    annotates: list[int] | None = None,
    part_of: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create or replace a page's text as a new revision.

    ``annotates`` names documents this page is about (``page --annotates-->
    paper`` edges, the page as source document); ``part_of`` names a
    project page by slug (``page --part_of--> project``). An ``agent``
    revision over a ``human`` one is refused unless ``force``; use
    ``append_page`` for agent additions. Returns ``{doc_id, slug, revision,
    created}``.
    """
    if kind not in PAGE_KINDS:
        raise ValueError(f"kind must be one of {PAGE_KINDS}")
    if author not in PAGE_AUTHORS:
        raise ValueError(f"author must be one of {PAGE_AUTHORS}")
    slug = slugify(slug)
    existing = con.execute(
        "SELECT p.doc_id, p.kind, d.title FROM pages p"
        " JOIN documents d ON d.id = p.doc_id"
        " WHERE p.slug = ?",
        (slug,),
    ).fetchone()
    if existing is not None and document_hidden(con, int(existing["doc_id"])):
        # a restricted viewer writes to nothing it may not see: as if absent
        raise KeyError(f"no page {slug!r}")
    created = existing is None
    if created:
        title = title or slug.replace("-", " ").capitalize()
        reg = register(
            con,
            _page_original(slug, text),
            mime="text/markdown",
            title=title,
            meta={"source": "wiki", "page": {"slug": slug, "kind": kind}},
        )
        doc_id = reg["doc_id"]
        con.execute(
            "INSERT INTO pages (doc_id, slug, kind) VALUES (?, ?, ?)",
            (doc_id, slug, kind),
        )
        revision = 1
    else:
        doc_id = existing["doc_id"]
        kind = existing["kind"]
        last = con.execute(
            "SELECT revision, author FROM page_revisions WHERE doc_id = ?"
            " ORDER BY revision DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        if last and last["author"] == "human" and author == "agent" and not force:
            raise PermissionError(
                f"page {slug!r} was last written by a person; append_page adds"
                " a section, force=True overwrites"
            )
        revision = (last["revision"] if last else 0) + 1
        if title and title != existing["title"]:
            # a rename: the page's entity in the graph goes with it, so
            # its edges stay its own (merged into a namesake if one exists)
            con.execute("UPDATE documents SET title = ? WHERE id = ?", (title, doc_id))
            page_type = "project" if kind == "project" else "page"
            row = con.execute(
                "SELECT id FROM entities WHERE name = ? AND type = ?",
                (existing["title"], page_type),
            ).fetchone()
            if row is not None:
                rename_entity(con, row["id"], title)
    indexed = index_text(con, doc_id, text, text_source=f"page/{author}")
    con.execute(
        "INSERT INTO page_revisions (doc_id, revision, text_hash, author, note)"
        " VALUES (?, ?, ?, ?, ?)",
        (doc_id, revision, indexed["text_hash"], author, note),
    )
    meta = get_meta(con, doc_id)
    meta["page"] = {
        **(meta.get("page") or {}),
        "slug": slug,
        "kind": kind,
        "revision": revision,
        "author": author,
    }
    set_meta(con, doc_id, meta)
    page_title = con.execute(
        "SELECT title FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()[0]
    page_type = "project" if kind == "project" else "page"
    # a synthesis or a project draws on its sources; any other page
    # annotates one document. A project page is a `project` in the graph,
    # which `annotates` does not take: a project page linking a paper was
    # refused outright until the graph-file tests wrote one (2026-09-27)
    source_rel = "synthesizes" if kind in ("synthesis", "project") else "annotates"
    for target in annotates or []:
        try:
            # one node per document, whatever links it (``document_node``)
            target_name, target_type = document_node(con, target)
        except KeyError:
            raise KeyError(f"no such document to annotate: {target}") from None
        edge = Edge(page_title, page_type, source_rel, target_name, target_type)
        if kind == "synthesis" and target_type != "page":
            _set_promote(con, target, by="page", reason=f"source of synthesis {slug}")
        if not find_edges(con, edge):
            link(
                con,
                edge,
                confidence="EXTRACTED",
                source_doc=doc_id,
                evidence=f"page {slug} revision {revision}",
                producer="page",
                run=f"{slug}@{revision}",
            )
    _link_edges(
        con, doc_id, slug, revision, text, page_title, page_type, source_rel, annotates
    )
    if part_of:
        project = con.execute(
            "SELECT d.title FROM pages p JOIN documents d ON d.id = p.doc_id"
            " WHERE p.slug = ? AND p.kind = 'project'",
            (slugify(part_of),),
        ).fetchone()
        if project is None:
            raise KeyError(f"no project page {part_of!r}")
        edge = Edge(page_title, page_type, "part_of", project[0], "project")
        if not find_edges(con, edge):
            link(
                con,
                edge,
                confidence="EXTRACTED",
                source_doc=doc_id,
                evidence=f"page {slug} revision {revision}",
                producer="page",
                run=f"{slug}@{revision}",
            )
    con.commit()
    return {"doc_id": doc_id, "slug": slug, "revision": revision, "created": created}


def _link_edges(
    con: sqlite3.Connection,
    doc_id: int,
    slug: str,
    revision: int,
    text: str,
    page_title: str,
    page_type: str,
    rel: str,
    annotates: list[int] | None,
) -> None:
    """The documents the page's text links to are what it annotates (or
    synthesizes) too: an edge for each link, made when the link appears
    and retired when it goes — the evidence says it was a link, so an
    edge given as ``annotates`` (a note made from a document's page, the
    sources of an answer kept) is never retired by an edit that leaves
    the link out. A link to a document the library does not have, or to
    the page itself, makes no edge."""
    named = set(annotates or [])
    wanted_titles: set[str] = set()
    for target in linked_documents(text):
        if target == doc_id or target in named:
            continue
        try:
            target_name, target_type = document_node(con, target)
        except KeyError:
            continue
        wanted_titles.add(target_name)
        edge = Edge(page_title, page_type, rel, target_name, target_type)
        if not find_edges(con, edge):
            link(
                con,
                edge,
                confidence="EXTRACTED",
                source_doc=doc_id,
                evidence=f"{_LINK_EVIDENCE} {slug} revision {revision}",
                producer="page",
                run=f"{slug}@{revision}",
            )
    gone = con.execute(
        "SELECT e.id, t.name FROM edges e JOIN entities t ON t.id = e.dst"
        " WHERE e.source_doc = ? AND e.producer = 'page' AND e.rel = ?"
        " AND e.valid_to IS NULL AND e.evidence LIKE ?",
        (doc_id, rel, _LINK_EVIDENCE + " %"),
    ).fetchall()
    for row in gone:
        if row["name"] not in wanted_titles:
            invalidate_edge(con, row["id"])


def fill_blocks(
    con: sqlite3.Connection,
    slug: str,
    fills: dict[str, str],
    *,
    asked: str,
    run: str,
    note: str | None = None,
    release: set[str] | None = None,
) -> dict[str, Any]:
    """Replace the interiors of a page's ask blocks (``prax.text.blocks``), by
    id, as one agent revision; nothing outside the blocks changes, so a
    person's text needs no ``force``. A block edited by hand since the
    door wrote it is left as it is and reported ``held`` unless its id is
    in ``release``; a block the page no longer has (its markers gone) is
    structural, not a permission, and raises. The documents the new
    interiors link become the page's edges the way any link does.
    Returns ``{doc_id, slug, revision, created, report}`` with the report
    ``{id: "filled" | "held"}``; the revision is unchanged when nothing
    was filled."""
    page = get_page(con, slugify(slug))
    if page is None:
        raise KeyError(f"no page {slug!r}")
    text, report = blocks.fill(
        page["text"], fills, asked=asked, run=run, release=release
    )
    missing = [i for i, r in report.items() if r == "missing"]
    if missing:
        raise ValueError(
            f"page {slug!r} has no ask block {', '.join(missing)}: its markers are gone"
        )
    if not any(r == "filled" for r in report.values()):
        return {
            "doc_id": page["doc_id"],
            "slug": page["slug"],
            "revision": page["revision"],
            "created": False,
            "report": report,
        }
    written = write_page(
        con,
        page["slug"],
        text,
        kind=page["kind"],
        author="agent",
        note=note,
        force=True,  # the blocks are the door's; the rest of the text is as it was
    )
    return {**written, "report": report}


def append_page(
    con: sqlite3.Connection,
    slug: str,
    section: str,
    *,
    heading: str | None = None,
    author: str = "agent",
    note: str | None = None,
    annotates: list[int] | None = None,
) -> dict[str, Any]:
    """Add a section to an existing page as a new revision: the agent's way
    of contributing without touching what a person wrote. ``annotates``
    adds ``annotates`` edges to the documents the section rests on."""
    page = get_page(con, slugify(slug))
    if page is None:
        raise KeyError(f"no page {slug!r}")
    block = section.strip()
    if heading:
        block = f"## {heading}\n\n{block}"
    text = page["text"].rstrip() + "\n\n" + block + "\n"
    return write_page(
        con,
        page["slug"],
        text,
        author=author,
        note=note,
        force=True,
        kind=page["kind"],
        annotates=annotates,
    )


def update_section(
    con: sqlite3.Connection,
    slug: str,
    heading: str,
    text: str,
    *,
    author: str = "agent",
    note: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Replace the body of the section headed ``heading`` with ``text``, as
    a new revision, or add the section at the end when the page has none:
    the place an agent keeps a status or a summary it rewrites, without
    appending a new copy each time. ``created`` in the answer says which.

    An agent never replaces what a person wrote: a section whose current
    body is in a revision a person saved is refused (PermissionError)
    unless ``force``. Nor does it replace an ask block's question; the
    questions pass owns that block's interior (ValueError)."""
    page = get_page(con, slugify(slug))
    if page is None:
        raise KeyError(f"no page {slug!r}")
    old = page["text"]
    span = markup.section_span(old, heading)
    body = text.strip()
    if span is None:
        appended = append_page(
            con, page["slug"], body, heading=heading, author=author, note=note
        )
        return {**appended, "section": "added"}
    start, end, _level = span
    current = old[start:end].strip()
    if blocks.HEAD.search(current):
        raise ValueError(
            f"section {heading!r} holds an ask block; the questions pass keeps it"
        )
    if author == "agent" and not force and current:
        human = con.execute(
            "SELECT r.text_hash FROM page_revisions r WHERE r.doc_id = ?"
            " AND r.author = 'human'",
            (page["doc_id"],),
        ).fetchall()
        for row in human:
            if current in _read_archive(row["text_hash"]).decode("utf-8"):
                raise PermissionError(
                    f"section {heading!r} of page {slug!r} is a person's text;"
                    " force=True replaces it"
                )
    tail = old[end:]
    new_text = (
        old[:start]
        + "\n"
        + body
        + "\n"
        + ("\n" if tail.strip() else "")
        + tail.lstrip("\n")
    )
    written = write_page(
        con,
        page["slug"],
        new_text,
        author=author,
        note=note or f"section {heading!r} replaced",
        force=True,
        kind=page["kind"],
    )
    return {**written, "section": "replaced"}


@_serialized
def add_to_project(
    con: sqlite3.Connection, project_slug: str, doc_id: int, *, promote: bool = True
) -> int | None:
    """``paper --part_of--> project`` for a library document; the edge id,
    or None when it already exists. A paper a person adds is flagged for
    the promote pass; a project's synced notes are not (``promote``)."""
    project = con.execute(
        "SELECT p.doc_id, d.title FROM pages p JOIN documents d ON d.id = p.doc_id"
        " WHERE p.slug = ? AND p.kind = 'project'",
        (slugify(project_slug),),
    ).fetchone()
    if project is None:
        raise KeyError(f"no project page {project_slug!r}")
    name, node_type = document_node(con, doc_id)
    is_page = node_type == "page"
    edge = Edge(name, node_type, "part_of", project["title"], "project")
    if find_edges(con, edge):
        return None
    if not is_page and promote:
        _set_promote(con, doc_id, by="page", reason=f"member of project {project_slug}")
    return link(
        con,
        edge,
        confidence="EXTRACTED",
        source_doc=project["doc_id"],
        evidence=f"project {project_slug}",
        producer="page",
        run=slugify(project_slug),
    )


@_reading
def pages_with_chunks_of(con: sqlite3.Connection, kind: str) -> list[str]:
    """The slugs of the pages that hold a chunk of ``kind`` (``ask``: the
    pages with standing questions), in order."""
    return [
        str(r["slug"])
        for r in con.execute(
            "SELECT DISTINCT p.slug FROM chunks c JOIN pages p ON p.doc_id = c.doc_id"
            " WHERE c.kind = ? ORDER BY p.slug",
            (kind,),
        )
    ]


@_serialized
def mark_open_answer(con: sqlite3.Connection, doc_id: int) -> None:
    """Mark a page as holding an open answer of ``ask`` (``meta.page.open``):
    the model's own knowledge beside the library's passages. A later
    search still finds the page; ``ask`` and the surfer never take it as
    the library's evidence. The mark stays with the page's revisions."""
    meta = get_meta(con, doc_id)
    meta["page"] = {**(meta.get("page") or {}), "open": True}
    set_meta(con, doc_id, meta)


@_reading
def open_answer_documents(con: sqlite3.Connection, doc_ids: list[int]) -> set[int]:
    """Which of these documents are pages marked as open answers."""
    ids = sorted({int(i) for i in doc_ids})
    if not ids:
        return set()
    rows = con.execute(
        "SELECT id FROM documents WHERE id IN"
        f" ({','.join('?' * len(ids))})"
        " AND json_extract(meta, '$.page.open') = 1",
        ids,
    )
    return {int(r[0]) for r in rows}


# ------------------------------------------------------------- projects
#
# A project's manifest (migration 38): where its working copy is, what is
# read from it, and whether the session-end hook syncs it on its own. The
# plan of a sync and its documents are ``prax.capture.projects``.

PROJECT_SOURCE: Final = "project"  # ``meta.source`` of a synced document


def _project_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    out["settings"] = json.loads(out.get("settings") or "{}")
    out["last"] = json.loads(out["last"]) if out.get("last") else None
    out["auto_sync"] = bool(out.get("auto_sync"))
    return out


@_reading
def project_named(con: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    """A project's manifest by name, or None."""
    return _project_row(
        con.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
    )


@_reading
def project_at(
    con: sqlite3.Connection, remote: str | None, prefix: str
) -> dict[str, Any] | None:
    """The project of a working copy: its canonical remote and its folder
    within the repository. None without a remote, which keys nothing."""
    if not remote:
        return None
    return _project_row(
        con.execute(
            "SELECT * FROM projects WHERE remote = ? AND prefix = ?", (remote, prefix)
        ).fetchone()
    )


@_reading
def list_projects(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every project's manifest, by name."""
    rows = con.execute("SELECT * FROM projects ORDER BY name").fetchall()
    return [p for p in (_project_row(r) for r in rows) if p is not None]


@_serialized
def save_project(
    con: sqlite3.Connection,
    name: str,
    *,
    remote: str | None,
    prefix: str,
    settings: dict[str, Any],
    auto_sync: bool | None = None,
) -> dict[str, Any]:
    """Write a project's manifest; ``auto_sync`` None keeps what it was. A
    working copy belongs to one project: a second name for the same remote
    and folder is refused (ValueError)."""
    if not name or not name.strip():
        raise ValueError("a project needs a name")
    if remote:
        taken = con.execute(
            "SELECT name FROM projects WHERE remote = ? AND prefix = ? AND name != ?",
            (remote, prefix, name),
        ).fetchone()
        if taken:
            raise ValueError(
                f"{remote} {prefix or '(the whole repository)'} is project"
                f" {taken[0]!r} already"
            )
    con.execute(
        "INSERT INTO projects (name, remote, prefix, settings, auto_sync)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET remote = excluded.remote,"
        " prefix = excluded.prefix, settings = excluded.settings,"
        " auto_sync = CASE WHEN ? IS NULL THEN projects.auto_sync"
        " ELSE excluded.auto_sync END",
        (
            name,
            remote,
            prefix,
            json.dumps(settings, sort_keys=True),
            int(bool(auto_sync)),
            None if auto_sync is None else int(auto_sync),
        ),
    )
    con.commit()
    got = _project_row(
        con.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
    )
    assert got is not None
    return got


@_serialized
def note_project_sync(con: sqlite3.Connection, name: str, last: dict[str, Any]) -> None:
    """When a project was last synced, and what that sync did."""
    con.execute(
        "UPDATE projects SET synced_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),"
        " last = ? WHERE name = ?",
        (json.dumps(last, sort_keys=True), name),
    )
    con.commit()


@_reading
def project_documents(
    con: sqlite3.Connection, name: str, keys: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """A project's live synced documents by key: ``{key: {doc_id, version,
    path}}``, those its name carries and those any of ``keys`` names (a
    document synced before the manifest, under an older key)."""
    keys = keys or []
    marks = ",".join("?" * len(keys)) or "NULL"
    rows = con.execute(
        "SELECT id, json_extract(meta, '$.project.key') AS key,"
        " json_extract(meta, '$.project.version') AS version,"
        " json_extract(meta, '$.project.path') AS path FROM documents"
        " WHERE json_extract(meta, '$.source') = ?"
        " AND json_extract(meta, '$.retired') IS NULL"
        " AND (json_extract(meta, '$.project.name') = ?"
        f" OR json_extract(meta, '$.project.key') IN ({marks}))"
        " ORDER BY id",
        (PROJECT_SOURCE, name, *keys),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["key"] and r["key"] not in out:
            out[str(r["key"])] = {
                "doc_id": int(r["id"]),
                "version": r["version"],
                "path": r["path"],
            }
    return out
