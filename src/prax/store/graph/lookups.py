"""The small reads the door's steps and routes make of the graph and the
documents: an entity's labels, the one answering to a name, a document's
identities, the documents of hashes or entities, a page's slugs, and
the global ids of entities and pages (docs/global-ids.md)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..base import _reading


@_reading
def labels_of_entities(
    con: sqlite3.Connection, names: list[tuple[str, str]]
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Each ``(name, type)`` entity's labels other than its own name: what
    else it is called, and in which language."""
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for name, etype in names:
        rows = con.execute(
            "SELECT l.label, l.lang, l.kind FROM entities e"
            " JOIN entity_labels l ON l.entity_id = e.id"
            " WHERE e.name = ? AND e.type = ? AND e.canonical_id IS NULL"
            " AND l.label != e.name AND l.was = 0 ORDER BY l.label, l.lang",
            (name, etype),
        ).fetchall()
        if rows:
            out[(name, etype)] = [
                {"label": r["label"], "lang": r["lang"], "kind": r["kind"]}
                for r in rows
            ]
    return out


IDENTITY_META = ("doi", "arxiv", "isbn", "zotero", "domains", "tags", "lang")


@_reading
def document_identities(
    con: sqlite3.Connection, doc_ids: list[int]
) -> list[dict[str, Any]]:
    """What another library needs to recognise these documents without
    their bytes: the hash of the original, the title, the URL, the type,
    the ids in ``meta`` (``IDENTITY_META``) and the summary. By hash."""
    out = []
    for start in range(0, len(doc_ids), 500):
        part = doc_ids[start : start + 500]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            "SELECT id, hash, title, source_url, mime, meta FROM documents"
            f" WHERE id IN ({marks})",
            part,
        ):
            meta = json.loads(r["meta"] or "{}")
            kept = {k: meta[k] for k in IDENTITY_META if meta.get(k)}
            if meta.get("summary"):
                kept["summary"] = meta["summary"]
            out.append(
                {
                    "id": r["id"],
                    "hash": r["hash"],
                    "title": r["title"],
                    "source_url": r["source_url"],
                    "mime": r["mime"],
                    "meta": kept,
                }
            )
    return sorted(out, key=lambda d: d["hash"])


@_reading
def entity_answering(con: sqlite3.Connection, name: str, etype: str) -> int | None:
    """The canonical entity of this type that answers to ``name`` (its own
    name, or a label only one entity of the type carries), or None. The
    lookup ``store.link`` makes, without making one."""
    row = con.execute(
        "SELECT COALESCE(canonical_id, id) FROM entities WHERE name = ? AND type = ?",
        (name, etype),
    ).fetchone()
    if row is not None:
        return int(row[0])
    known = con.execute(
        "SELECT DISTINCT e.id FROM entity_labels l JOIN entities e"
        " ON e.id = l.entity_id WHERE l.label = ? COLLATE NOCASE"
        " AND e.type = ? AND e.canonical_id IS NULL LIMIT 2",
        (name, etype),
    ).fetchall()
    return int(known[0][0]) if len(known) == 1 else None


@_reading
def documents_by_hash(con: sqlite3.Connection, hashes: list[str]) -> dict[str, int]:
    """This library's document for each of these original hashes it holds."""
    out: dict[str, int] = {}
    for start in range(0, len(hashes), 500):
        part = hashes[start : start + 500]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            f"SELECT hash, id FROM documents WHERE hash IN ({marks})", part
        ):
            out[str(r["hash"])] = int(r["id"])
    return out


@_reading
def entities_by_gid(con: sqlite3.Connection, gids: list[str]) -> dict[str, int]:
    """This library's entity for each of these global ids it minted, the
    canonical one: a folded row keeps its gid, so a twin's id is an alias
    (migration 64)."""
    out: dict[str, int] = {}
    for start in range(0, len(gids), 500):
        part = gids[start : start + 500]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            f"SELECT gid, COALESCE(canonical_id, id) AS id FROM entities"
            f" WHERE gid IN ({marks})",
            part,
        ):
            out[str(r["gid"])] = int(r["id"])
    return out


@_reading
def gids_of_entities(con: sqlite3.Connection, ids: list[int]) -> dict[int, str]:
    """The global id of each of these entities, as the row carries it."""
    out: dict[int, str] = {}
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            f"SELECT id, gid FROM entities WHERE id IN ({marks})", part
        ):
            out[int(r["id"])] = str(r["gid"])
    return out


@_reading
def folded_gids(con: sqlite3.Connection, gids: list[str]) -> dict[str, list[str]]:
    """The aliases another library may know each of these entities by: the
    global ids of the rows folded into it, and those of other libraries'
    entities an import decided are it (``foreign_entities``), so a file
    that goes back lands on the rows it came from."""
    out: dict[str, set[str]] = {}
    for start in range(0, len(gids), 500):
        part = gids[start : start + 500]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            f"SELECT c.gid AS keep, e.gid AS twin FROM entities c"
            f" JOIN entities e ON e.canonical_id = c.id WHERE c.gid IN ({marks})"
            f" UNION SELECT c.gid, f.gid FROM entities c"
            f" JOIN foreign_entities f ON f.entity_id = c.id"
            f" WHERE c.gid IN ({marks}) AND f.state IN ('same', 'new')",
            part + part,
        ):
            out.setdefault(str(r["keep"]), set()).add(str(r["twin"]))
    return {k: sorted(v) for k, v in out.items()}


@_reading
def page_by_gid(con: sqlite3.Connection, gid: str) -> int | None:
    """The page document of this global id, or None."""
    row = con.execute("SELECT doc_id FROM pages WHERE gid = ?", (gid,)).fetchone()
    return int(row["doc_id"]) if row is not None else None


@_reading
def page_slugs_of(con: sqlite3.Connection, doc_ids: list[int]) -> list[str]:
    """The slugs of those documents that are pages."""
    out: list[str] = []
    for start in range(0, len(doc_ids), 500):
        part = doc_ids[start : start + 500]
        marks = ",".join("?" * len(part))
        out.extend(
            str(r[0])
            for r in con.execute(
                f"SELECT slug FROM pages WHERE doc_id IN ({marks})", part
            )
        )
    return sorted(out)


@_reading
def entity_named_in(con: sqlite3.Connection, entity_id: int) -> str:
    """The title of a document that has a live edge about the entity."""
    row = con.execute(
        "SELECT d.title FROM edges x JOIN documents d ON d.id = x.source_doc"
        " WHERE x.valid_to IS NULL AND (x.src = ? OR x.dst = ?)"
        " AND d.title IS NOT NULL LIMIT 1",
        (entity_id, entity_id),
    ).fetchone()
    return str(row["title"]) if row else ""


@_reading
def entities_of_documents(con: sqlite3.Connection, doc_ids: list[int]) -> list[int]:
    """The entities the live edges of these documents point at."""
    if not doc_ids:
        return []
    marks = ",".join("?" * len(doc_ids))
    return [
        int(r[0])
        for r in con.execute(
            f"SELECT DISTINCT dst FROM edges WHERE source_doc IN ({marks})"
            " AND valid_to IS NULL",
            tuple(doc_ids),
        )
    ]


@_reading
def documents_sharing(
    con: sqlite3.Connection, entity_ids: list[int], *, after: int, at_least: int
) -> list[tuple[int, int]]:
    """``(doc_id, n)`` for the documents above id ``after`` whose live edges
    point at ``at_least`` of these entities."""
    if not entity_ids:
        return []
    marks = ",".join("?" * len(entity_ids))
    return [
        (int(r["source_doc"]), int(r["n"]))
        for r in con.execute(
            f"SELECT source_doc, count(DISTINCT dst) AS n FROM edges"
            f" WHERE dst IN ({marks}) AND valid_to IS NULL AND source_doc > ?"
            " GROUP BY source_doc HAVING n >= ?",
            (*entity_ids, after, at_least),
        )
    ]


@_reading
def documents_as_pages(
    con: sqlite3.Connection, doc_ids: list[int]
) -> list[tuple[int, str, str | None]]:
    """``(doc_id, title, page kind)``, the kind None for a document that is
    not one of prax's pages."""
    if not doc_ids:
        return []
    marks = ",".join("?" * len(doc_ids))
    return [
        (int(r["id"]), str(r["title"] or ""), r["kind"])
        for r in con.execute(
            f"SELECT d.id, d.title, p.kind FROM documents d LEFT JOIN pages p"
            f" ON p.doc_id = d.id WHERE d.id IN ({marks})",
            tuple(doc_ids),
        )
    ]


@_reading
def own_type(con: sqlite3.Connection, doc_id: int, title: str) -> str | None:
    """The type the graph gave a document itself: the newest live edge of
    the document whose source entity carries its title."""
    row = con.execute(
        "SELECT s.type FROM edges e JOIN entities s ON s.id = e.src"
        " WHERE e.source_doc = ? AND s.name = ? AND e.valid_to IS NULL"
        " ORDER BY e.id DESC LIMIT 1",
        (doc_id, title),
    ).fetchone()
    return str(row[0]) if row else None


@_reading
def described_devices(con: sqlite3.Connection, doc_id: int, title: str) -> list[str]:
    """The devices a document ``describes`` by its own live edges."""
    return [
        str(x[0])
        for x in con.execute(
            "SELECT DISTINCT t.name FROM edges e"
            " JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
            " WHERE e.source_doc = ? AND e.rel = 'describes' AND s.name = ?"
            " AND t.type = 'device' AND e.valid_to IS NULL",
            (doc_id, title),
        )
    ]
