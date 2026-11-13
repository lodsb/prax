"""Which document an entity of a document type is (migration 69): kept on
the entity when an edge first reaches it (``_claim_node``) by one rule
(``_node_doc``), and migration 69's frozen upgrade of the nodes written
before it."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from prax.graph import ontology
from prax.text import mimes

from .common import LIVE, marks

# ---------------------------------------------------- a document's node
# Which document an entity of a document type is (migration 69): kept on
# the entity when an edge first reaches it, and read back by
# ``node_document``, in place of a lookup by title at each reader. Here
# because the migration's upgrade links the nodes written before it.


def media_node_name(title: str, doc_id: int) -> str:
    """A picture's or a recording's name in the graph: its file and its
    number (``document_node``)."""
    return f"{title} (#{doc_id})"


_MEDIA_NODE = re.compile(r" \(#(\d+)\)$")


def node_name_of(row: sqlite3.Row | dict[str, Any], doc_id: int) -> tuple[str, bool]:
    """A document's node name from its ``title``, ``mime`` and ``text_hash``,
    and whether it is a picture's or a recording's (``media_node_name``):
    one with no text of its own is named by its file and its number."""
    title = str(row["title"])
    if not row["text_hash"] and (
        mimes.is_picture(row["mime"]) or mimes.is_recording(row["mime"])
    ):
        return media_node_name(title, doc_id), True
    return title, False


def _node_doc(
    con: sqlite3.Connection,
    name: str,
    *,
    entity_id: int | None = None,
    source_doc: int | None = None,
) -> int | None:
    """The document a node of a document type called ``name`` is, by one
    rule: ``source_doc`` when that is its node name (a document's own
    facts); a picture's or a recording's by the number in its name; else,
    of the documents with that title, one whose facts the entity already
    carries, else the live one of the lowest id, else the lowest id. None
    when no document has the name."""
    if source_doc is not None:
        row = con.execute(
            "SELECT title, mime, text_hash FROM documents WHERE id = ?",
            (source_doc,),
        ).fetchone()
        if (
            row is not None
            and row["title"]
            and node_name_of(row, source_doc)[0] == (name)
        ):
            return source_doc
    media = _MEDIA_NODE.search(name)
    if media:
        doc = int(media.group(1))
        row = con.execute(
            "SELECT title, mime, text_hash FROM documents WHERE id = ?", (doc,)
        ).fetchone()
        if row is not None and row["title"] and node_name_of(row, doc) == (name, True):
            return doc
    titled = [
        (int(r[0]), bool(r[1]))
        for r in con.execute(
            f"SELECT id, {LIVE} FROM documents WHERE title = ? ORDER BY id", (name,)
        )
    ]
    if len(titled) > 1 and entity_id is not None:
        ids = [i for i, _ in titled]
        own = con.execute(
            f"SELECT min(source_doc) FROM edges WHERE src = ?"
            f" AND source_doc IN ({marks(len(ids))})",
            (entity_id, *ids),
        ).fetchone()[0]
        if own is not None:
            return int(own)
    live_ones = [i for i, alive in titled if alive]
    either = live_ones or [i for i, _ in titled]
    return either[0] if either else None


def _claim_node(
    con: sqlite3.Connection,
    entity_id: int,
    etype: str,
    name: str,
    source_doc: int | None = None,
    onto: ontology.Ontology | None = None,
) -> None:
    """Link an entity of a document type to its document (``_node_doc``)
    when it has no document yet; what ``_link`` does for both ends of an
    edge. An entity of another type is no document's. ``onto`` is the
    ontology the caller holds: ``ontology.current()`` stats every module
    file, and two more calls an edge made each link 55-80% slower (the
    quality review of 2026-11-11, findings 11 and 32). Does not commit."""
    onto = onto or ontology.current()
    if etype not in onto.entity_types or not onto.is_a(etype, "document"):
        return
    row = con.execute(
        "SELECT doc_id FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None or row[0] is not None:
        return
    doc = _node_doc(con, name, entity_id=entity_id, source_doc=source_doc)
    if doc is not None:
        con.execute("UPDATE entities SET doc_id = ? WHERE id = ?", (doc, entity_id))


def _migration_69_nodes(con: sqlite3.Connection) -> int:
    """Migration 69's Python half, frozen as it was on 2026-11-13 like the
    migration's SQL (the quality review of 2026-11-11, C2): every entity of
    a document type with no document yet gets the one its name gives, by
    ``_node_doc``'s rule of that day written out here, so a later change to
    the living rule changes no store's upgrade. The heal's
    ``unlinked-document-nodes`` applies the living rule after it. Returns
    how many it linked; does not commit."""
    onto = ontology.current()  # which types are documents is the ontology's
    types = [t for t in onto.entity_types if onto.is_a(t, "document")]
    if not types:
        return 0
    live = "json_extract(meta, '$.retired') IS NULL"  # frozen, not LIVE

    def media_name(doc: int) -> str | None:
        row = con.execute(
            "SELECT title, mime, text_hash FROM documents WHERE id = ?", (doc,)
        ).fetchone()
        if row is None or not row["title"] or row["text_hash"]:
            return None
        mime = (row["mime"] or "").lower()
        picture = mime.startswith("image/") and mime != "image/vnd.djvu"
        if picture or mime.startswith(("audio/", "video/")):
            return f"{row['title']} (#{doc})"
        return None

    rows = con.execute(
        f"SELECT id, name FROM entities WHERE doc_id IS NULL"
        f" AND type IN ({marks(len(types))})",
        types,
    ).fetchall()
    n = 0
    for r in rows:
        name, entity = str(r["name"]), int(r["id"])
        doc: int | None = None
        media = re.search(r" \(#(\d+)\)$", name)
        if media and media_name(int(media.group(1))) == name:
            doc = int(media.group(1))
        if doc is None:
            titled = [
                (int(t[0]), bool(t[1]))
                for t in con.execute(
                    f"SELECT id, {live} FROM documents WHERE title = ? ORDER BY id",
                    (name,),
                )
            ]
            own = None
            if len(titled) > 1:
                ids = [i for i, _ in titled]
                own = con.execute(
                    f"SELECT min(source_doc) FROM edges WHERE src = ?"
                    f" AND source_doc IN ({marks(len(ids))})",
                    (entity, *ids),
                ).fetchone()[0]
            if own is not None:
                doc = int(own)
            else:
                either = [i for i, alive in titled if alive] or [i for i, _ in titled]
                doc = either[0] if either else None
        if doc is not None:
            con.execute("UPDATE entities SET doc_id = ? WHERE id = ?", (doc, entity))
            n += 1
    return n
