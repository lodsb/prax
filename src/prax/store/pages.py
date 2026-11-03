"""Pages: the notes that are documents too.

A page is a document with a slug and a revision history; the agent may add
to what a person wrote but never replace it. Writing one goes through the
same ingest as any other document, and its ``annotates`` and ``part_of``
edges go through the same graph.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Final

from prax.text import blocks, markup, slugs

from .base import (
    _read_archive,
    _reading,
    _serialized,
    document_hidden,
    hidden_documents,
    personal_sql,
)
from .documents import (
    _set_promote,
    get_meta,
    index_text,
    register,
    set_meta,
    set_sensitivity,
)
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


# a link to a library document in a page's own prose, ``[title](#doc/12)``:
# the page annotates that document, an edge kept while the link stands
_DOC_LINK = markup.DOC_LINK
_LINK_EVIDENCE = "link in page"


def slugify(text: str) -> str:
    return slugs.slug(text)


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


def _slug_of(con: sqlite3.Connection, slug: str) -> str:
    """The slug a page has now: ``slug`` itself, or the page's current one
    when ``slug`` is an old name of it (``page_aliases``, migration 67)."""
    row = con.execute(
        "SELECT p.slug FROM page_aliases a JOIN pages p ON p.doc_id = a.doc_id"
        " WHERE a.slug = ?",
        (slug,),
    ).fetchone()
    return str(row[0]) if row else slug


# a page past this many characters is worth splitting: an append or a
# section written to it says so (``large`` in the answer). The nlbank log
# reached 407,101 characters in 183 revisions (2026-11-03)
PAGE_LARGE: Final = 200_000


@_reading
def get_page(
    con: sqlite3.Connection,
    slug: str,
    *,
    text: bool = True,
    offset: int = 0,
    limit: int | None = None,
) -> dict[str, Any] | None:
    """A page with its current text and revision list, or None. A slug the
    page had before a rename finds it too, and ``asked_as`` then names the
    old one. ``text`` False leaves the text, its ask blocks and its
    lifecycle out: where the page sits, its size and its history only.
    ``offset`` and ``limit`` give a window of the text, in characters;
    ``text_len`` and ``text_hash`` (the sha256 of the whole stored text's
    UTF-8 bytes) let a reader check that the windows it read are the page."""
    asked = slug
    current = _slug_of(con, asked)
    row = con.execute(
        "SELECT p.doc_id, p.slug, p.kind, p.gid, d.title, d.text_hash, d.text_len,"
        " d.meta FROM pages p JOIN documents d ON d.id = p.doc_id WHERE p.slug = ?",
        (current,),
    ).fetchone()
    if row is None or document_hidden(con, int(row["doc_id"])):
        return None
    doc_id = int(row["doc_id"])
    meta = json.loads(row["meta"] or "{}")
    revisions = [
        dict(r)
        for r in con.execute(
            "SELECT revision, author, note, created_at, text_hash, change"
            " FROM page_revisions WHERE doc_id = ? ORDER BY revision",
            (doc_id,),
        )
    ]
    written = [r for r in revisions if r["change"] == "text"]
    out: dict[str, Any] = {
        "doc_id": doc_id,
        "slug": row["slug"],
        "kind": row["kind"],
        "gid": row["gid"],
        "title": row["title"],
        "revision": revisions[-1]["revision"] if revisions else 0,
        # who wrote the text: a move or a rename writes no one's words
        "author": written[-1]["author"] if written else None,
        "revisions": revisions,
        "meta": meta,
        "text_len": int(row["text_len"] or 0),
        "text_hash": row["text_hash"],
        "ancestors": _ancestors(con, doc_id),
        "children": _children(con, doc_id),
        "projects": _projects_of(con, doc_id),
        "aliases": _aliases(con, doc_id),
    }
    if row["kind"] == "project":
        out["members"] = _members(con, str(row["title"]))
    if current != asked:
        out["asked_as"] = asked
    if not text:
        return out
    whole = _read_archive(row["text_hash"]).decode("utf-8") if row["text_hash"] else ""
    if offset or limit is not None:
        start = max(0, offset)
        end = len(whole) if limit is None else start + max(0, limit)
        out["text"] = whole[start:end]
        out["offset"] = start
        out["next_offset"] = end if end < len(whole) else None
    else:
        out["text"] = whole
    out["blocks"] = page_blocks(whole, meta)
    return {**out, **_lifecycle(con, doc_id, whole)}


def _aliases(con: sqlite3.Connection, doc_id: int) -> list[str]:
    return [
        str(r[0])
        for r in con.execute(
            "SELECT slug FROM page_aliases WHERE doc_id = ? ORDER BY created_at, slug",
            (doc_id,),
        )
    ]


def _children(con: sqlite3.Connection, doc_id: int) -> list[dict[str, Any]]:
    """The pages directly under this one in the tree, the viewer's to see."""
    rows = con.execute(
        "SELECT p.doc_id, p.slug, d.title FROM pages p"
        " JOIN documents d ON d.id = p.doc_id"
        " WHERE json_extract(d.meta, '$.page.parent') = ? ORDER BY p.slug",
        (doc_id,),
    ).fetchall()
    return [dict(r) for r in rows if not document_hidden(con, int(r["doc_id"]))]


def _page_node(con: sqlite3.Connection, doc_id: int) -> tuple[str, str]:
    """A page as a node of the graph: its title, and ``project`` or ``page``."""
    row = con.execute(
        "SELECT d.title, p.kind FROM pages p JOIN documents d ON d.id = p.doc_id"
        " WHERE p.doc_id = ?",
        (doc_id,),
    ).fetchone()
    return str(row["title"]), "project" if row["kind"] == "project" else "page"


def _project_edges(con: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    """The live ``part_of`` edges from a page to a project page, whoever
    wrote them (``write_page``, a project's member, a ``link`` by hand),
    each with the project page's id and slug. The project end is matched
    by its page's title and not its type: ``link`` to a project page's
    document id names it as a ``page`` (``document_node``), where the
    project's own writes name it a ``project``; the nlbank agent's three
    edges of 2026-11-03 were the first."""
    name, node_type = _page_node(con, doc_id)
    return con.execute(
        "SELECT x.id, x.source_doc, p.doc_id, p.slug, d.title FROM edges x"
        " JOIN entities s ON s.id = x.src JOIN entities t ON t.id = x.dst"
        " JOIN documents d ON d.title = t.name"
        " JOIN pages p ON p.doc_id = d.id AND p.kind = 'project'"
        " WHERE s.name = ? AND s.type = ? AND t.type IN ('project', 'page')"
        " AND x.rel = 'part_of' AND x.valid_to IS NULL ORDER BY x.id",
        (name, node_type),
    ).fetchall()


def _seen_project_edges(con: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    """``_project_edges`` the viewer may see: to a project page it sees, from
    a document it sees. A move by a restricted viewer leaves the others as
    they are (the review of 2026-11-03)."""
    hidden = hidden_documents(con)
    return [
        r
        for r in _project_edges(con, doc_id)
        if int(r["doc_id"]) not in hidden
        and (r["source_doc"] is None or int(r["source_doc"]) not in hidden)
    ]


def _projects_of(con: sqlite3.Connection, doc_id: int) -> list[dict[str, Any]]:
    """The projects a page is part of, the viewer's to see."""
    seen: dict[int, dict[str, Any]] = {}
    for r in _seen_project_edges(con, doc_id):
        if int(r["doc_id"]) not in seen:
            seen[int(r["doc_id"])] = {
                "doc_id": int(r["doc_id"]),
                "slug": r["slug"],
                "title": r["title"],
            }
    return list(seen.values())


PROJECT_MEMBERS = 500  # members a project page's read lists


def _members(con: sqlite3.Connection, title: str) -> dict[str, Any]:
    """What is part of a project: the documents and pages with a live
    ``part_of`` edge to it, the viewer's to see, ``PROJECT_MEMBERS`` at
    most, ``count`` all of them."""
    hidden = hidden_documents(con)
    rows = list(
        {
            (str(r["name"]), str(r["type"])): r
            for r in con.execute(
                "SELECT DISTINCT s.name, s.type, x.source_doc FROM edges x"
                " JOIN entities s ON s.id = x.src JOIN entities t ON t.id = x.dst"
                " WHERE x.rel = 'part_of' AND x.valid_to IS NULL"
                " AND t.name = ? AND t.type IN ('project', 'page') ORDER BY s.name",
                (title,),
            )
            # a fact a hidden document states is hidden with it, as in a walk
            if r["source_doc"] is None or int(r["source_doc"]) not in hidden
        }.values()
    )
    names = [str(r["name"]) for r in rows]
    first: dict[str, int] = {}
    for start in range(0, len(names), 500):
        part = names[start : start + 500]
        marks = ",".join("?" * len(part))
        for got, doc in con.execute(
            f"SELECT title, min(id) FROM documents WHERE title IN ({marks})"
            " GROUP BY title",
            part,
        ):
            first[str(got)] = int(doc)
    items = [
        {"title": str(r["name"]), "type": r["type"], "doc_id": first.get(r["name"])}
        for r in rows
        if first.get(r["name"]) not in hidden
    ]
    return {"count": len(items), "items": items[:PROJECT_MEMBERS]}


def _ancestors(con: sqlite3.Connection, doc_id: int) -> list[dict[str, Any]]:
    """The pages above this one, the top first: what a page's breadcrumb
    shows. A page the viewer may not see ends the line there."""
    out: list[dict[str, Any]] = []
    seen = {doc_id}
    above = _parent_of(con, doc_id)
    while above is not None and above not in seen and not document_hidden(con, above):
        seen.add(above)
        row = con.execute(
            "SELECT p.slug, d.title FROM pages p JOIN documents d ON d.id = p.doc_id"
            " WHERE p.doc_id = ?",
            (above,),
        ).fetchone()
        if row is None:
            break
        out.append({"doc_id": above, "slug": row["slug"], "title": row["title"]})
        above = _parent_of(con, above)
    return out[::-1]


PAGE_SOURCES = 200  # sources of one page looked at for staleness


def _annotated(con: sqlite3.Connection, doc_id: int) -> list[int]:
    """The documents a page annotates or synthesizes, by its edges: each
    target's title to the first document of that title, looked up a batch
    at a time. No index serves a title, and one lookup an edge was 48 ms
    on the live store: a page of 14,499 links took minutes to open
    (2026-10-29)."""
    names = [
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT t.name FROM edges x JOIN entities t ON t.id = x.dst"
            " WHERE x.source_doc = ? AND x.valid_to IS NULL"
            " AND x.rel IN ('annotates', 'synthesizes')",
            (doc_id,),
        )
    ]
    first: dict[str, int] = {}
    for start in range(0, len(names), 500):
        part = names[start : start + 500]
        marks = ",".join("?" * len(part))
        for title, got in con.execute(
            f"SELECT title, min(id) FROM documents WHERE title IN ({marks})"
            " GROUP BY title",
            part,
        ):
            first[str(title)] = int(got)
    return [first[n] for n in names if n in first]


def _lifecycle(con: sqlite3.Connection, doc_id: int, text: str) -> dict[str, Any]:
    """Whether what the page rests on still holds: ``lifecycle`` is
    ``stale`` when one of its sources (the documents its links name, and
    those it annotates or synthesizes) is no longer current
    (``staleness``: superseded, retired, invalid), else ``active``; and
    ``stale_sources`` names them with what replaced each. A page is never
    changed for it: the person or the agent decides what follows."""
    ids = [i for i in dict.fromkeys(linked_documents(text)) if i != doc_id]
    if len(ids) < PAGE_SOURCES:
        ids += [i for i in _annotated(con, doc_id) if i not in ids and i != doc_id]
    ids = ids[:PAGE_SOURCES]
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
        (_slug_of(con, slug), revision),
    ).fetchone()
    if row is None:
        raise KeyError(f"no revision {revision} of page {slug!r}")
    return _read_archive(row["text_hash"]).decode("utf-8")


@_reading
def list_pages(
    con: sqlite3.Connection, *, kind: str | None = None, limit: int = 2000
) -> list[dict[str, Any]]:
    """Pages, the last revised first, each with its ``parent`` (a page's
    document id, or None for a page at the top): what the Pages view draws
    as a tree."""
    where = "WHERE p.kind = ?" if kind else ""
    args: tuple[Any, ...] = (kind, limit) if kind else (limit,)
    rows = con.execute(
        f"""
        SELECT p.doc_id, p.slug, p.kind, d.title,
               (SELECT max(revision) FROM page_revisions r WHERE r.doc_id = p.doc_id)
                   AS revision,
               (SELECT author FROM page_revisions r WHERE r.doc_id = p.doc_id
                AND r.change = 'text' ORDER BY revision DESC LIMIT 1) AS author,
               (SELECT max(created_at) FROM page_revisions r WHERE r.doc_id = p.doc_id)
                   AS updated_at,
               json_extract(d.meta, '$.page.parent') AS parent,
               json_extract(d.meta, '$.source') AS source
        FROM pages p JOIN documents d ON d.id = p.doc_id {where}
        ORDER BY updated_at DESC LIMIT ?
        """,
        args,
    ).fetchall()
    hidden = hidden_documents(con)
    return [dict(r) for r in rows if int(r["doc_id"]) not in hidden]


@_serialized
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
    parent: str | None = None,
    force: bool = False,
    sensitivity: str | None = None,
    gid: str | None = None,
    expected_text_hash: str | None = None,
) -> dict[str, Any]:
    """Create or replace a page's text as a new revision.

    A slug the page had before a rename writes to that page, never to a
    new one. ``expected_text_hash`` is the text hash the writer read
    (``get_page``): a page whose text changed since is refused
    (PermissionError), so a writer that reassembles a long page from
    windows never writes over a revision it did not see.

    What can refuse the write (a document to annotate, a project, a parent
    that is not there, a parent that would make a cycle) is checked before
    anything is written, and a page asked to be ``personal`` is marked so
    before its text is indexed: a refused write leaves no page, and never
    an open one (the review of 2026-10-18).

    ``parent`` names, by slug, the page this one sits under in the tree of
    pages (``set_page_parent``); without it a ``part_of`` project is the
    parent, when the page has none yet.

    ``annotates`` names documents this page is about (``page --annotates-->
    paper`` edges, the page as source document); ``part_of`` names a
    project page by slug (``page --part_of--> project``). An ``agent``
    revision over a ``human`` one is refused unless ``force``; use
    ``append_page`` for agent additions. ``gid`` is a new page's global id
    when it has one already: a page imported from another library keeps the
    one it was minted with there (migration 64); else the database mints it.
    Returns ``{doc_id, slug, revision, created, text_hash, text_len}``.
    """
    if kind not in PAGE_KINDS:
        raise ValueError(f"kind must be one of {PAGE_KINDS}")
    if author not in PAGE_AUTHORS:
        raise ValueError(f"author must be one of {PAGE_AUTHORS}")
    asked = slugify(slug)
    slug = _slug_of(con, asked)
    existing = con.execute(
        "SELECT p.doc_id, p.kind, d.title, d.text_hash FROM pages p"
        " JOIN documents d ON d.id = p.doc_id"
        " WHERE p.slug = ?",
        (slug,),
    ).fetchone()
    if existing is not None and document_hidden(con, int(existing["doc_id"])):
        # a restricted viewer writes to nothing it may not see: as if absent,
        # under the name it was asked by (not the one an old slug leads to)
        raise KeyError(f"no page {asked!r}")
    if expected_text_hash is not None and expected_text_hash != (
        existing["text_hash"] if existing is not None else ""
    ):
        raise PermissionError(
            f"page {slug!r} changed since it was read: its text hash is not"
            f" {expected_text_hash}; read it again"
        )
    _refuse_write(
        con,
        None if existing is None else int(existing["doc_id"]),
        annotates=annotates,
        part_of=part_of,
        parent=parent,
        sensitivity=sensitivity,
    )
    created = existing is None
    if created:
        doc_id, revision = _new_page(con, slug, text, title=title, kind=kind, gid=gid)
    else:
        doc_id, kind = int(existing["doc_id"]), existing["kind"]
        revision = _next_revision(con, existing, slug, author=author, force=force)
        if title and title != existing["title"]:
            _rename_page(con, doc_id, existing["title"], title, kind)
    if sensitivity is not None:
        set_sensitivity(con, doc_id, sensitivity, by=author)
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
    page = (page_title, page_type)
    for target in annotates or []:
        _annotate(con, page, source_rel, target, doc_id, slug, revision, kind)
    _link_edges(
        con, doc_id, slug, revision, text, page_title, page_type, source_rel, annotates
    )
    if part_of:
        _link_project(con, page, part_of, doc_id, slug, revision)
    under = parent or (part_of if _parent_of(con, doc_id) is None else None)
    if under:
        above = page_id(con, under)
        if above is None:
            raise KeyError(f"no page {under!r}")
        if above != doc_id:
            set_page_parent(con, doc_id, above, by=author)
    con.commit()
    return {
        "doc_id": doc_id,
        "slug": slug,
        "revision": revision,
        "created": created,
        "text_hash": indexed["text_hash"],
        "text_len": int(
            con.execute(
                "SELECT text_len FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()[0]
            or 0
        ),
    }


def _new_page(
    con: sqlite3.Connection,
    slug: str,
    text: str,
    *,
    title: str | None,
    kind: str,
    gid: str | None = None,
) -> tuple[int, int]:
    """A new page's document row and its ``pages`` row; its revision is 1."""
    title = title or slug.replace("-", " ").capitalize()
    reg = register(
        con,
        _page_original(slug, text),
        mime="text/markdown",
        title=title,
        meta={"source": "wiki", "page": {"slug": slug, "kind": kind}},
    )
    doc_id = int(reg["doc_id"])
    con.execute(
        "INSERT INTO pages (doc_id, slug, kind, gid) VALUES (?, ?, ?, ?)",
        (doc_id, slug, kind, gid),
    )
    return doc_id, 1


def _next_revision(
    con: sqlite3.Connection,
    existing: sqlite3.Row,
    slug: str,
    *,
    author: str,
    force: bool,
) -> int:
    """The revision a write over an existing page makes; an agent's over a
    person's text is refused unless ``force``. Whose the text is, is the
    last revision that wrote text: a move or a rename writes no one's."""
    last = con.execute(
        "SELECT author FROM page_revisions WHERE doc_id = ? AND change = 'text'"
        " ORDER BY revision DESC LIMIT 1",
        (existing["doc_id"],),
    ).fetchone()
    if last and last["author"] == "human" and author == "agent" and not force:
        raise PermissionError(
            f"page {slug!r} was last written by a person; append_page adds"
            " a section, force=True overwrites"
        )
    return _revision_after(con, int(existing["doc_id"]))


def _revision_after(con: sqlite3.Connection, doc_id: int) -> int:
    top = con.execute(
        "SELECT max(revision) FROM page_revisions WHERE doc_id = ?", (doc_id,)
    ).fetchone()[0]
    return int(top or 0) + 1


def _rename_page(
    con: sqlite3.Connection, doc_id: int, old: str, title: str, kind: str
) -> None:
    """A rename: the page's entity in the graph goes with it, so its edges
    stay its own (merged into a namesake if one exists)."""
    con.execute("UPDATE documents SET title = ? WHERE id = ?", (title, doc_id))
    page_type = "project" if kind == "project" else "page"
    row = con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (old, page_type)
    ).fetchone()
    if row is not None:
        rename_entity(con, row["id"], title)


def _page_link(
    con: sqlite3.Connection, edge: Edge, doc_id: int, slug: str, revision: int
) -> None:
    """One of a page's own edges, written once."""
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


def _annotate(
    con: sqlite3.Connection,
    page: tuple[str, str],
    rel: str,
    target: int,
    doc_id: int,
    slug: str,
    revision: int,
    kind: str,
) -> None:
    """The page's edge (``page``: its name and type) to a document it
    annotates or draws on (``rel``)."""
    try:
        # one node per document, whatever links it (``document_node``)
        target_name, target_type = document_node(con, target)
    except KeyError:
        raise KeyError(f"no such document to annotate: {target}") from None
    edge = Edge(*page, rel, target_name, target_type)
    if kind == "synthesis" and target_type != "page":
        _set_promote(con, target, by="page", reason=f"source of synthesis {slug}")
    _page_link(con, edge, doc_id, slug, revision)


def _link_project(
    con: sqlite3.Connection,
    page: tuple[str, str],
    part_of: str,
    doc_id: int,
    slug: str,
    revision: int,
) -> None:
    project = con.execute(
        "SELECT d.title FROM pages p JOIN documents d ON d.id = p.doc_id"
        " WHERE p.slug = ? AND p.kind = 'project'",
        (slugify(part_of),),
    ).fetchone()
    if project is None:
        raise KeyError(f"no project page {part_of!r}")
    edge = Edge(*page, "part_of", project[0], "project")
    _page_link(con, edge, doc_id, slug, revision)


def _refuse_write(
    con: sqlite3.Connection,
    doc_id: int | None,
    *,
    annotates: list[int] | None,
    part_of: str | None,
    parent: str | None,
    sensitivity: str | None,
) -> None:
    """What would refuse a page's write (``write_page``), raised before
    anything is written: a sensitivity that is not ``personal``, a
    document to annotate or a project that is not there, a parent that is
    not there or would make a cycle (``doc_id`` None: a new page)."""
    if sensitivity not in (None, "personal"):
        raise ValueError("sensitivity on a write is personal, or none")
    for target in annotates or []:
        try:
            document_node(con, target)
        except KeyError:
            raise KeyError(f"no such document to annotate: {target}") from None
    if part_of and (
        con.execute(
            "SELECT 1 FROM pages WHERE slug = ? AND kind = 'project'",
            (slugify(part_of),),
        ).fetchone()
        is None
    ):
        raise KeyError(f"no project page {part_of!r}")
    if parent:
        above = page_id(con, parent)
        if above is None or document_hidden(con, above):
            raise KeyError(f"no page {parent!r}")
        if doc_id is not None and above != doc_id:
            _refuse_cycle(con, doc_id, above)


def _refuse_cycle(con: sqlite3.Connection, doc_id: int, parent: int) -> None:
    """ValueError when ``parent`` sits under ``doc_id`` (or is it)."""
    seen: set[int] = set()
    above: int | None = parent
    while above is not None and above not in seen:
        if above == doc_id:
            raise ValueError("that page sits under this one: a cycle")
        seen.add(above)
        above = _parent_of(con, above)


def _parent_of(con: sqlite3.Connection, doc_id: int) -> int | None:
    row = con.execute(
        "SELECT json_extract(meta, '$.page.parent') FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


@_reading
def page_id(con: sqlite3.Connection, slug: str) -> int | None:
    """The document id of the page at ``slug``, or None."""
    row = con.execute(
        "SELECT doc_id FROM pages WHERE slug = ?", (_slug_of(con, slugify(slug)),)
    ).fetchone()
    return int(row[0]) if row else None


@_reading
def page_subtree(con: sqlite3.Connection, doc_id: int) -> list[int]:
    """A page and every page under it in the tree of pages, the viewer's
    to see; empty for a page that is not there."""
    kids: dict[int, list[int]] = {}
    for child, parent in con.execute(
        "SELECT p.doc_id, json_extract(d.meta, '$.page.parent')"
        " FROM pages p JOIN documents d ON d.id = p.doc_id"
    ):
        if parent is not None:
            kids.setdefault(int(parent), []).append(int(child))
    if con.execute(
        "SELECT 1 FROM pages WHERE doc_id = ?", (doc_id,)
    ).fetchone() is None or document_hidden(con, doc_id):
        return []
    out, todo, seen = [], [doc_id], {doc_id}
    while todo:
        here = todo.pop()
        out.append(here)
        for k in kids.get(here, []):
            if k not in seen and not document_hidden(con, k):
                seen.add(k)
                todo.append(k)
    return out


@_reading
def scope_documents(
    con: sqlite3.Connection, *, page: int | None = None, personal: str | None = None
) -> frozenset[int] | None:
    """The documents a view of the graph is drawn from, or None for all of
    them. ``page``: that page, the pages under it in the tree of pages,
    and the documents they link (``[title](#doc/N)``); a project's page so
    gives its notes and the papers they cite. ``personal``: ``hide`` leaves
    out the documents marked or suspected personal, ``only`` keeps them
    alone (``documents.sensitivity``). The two narrow each other."""
    out: frozenset[int] | None = None
    if page is not None:
        docs: set[int] = set()
        for did in page_subtree(con, page):
            docs.add(did)
            row = con.execute(
                "SELECT text_hash FROM documents WHERE id = ?", (did,)
            ).fetchone()
            if row is not None and row[0]:
                docs.update(linked_documents(_read_archive(row[0]).decode("utf-8")))
        out = frozenset(docs)
    if personal in ("hide", "only"):
        marked = {
            int(r[0])
            for r in con.execute(
                f"SELECT id FROM documents d WHERE {personal_sql('d')}"
            )
        }
        if personal == "only":
            out = frozenset(marked) if out is None else out & marked
        elif out is not None:
            out = out - marked
        else:
            out = (
                frozenset(int(r[0]) for r in con.execute("SELECT id FROM documents"))
                - marked
            )
    elif personal not in (None, "", "all"):
        raise ValueError(f"personal: hide, only or all, not {personal!r}")
    return out


@_serialized
def set_page_parent(
    con: sqlite3.Connection, doc_id: int, parent: int | None, *, by: str = "human"
) -> dict[str, Any]:
    """Put a page under another in the tree of pages, or at the top
    (``parent`` None): ``meta.page.parent`` and who put it there
    (``parent_by``). Both must be pages the viewer may see, and a page may
    not sit under itself or under one of the pages below it (ValueError).
    One parent a page: a page that belongs in two places sits under one
    and is linked from the other."""
    if con.execute(
        "SELECT 1 FROM pages WHERE doc_id = ?", (doc_id,)
    ).fetchone() is None or document_hidden(con, doc_id):
        raise KeyError(f"no such page: {doc_id}")
    if parent is not None:
        if con.execute(
            "SELECT 1 FROM pages WHERE doc_id = ?", (parent,)
        ).fetchone() is None or document_hidden(con, parent):
            raise KeyError(f"no such page: {parent}")
        _refuse_cycle(con, doc_id, parent)
    meta = get_meta(con, doc_id)
    page = dict(meta.get("page") or {})
    if parent is None:
        page.pop("parent", None)
        page.pop("parent_by", None)
    else:
        page["parent"] = parent
        page["parent_by"] = by
    meta["page"] = page
    set_meta(con, doc_id, meta)
    con.commit()
    return {"doc_id": doc_id, "parent": parent}


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
    return _sized(
        write_page(
            con,
            page["slug"],
            text,
            author=author,
            note=note,
            force=True,
            kind=page["kind"],
            annotates=annotates,
        )
    )


def _sized(written: dict[str, Any]) -> dict[str, Any]:
    """A write's answer, with ``large`` when the page has grown past
    ``PAGE_LARGE``: what it holds, and that a split would serve it."""
    if int(written.get("text_len") or 0) <= PAGE_LARGE:
        return written
    return {
        **written,
        "large": (
            f"the page holds {written['text_len']:,} characters, past"
            f" {PAGE_LARGE:,}: an index page with a child page a part"
            " (move_page puts a page under another) keeps each one readable"
        ),
    }


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
            " AND r.author = 'human' AND r.change = 'text'",
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
    return _sized({**written, "section": "replaced"})


# what a move or a rename writes about itself in the page's history and
# the evidence of its edges
_MOVED = "moved"


@_serialized
def move_page(
    con: sqlite3.Connection,
    slug: str,
    *,
    part_of: str | None = None,
    parent: str | None = None,
    author: str = "agent",
    note: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move a page to another project, another place in the tree of pages,
    or both, without touching its text.

    ``part_of`` names the project page it is then part of, by slug, and
    ``""`` none; ``parent`` the page it sits under, and ``""`` the top;
    None leaves either as it is. A page's projects become exactly the one
    named: its other live ``part_of`` edges to a project are ended
    (``valid_to``, so a walk at an earlier moment still finds them), and
    whoever wrote them, ``link`` by hand included, and the project end is
    known by its page's title whatever type its node has. When a project
    is named and no parent, a page that sat under another project's page
    (by an edge or only in the tree) moves under the named one's, and so
    does a page at the top that changes project.

    The move is a revision of its own (``change`` ``move``) with the text
    hash of the one before, the note and the author, and is allowed on a
    page a person wrote: it writes no one's words. Refused before anything
    is written: a page, project or parent that is not there (KeyError), a
    parent under the page itself or not in the page's project (ValueError).
    A move to where the page is changes nothing and says so (``changed``
    false); ``dry_run`` says what would change and writes nothing.
    """
    if author not in PAGE_AUTHORS:
        raise ValueError(f"author must be one of {PAGE_AUTHORS}")
    doc_id = page_id(con, slug)
    if doc_id is None or document_hidden(con, doc_id):
        raise KeyError(f"no page {slug!r}")
    current_slug = str(
        con.execute("SELECT slug FROM pages WHERE doc_id = ?", (doc_id,)).fetchone()[0]
    )
    edges = _seen_project_edges(con, doc_id)
    before_ids = list(dict.fromkeys(int(r["doc_id"]) for r in edges))
    before = [str(_slug_at(con, i)) for i in before_ids]
    above = _parent_of(con, doc_id)
    project: sqlite3.Row | None = None
    if part_of:
        project = con.execute(
            "SELECT p.doc_id, p.slug, d.title FROM pages p"
            " JOIN documents d ON d.id = p.doc_id"
            " WHERE p.slug = ? AND p.kind = 'project'",
            (_slug_of(con, slugify(part_of)),),
        ).fetchone()
        if project is None or document_hidden(con, int(project["doc_id"])):
            raise KeyError(f"no project page {part_of!r}")
        if int(project["doc_id"]) == doc_id:
            raise ValueError("a project is not part of itself")
    after = before if part_of is None else ([str(project["slug"])] if project else [])
    if parent is not None:
        under = page_id(con, parent) if parent else None
        if parent and (under is None or document_hidden(con, under)):
            raise KeyError(f"no page {parent!r}")
    elif part_of is not None and (
        (above is None and after != before)
        or (
            above is not None
            and _is_project_page(con, above)
            and above != (int(project["doc_id"]) if project is not None else None)
        )
    ):
        # the page sat under a project's page (by an edge, or only in the
        # tree) or at the top: it goes under the project it is part of now
        under = int(project["doc_id"]) if project is not None else None
    else:
        under = above
    if under is not None:
        if under == doc_id:
            raise ValueError("a page does not sit under itself")
        _refuse_cycle(con, doc_id, under)
        if after:
            _refuse_elsewhere(con, under, after[0], doc_id)
    plan = {
        "doc_id": doc_id,
        "slug": current_slug,
        "part_of": {"from": before, "to": after},
        "parent": {"from": _slug_shown(con, above), "to": _slug_shown(con, under)},
    }
    if after == before and under == above:
        return {**plan, "changed": False, "why": "the page is there already"}
    if dry_run:
        return {**plan, "changed": True, "dry_run": True}
    revision = _revision_after(con, doc_id)
    name, node_type = _page_node(con, doc_id)
    ended: list[int] = []
    kept = False  # one live edge to the project stays; any other ends
    target = int(project["doc_id"]) if project is not None else None
    for r in edges if part_of is not None else []:
        if int(r["doc_id"]) == target and not kept:
            kept = True
            continue
        invalidate_edge(con, int(r["id"]))
        ended.append(int(r["id"]))
    if project is not None and not kept:
        link(
            con,
            Edge(name, node_type, "part_of", str(project["title"]), "project"),
            confidence="EXTRACTED",
            source_doc=doc_id,
            evidence=f"page {current_slug} {_MOVED}, revision {revision}",
            producer="page",
            run=f"{current_slug}@{revision}",
        )
    if under != above:
        set_page_parent(con, doc_id, under, by=author)
    # the note is read by everyone who sees the page, whoever moved it
    after_ids = before_ids if part_of is None else ([target] if target else [])
    said = note or _move_note(
        {
            "part_of": {
                "from": [_slug_for_all(con, i) for i in before_ids],
                "to": [_slug_for_all(con, i) for i in after_ids],
            },
            "parent": {
                "from": _slug_for_all(con, above),
                "to": _slug_for_all(con, under),
            },
        }
    )
    _bookkeeping_revision(con, doc_id, revision, author, said, "move")
    con.commit()
    return {**plan, "changed": True, "revision": revision, "ended_edges": ended}


def _is_project_page(con: sqlite3.Connection, doc_id: int) -> bool:
    row = con.execute("SELECT kind FROM pages WHERE doc_id = ?", (doc_id,)).fetchone()
    return row is not None and row[0] == "project"


def _slug_at(con: sqlite3.Connection, doc_id: int | None) -> str | None:
    if doc_id is None:
        return None
    row = con.execute("SELECT slug FROM pages WHERE doc_id = ?", (doc_id,)).fetchone()
    return str(row[0]) if row else None


NOT_SHOWN: Final = "(a page not shown)"


def _slug_shown(con: sqlite3.Connection, doc_id: int | None) -> str | None:
    """A page's slug for the viewer: a page it may not see is not named."""
    if doc_id is not None and document_hidden(con, doc_id):
        return NOT_SHOWN
    return _slug_at(con, doc_id)


def _slug_for_all(con: sqlite3.Connection, doc_id: int | None) -> str | None:
    """A page's slug for what every reader of another page is shown (a
    move's note): a personal page is not named, whoever writes it."""
    if doc_id is None:
        return None
    row = con.execute(
        f"SELECT {personal_sql('d')} FROM documents d WHERE d.id = ?", (doc_id,)
    ).fetchone()
    return NOT_SHOWN if row is not None and row[0] else _slug_at(con, doc_id)


def _refuse_elsewhere(
    con: sqlite3.Connection, under: int, project_slug: str, doc_id: int
) -> None:
    """ValueError when the page a page is to sit under is not in the page's
    project: neither the project's page, nor under it, nor part of it."""
    project = page_id(con, project_slug)
    if under == project:
        return
    seen: set[int] = {doc_id}
    at: int | None = under
    while at is not None and at not in seen:
        if at == project or project_slug in (
            str(r["slug"]) for r in _project_edges(con, at)
        ):
            return
        seen.add(at)
        at = _parent_of(con, at)
    raise ValueError(
        f"page {_slug_at(con, under)!r} is not in project {project_slug!r}:"
        " a page sits under its project's page or a page of its project"
    )


def _move_note(plan: dict[str, Any]) -> str:
    parts = []
    if plan["part_of"]["from"] != plan["part_of"]["to"]:
        was = ", ".join(plan["part_of"]["from"]) or "no project"
        now = ", ".join(plan["part_of"]["to"]) or "no project"
        parts.append(f"project {was} -> {now}")
    if plan["parent"]["from"] != plan["parent"]["to"]:
        parts.append(
            f"under {plan['parent']['from'] or 'the top'}"
            f" -> {plan['parent']['to'] or 'the top'}"
        )
    return f"{_MOVED}: " + "; ".join(parts)


def _bookkeeping_revision(
    con: sqlite3.Connection,
    doc_id: int,
    revision: int,
    author: str,
    note: str,
    change: str,
) -> None:
    """A revision that changed no text: the text hash it had, a new number."""
    text_hash = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()[0]
    con.execute(
        "INSERT INTO page_revisions (doc_id, revision, text_hash, author, note, change)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, revision, text_hash, author, note, change),
    )
    meta = get_meta(con, doc_id)
    meta["page"] = {**(meta.get("page") or {}), "revision": revision}
    set_meta(con, doc_id, meta)


@_serialized
def rename_page(
    con: sqlite3.Connection,
    slug: str,
    new_slug: str,
    *,
    author: str = "agent",
    note: str | None = None,
) -> dict[str, Any]:
    """Give a page a new slug. The old one stays its name for ever
    (``page_aliases``): a read, an append or a write under it reaches the
    page, and no other page takes it. The document, its id, its text and
    its edges are unchanged; the rename is a revision of its own
    (``change`` ``rename``). Refused: a page that is not there (KeyError),
    a slug another page has or had, an empty one, and a project's page,
    whose slug is its project's name (ValueError). A rename to the slug
    the page has changes nothing; one back to an old slug of its own
    takes that alias out."""
    if author not in PAGE_AUTHORS:
        raise ValueError(f"author must be one of {PAGE_AUTHORS}")
    doc_id = page_id(con, slug)
    if doc_id is None or document_hidden(con, doc_id):
        raise KeyError(f"no page {slug!r}")
    row = con.execute(
        "SELECT slug, kind FROM pages WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    if not any(c.isalnum() for c in new_slug):
        raise ValueError("a page needs a slug: letters or digits")
    old, new = str(row["slug"]), slugify(new_slug)
    if new == old:
        return {"doc_id": doc_id, "slug": old, "changed": False}
    if row["kind"] == "project":
        raise ValueError(f"{old!r} is a project's page: its slug is the project's name")
    holder = con.execute("SELECT doc_id FROM pages WHERE slug = ?", (new,)).fetchone()
    other = con.execute(
        "SELECT doc_id FROM page_aliases WHERE slug = ?", (new,)
    ).fetchone()
    for taken in (holder, other):
        # a page the viewer may not see: which of the two, it is not told
        if (
            taken is not None
            and int(taken[0]) != doc_id
            and document_hidden(con, int(taken[0]))
        ):
            raise ValueError(f"{new!r} cannot be used: choose another slug")
    if holder is not None:
        raise ValueError(f"page {new!r} exists")
    if other is not None and int(other[0]) != doc_id:
        raise ValueError(f"{new!r} is an old name of another page")
    con.execute("DELETE FROM page_aliases WHERE slug = ?", (new,))
    con.execute("INSERT INTO page_aliases (slug, doc_id) VALUES (?, ?)", (old, doc_id))
    con.execute("UPDATE pages SET slug = ? WHERE doc_id = ?", (new, doc_id))
    meta = get_meta(con, doc_id)
    meta["page"] = {**(meta.get("page") or {}), "slug": new}
    set_meta(con, doc_id, meta)
    revision = _revision_after(con, doc_id)
    _bookkeeping_revision(
        con, doc_id, revision, author, note or f"renamed: {old} -> {new}", "rename"
    )
    con.commit()
    return {
        "doc_id": doc_id,
        "slug": new,
        "was": old,
        "changed": True,
        "revision": revision,
        "aliases": _aliases(con, doc_id),
    }


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
