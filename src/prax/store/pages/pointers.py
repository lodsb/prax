"""Pointers (``prax:<slug>``, plan item AU step 3): what names a page, or
a project's file or journal entry, and outlives a sync, which retires the
document a changed file was (invariant 2). The grammar is
``prax.text.slugs``; here it is resolved against the store: a page by its
slug, a file or an entry by the project's synced documents
(``project_documents``), the current text of each. A page's text links
with one as it does with ``#doc/N`` (``page_links``)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Final

from prax.text import markup, slugs

from ..base import (
    LIVE,
    NotFound,
    _reading,
    document_hidden,
    meta_of,
)
from .texts import _slug_of, linked_documents

PROJECT_SOURCE: Final = "project"  # ``meta.source`` of a synced document


@_reading
def project_documents(
    con: sqlite3.Connection, name: str, keys: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """A project's live synced documents by key: ``{key: {doc_id, version,
    path, entry, question}}`` (``entry``, a journal entry's or a register
    question's key, else None; ``question``, a question's state), those
    its name carries and those any of ``keys`` names (a document synced
    before the manifest, under an older key)."""
    keys = keys or []
    marks = ",".join("?" * len(keys)) or "NULL"
    rows = con.execute(
        "SELECT id, json_extract(meta, '$.project.key') AS key,"
        " json_extract(meta, '$.project.version') AS version,"
        " json_extract(meta, '$.project.path') AS path,"
        " json_extract(meta, '$.project.entry') AS entry,"
        " json_extract(meta, '$.project.question') AS question FROM documents"
        " WHERE json_extract(meta, '$.source') = ?"
        f" AND {LIVE}"
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
                "entry": r["entry"],
                "question": json.loads(r["question"]) if r["question"] else None,
            }
    return out


# ------------------------------------------------------------- pointers


@_reading
def resolve_pointer(con: sqlite3.Connection, ref: str) -> dict[str, Any]:
    """What a pointer names now (``prax.text.slugs``): ``{pointer, doc_id,
    title, kind}``, kind ``page``, ``file`` or ``entry``. A page by its slug
    (an old slug finds it too); a project's file by its project and path,
    the text the last sync left current; a journal entry by its key, its
    newest version. ``NotFound`` when nothing current answers to it or the
    viewer may not see it; a ValueError when ``ref`` is no pointer."""
    got = slugs.parse_pointer(ref)
    if got is None:
        raise ValueError(
            f"not a pointer: {ref!r} (prax:<page>, prax:<project>/<path>,"
            " prax:<project>/<path>#<entry>)"
        )
    if got.page is not None:
        page = con.execute(
            "SELECT p.doc_id, p.slug, d.title FROM pages p"
            " JOIN documents d ON d.id = p.doc_id WHERE p.slug = ?",
            (_slug_of(con, got.page),),
        ).fetchone()
        if page is None or document_hidden(con, int(page["doc_id"])):
            raise NotFound(f"no such page: {got.page}")
        return {
            "pointer": slugs.page_pointer(str(page["slug"])),
            "doc_id": int(page["doc_id"]),
            "title": page["title"],
            "kind": "page",
        }
    for (name,) in con.execute("SELECT name FROM projects ORDER BY name"):
        if slugs.slug(str(name)) != got.project:
            continue
        for v in project_documents(con, str(name)).values():
            entry = slugs.slug(str(v["entry"])) if v.get("entry") else None
            if v.get("path") != got.path or entry != got.entry:
                continue
            doc_id = int(v["doc_id"])
            if document_hidden(con, doc_id):
                break
            row = con.execute(
                "SELECT title FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
            return {
                "pointer": slugs.pointer(str(name), str(v["path"]), v.get("entry")),
                "doc_id": doc_id,
                "title": row[0] if row else None,
                "kind": "entry" if entry else "file",
            }
    raise NotFound(f"nothing current answers to {ref}")


@_reading
def pointer_of(con: sqlite3.Connection, doc_id: int) -> str | None:
    """The pointer that names a document: a page's, or a project file's or
    journal entry's (an earlier text of one too, whose pointer now finds the
    newest); None for any other document, which only its id names."""
    page = con.execute("SELECT slug FROM pages WHERE doc_id = ?", (doc_id,)).fetchone()
    if page is not None:
        return slugs.page_pointer(str(page[0]))
    row = con.execute("SELECT meta FROM documents WHERE id = ?", (doc_id,)).fetchone()
    held = meta_of(row[0]).get(PROJECT_SOURCE) if row else None
    if not isinstance(held, dict) or not held.get("name") or not held.get("path"):
        return None
    return slugs.pointer(str(held["name"]), str(held["path"]), held.get("entry"))


def page_links(con: sqlite3.Connection, text: str) -> list[int]:
    """The library documents a page's text links to, in order of first
    mention: ``[title](#doc/N)`` and ``[title](prax:<slug>)``, a pointer
    as the document it names now; one that names nothing is left out."""
    out = linked_documents(text)
    for m in markup.POINTER_LINK.finditer(text or ""):
        try:
            got = int(resolve_pointer(con, m.group(1))["doc_id"])
        except (NotFound, ValueError):
            continue
        if got not in out:
            out.append(got)
    return out
