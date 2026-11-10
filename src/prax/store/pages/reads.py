"""Reading pages back: one page whole or in windows, a revision, the list."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..base import (
    NotFound,
    _read_archive,
    _reading,
    document_hidden,
    hidden_documents,
    meta_of,
)
from .texts import (
    _slug_of,
    page_blocks,
)
from .tree import (
    _aliases,
    _ancestors,
    _children,
    _lifecycle,
    _members,
    _projects_of,
)


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
    meta = meta_of(row["meta"])
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
        out["members"] = _members(con, doc_id)
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


@_reading
def page_revision_text(con: sqlite3.Connection, slug: str, revision: int) -> str:
    row = con.execute(
        "SELECT r.text_hash FROM page_revisions r JOIN pages p ON p.doc_id = r.doc_id"
        " WHERE p.slug = ? AND r.revision = ?",
        (_slug_of(con, slug), revision),
    ).fetchone()
    if row is None:
        raise NotFound(f"no revision {revision} of page {slug!r}")
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
