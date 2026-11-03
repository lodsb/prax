"""Where a page sits: its parent and children in the tree of pages, the
projects it is part of, a project's members, and what it rests on."""

from __future__ import annotations

import sqlite3
from typing import Any, Final

from ..base import (
    _read_archive,
    _reading,
    document_hidden,
    hidden_documents,
    personal_sql,
)
from ..graph import (
    _doc_ids_by_title,
)
from ..retrieval import staleness
from .texts import (
    _slug_of,
    linked_documents,
    slugify,
)


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
    first = _doc_ids_by_title(con, [str(r["name"]) for r in rows])
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
    at a time: one lookup an edge was 48 ms on the live store before
    migration 68 indexed the titles, and a page of 14,499 links took
    minutes to open (2026-10-29)."""
    names = [
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT t.name FROM edges x JOIN entities t ON t.id = x.dst"
            " WHERE x.source_doc = ? AND x.valid_to IS NULL"
            " AND x.rel IN ('annotates', 'synthesizes')",
            (doc_id,),
        )
    ]
    first = _doc_ids_by_title(con, names)
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
