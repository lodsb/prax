"""A page moved or renamed without a change to its text: a revision of its
own (``page_revisions.change``), the old slug kept for ever."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..base import (
    NotFound,
    _committing,
    _serialized,
    document_hidden,
)
from ..documents import (
    _set_meta,
    get_meta,
)
from ..graph import (
    Edge,
    _invalidate_edge,
    _link,
)
from .texts import (
    PAGE_AUTHORS,
    _slug_of,
    slugify,
)
from .tree import (
    _aliases,
    _is_project_page,
    _page_node,
    _parent_of,
    _refuse_cycle,
    _refuse_elsewhere,
    _seen_project_edges,
    _slug_at,
    _slug_for_all,
    _slug_shown,
    page_id,
)
from .writes import (
    _revision_after,
    _set_page_parent,
)

# what a move or a rename writes about itself in the page's history and
# the evidence of its edges
_MOVED = "moved"


def _move_page(
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
        raise NotFound(f"no page {slug!r}")
    current_slug = str(
        con.execute("SELECT slug FROM pages WHERE doc_id = ?", (doc_id,)).fetchone()[0]
    )
    edges = _seen_project_edges(con, doc_id)
    before_ids = list(dict.fromkeys(int(r["doc_id"]) for r in edges))
    before = [str(_slug_at(con, i)) for i in before_ids]
    above = _parent_of(con, doc_id)
    project = _project_named(con, part_of, doc_id) if part_of else None
    after = before if part_of is None else ([str(project["slug"])] if project else [])
    under = _destination(
        con,
        doc_id,
        parent=parent,
        moving=part_of is not None,
        project=project,
        above=above,
        changed=after != before,
    )
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
    target = int(project["doc_id"]) if project is not None else None
    ended = (
        _rehome(con, doc_id, edges, project, f"{current_slug}@{revision}")
        if part_of is not None
        else []
    )
    if under != above:
        _set_page_parent(con, doc_id, under, by=author)
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
    return {**plan, "changed": True, "revision": revision, "ended_edges": ended}


move_page = _serialized(_committing(_move_page))


def _project_named(con: sqlite3.Connection, part_of: str, doc_id: int) -> sqlite3.Row:
    """The project page a move names, refused when it is not there, is
    hidden from the mover, or is the page itself."""
    project: sqlite3.Row | None = con.execute(
        "SELECT p.doc_id, p.slug, d.title FROM pages p"
        " JOIN documents d ON d.id = p.doc_id"
        " WHERE p.slug = ? AND p.kind = 'project'",
        (_slug_of(con, slugify(part_of)),),
    ).fetchone()
    if project is None or document_hidden(con, int(project["doc_id"])):
        raise NotFound(f"no project page {part_of!r}")
    if int(project["doc_id"]) == doc_id:
        raise ValueError("a project is not part of itself")
    return project


def _destination(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    parent: str | None,
    moving: bool,
    project: sqlite3.Row | None,
    above: int | None,
    changed: bool,
) -> int | None:
    """The page a moved page sits under: the ``parent`` named (None for
    the top); else, when its project changes (``moving``), the new
    project's page for a page that sat at the top or under another
    project's page; else where it sits (``above``)."""
    if parent is not None:
        under = page_id(con, parent) if parent else None
        if parent and (under is None or document_hidden(con, under)):
            raise NotFound(f"no page {parent!r}")
        return under
    target = int(project["doc_id"]) if project is not None else None
    if moving and (
        (above is None and changed)
        or (above is not None and _is_project_page(con, above) and above != target)
    ):
        # the page sat under a project's page (by an edge, or only in the
        # tree) or at the top: it goes under the project it is part of now
        return target
    return above


def _rehome(
    con: sqlite3.Connection,
    doc_id: int,
    edges: list[sqlite3.Row],
    project: sqlite3.Row | None,
    run: str,
) -> list[int]:
    """A moved page's ``part_of`` edges made the one to ``project``: one
    live edge to it stays, any other ends, and one is linked when none was
    there. Returns the ended edges' ids."""
    name, node_type = _page_node(con, doc_id)
    ended: list[int] = []
    kept = False
    target = int(project["doc_id"]) if project is not None else None
    for r in edges:
        if int(r["doc_id"]) == target and not kept:
            kept = True
            continue
        _invalidate_edge(con, int(r["id"]), run=run)
        ended.append(int(r["id"]))
    if project is not None and not kept:
        slug, revision = run.rsplit("@", 1)
        _link(
            con,
            Edge(name, node_type, "part_of", str(project["title"]), "project"),
            confidence="EXTRACTED",
            source_doc=doc_id,
            evidence=f"page {slug} {_MOVED}, revision {revision}",
            producer="page",
            run=run,
        )
    return ended


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
    _set_meta(con, doc_id, meta)


def _rename_slug(
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
        raise NotFound(f"no page {slug!r}")
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
    _set_meta(con, doc_id, meta)
    revision = _revision_after(con, doc_id)
    _bookkeeping_revision(
        con, doc_id, revision, author, note or f"renamed: {old} -> {new}", "rename"
    )
    return {
        "doc_id": doc_id,
        "slug": new,
        "was": old,
        "changed": True,
        "revision": revision,
        "aliases": _aliases(con, doc_id),
    }


rename_page = _serialized(_committing(_rename_slug))
