"""Writing a page's text: a revision, an append, a section, the blocks'
interiors, its place under another page and in a project."""

from __future__ import annotations

import sqlite3
from typing import Any

from prax.text import blocks, markup

from ..base import (
    DocumentMeta,
    NotFound,
    _committing,
    _read_archive,
    _serialized,
    document_hidden,
)
from ..documents import (
    _index_text,
    _register,
    _set_meta,
    _set_promote,
    _set_sensitivity,
    get_meta,
    update_meta,
)
from ..graph import (
    Edge,
    _invalidate_edge,
    _link,
    document_node,
    find_edges,
    link,
    rename_entity,
)
from .pointers import page_links
from .reads import (
    get_page,
)
from .texts import (
    _LINK_EVIDENCE,
    PAGE_AUTHORS,
    PAGE_KINDS,
    PAGE_LARGE,
    _page_original,
    _slug_of,
    slugify,
)
from .tree import (
    _parent_of,
    _refuse_cycle,
    page_id,
)


def _write_page(
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
        raise NotFound(f"no page {asked!r}")
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
        _set_sensitivity(con, doc_id, sensitivity, by=author)
    indexed = _index_text(con, doc_id, text, text_source=f"page/{author}")
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
    _set_meta(con, doc_id, meta)
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
            raise NotFound(f"no page {under!r}")
        if above != doc_id:
            _set_page_parent(con, doc_id, above, by=author)
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


write_page = _serialized(_committing(_write_page))


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
    reg = _register(
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
    stay its own (merged into a namesake if one exists). The one write of
    ``_write_page`` that still commits on its own: ``rename_entity`` commits
    a merge in batches, so a retitled page is not taken back whole. Every
    node of the page under the old name goes, whatever type a write gave
    it (``entities.doc_id``)."""
    con.execute("UPDATE documents SET title = ? WHERE id = ?", (title, doc_id))
    for (entity,) in con.execute(
        "SELECT id FROM entities WHERE doc_id = ? AND name = ?"
        " AND canonical_id IS NULL ORDER BY id",
        (doc_id, old),
    ).fetchall():
        rename_entity(con, int(entity), title)


def _page_link(
    con: sqlite3.Connection, edge: Edge, doc_id: int, slug: str, revision: int
) -> None:
    """One of a page's own edges, written once."""
    if not find_edges(con, edge):
        _link(
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
    except NotFound:
        raise NotFound(f"no such document to annotate: {target}") from None
    edge = Edge(*page, rel, target_name, target_type)
    if kind == "synthesis" and target_type not in ("page", "project"):
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
        "SELECT doc_id FROM pages WHERE slug = ? AND kind = 'project'",
        (slugify(part_of),),
    ).fetchone()
    if project is None:
        raise NotFound(f"no project page {part_of!r}")
    edge = Edge(*page, "part_of", *document_node(con, int(project[0])))
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
        except NotFound:
            raise NotFound(f"no such document to annotate: {target}") from None
    if part_of and (
        con.execute(
            "SELECT 1 FROM pages WHERE slug = ? AND kind = 'project'",
            (slugify(part_of),),
        ).fetchone()
        is None
    ):
        raise NotFound(f"no project page {part_of!r}")
    if parent:
        above = page_id(con, parent)
        if above is None or document_hidden(con, above):
            raise NotFound(f"no page {parent!r}")
        if doc_id is not None and above != doc_id:
            _refuse_cycle(con, doc_id, above)


def _set_page_parent(
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
        raise NotFound(f"no such page: {doc_id}")
    if parent is not None:
        if con.execute(
            "SELECT 1 FROM pages WHERE doc_id = ?", (parent,)
        ).fetchone() is None or document_hidden(con, parent):
            raise NotFound(f"no such page: {parent}")
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
    _set_meta(con, doc_id, meta)
    return {"doc_id": doc_id, "parent": parent}


set_page_parent = _serialized(_committing(_set_page_parent))


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
    for target in page_links(con, text):
        if target == doc_id or target in named:
            continue
        try:
            target_name, target_type = document_node(con, target)
        except NotFound:
            continue
        wanted_titles.add(target_name)
        edge = Edge(page_title, page_type, rel, target_name, target_type)
        if not find_edges(con, edge):
            _link(
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
            _invalidate_edge(con, row["id"], run=f"{slug}@{revision}")


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
        raise NotFound(f"no page {slug!r}")
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
        raise NotFound(f"no page {slug!r}")
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
            f" {PAGE_LARGE:,}: split_page cuts it at its headings into an"
            " index page and a child page a part, each one readable"
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
        raise NotFound(f"no page {slug!r}")
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
        raise NotFound(f"no project page {project_slug!r}")
    name, node_type = document_node(con, doc_id)
    is_page = node_type in ("page", "project")
    edge = Edge(name, node_type, "part_of", *document_node(con, project["doc_id"]))
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


@_serialized
def mark_open_answer(con: sqlite3.Connection, doc_id: int) -> None:
    """Mark a page as holding an open answer of ``ask`` (``meta.page.open``):
    the model's own knowledge beside the library's passages. A later
    search still finds the page; ``ask`` and the surfer never take it as
    the library's evidence. The mark stays with the page's revisions."""

    def mark(meta: DocumentMeta) -> None:
        meta["page"] = {**(meta.get("page") or {}), "open": True}

    update_meta(con, doc_id, mark)
