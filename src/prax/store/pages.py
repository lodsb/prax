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
from typing import Any

from .base import _read_archive, _serialized
from .documents import _set_promote, get_meta, index_text, register, set_meta
from .graph import Edge, find_edges, link

# Living Markdown documents (migration 0006): notes on a document, ongoing
# projects, topic pages. A page is a document, so everything that applies
# to documents applies; its identity is the slug, every save is a new text
# artifact with an append-only revision row, and its relationships to other
# documents are edges. An agent never overwrites human text: ``write_page``
# refuses an agent revision over a human one unless told to; ``append_page``
# adds a section instead.

PAGE_KINDS = ("addendum", "project", "synthesis", "topic")


PAGE_AUTHORS = ("human", "agent")


_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    slug = _SLUG_CHARS.sub("-", text.lower()).strip("-")
    return slug[:80] or "page"


def _page_original(slug: str, text: str) -> bytes:
    """The archived original of a page: the first revision with an identity
    line, so two pages with the same opening text stay distinct documents."""
    return f"<!-- prax page: {slug} -->\n{text}".encode()


@_serialized
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


@_serialized
def get_page(con: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    """A page with its current text and revision list, or None."""
    row = con.execute(
        "SELECT p.doc_id, p.slug, p.kind, d.title, d.text_hash, d.meta"
        " FROM pages p JOIN documents d ON d.id = p.doc_id WHERE p.slug = ?",
        (slug,),
    ).fetchone()
    if row is None:
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
    }


@_serialized
def page_revision_text(con: sqlite3.Connection, slug: str, revision: int) -> str:
    row = con.execute(
        "SELECT r.text_hash FROM page_revisions r JOIN pages p ON p.doc_id = r.doc_id"
        " WHERE p.slug = ? AND r.revision = ?",
        (slug, revision),
    ).fetchone()
    if row is None:
        raise KeyError(f"no revision {revision} of page {slug!r}")
    return _read_archive(row["text_hash"]).decode("utf-8")


@_serialized
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
        if title:
            con.execute("UPDATE documents SET title = ? WHERE id = ?", (title, doc_id))
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
    # a synthesis draws on its sources; any other page annotates one document
    source_rel = "synthesizes" if kind == "synthesis" else "annotates"
    for target in annotates or []:
        t = con.execute(
            "SELECT title FROM documents WHERE id = ?", (target,)
        ).fetchone()
        if t is None or not t[0]:
            raise KeyError(f"no such document to annotate: {target}")
        target_type = (
            "page"
            if con.execute("SELECT 1 FROM pages WHERE doc_id = ?", (target,)).fetchone()
            else "paper"
        )
        edge = Edge(page_title, page_type, source_rel, t[0], target_type)
        if kind == "synthesis" and target_type == "paper":
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


@_serialized
def add_to_project(
    con: sqlite3.Connection, project_slug: str, doc_id: int
) -> int | None:
    """``paper --part_of--> project`` for a library document; the edge id,
    or None when it already exists."""
    project = con.execute(
        "SELECT p.doc_id, d.title FROM pages p JOIN documents d ON d.id = p.doc_id"
        " WHERE p.slug = ? AND p.kind = 'project'",
        (slugify(project_slug),),
    ).fetchone()
    if project is None:
        raise KeyError(f"no project page {project_slug!r}")
    doc = con.execute("SELECT title FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if doc is None or not doc[0]:
        raise KeyError(f"no such document: {doc_id}")
    is_page = con.execute("SELECT 1 FROM pages WHERE doc_id = ?", (doc_id,)).fetchone()
    edge = Edge(
        doc[0], "page" if is_page else "paper", "part_of", project["title"], "project"
    )
    if find_edges(con, edge):
        return None
    if not is_page:
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
