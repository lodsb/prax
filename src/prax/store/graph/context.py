"""What the graph says about a document, for ask and the document page:
its facts, its context (what it links to and what links to it, the
similar documents) and the documents its title's entities reach."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from prax.graph import ontology

from ..base import (
    NotFound,
    _guards,
    _reading,
    _scrubbed,
    hidden_documents,
    meta_of,
)
from ..retrieval import CONTEXT_LIMIT, _similar_documents
from .edges import doc_node_ids


def _docs_by_zotero_key(con: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, title, json_extract(meta, '$.zotero.kind') AS kind FROM documents
        WHERE EXISTS (SELECT 1 FROM json_each(meta, '$.zotero.keys') WHERE value = ?)
           OR EXISTS (SELECT 1 FROM json_each(meta, '$.zotero.items') WHERE value = ?)
           OR json_extract(meta, '$.zotero.parent') = ?
        ORDER BY id
        """,
        (key, key, key),
    ).fetchall()
    return [dict(r) for r in rows]


ASK_FACT_RELS_SKIPPED = ("cites",)  # dozens per paper; the passages carry them
_CONFIDENCE_BY_RANK = ("EXTRACTED", "INFERRED", "AMBIGUOUS")


@_reading
def document_facts(
    con: sqlite3.Connection, doc_ids: list[int], *, limit: int = 8
) -> dict[int, list[dict[str, Any]]]:
    """What the graph records about each document, as its own edges: the
    relations from the document's entity, ``{doc_id: [{rel, name, type}]}``,
    at most ``limit`` per document, canonical entity names, ``cites``
    left out. The "what the library knows" part of an ask bundle."""
    hidden = hidden_documents(con)
    doc_ids = [i for i in doc_ids if i not in hidden]
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in doc_ids}
    if not doc_ids:
        return out
    marks = ",".join("?" * len(doc_ids))
    skip = ",".join("?" * len(ASK_FACT_RELS_SKIPPED))
    rows = con.execute(
        f"""
        SELECT x.source_doc AS doc_id, x.rel, t.name, t.type FROM edges x
        JOIN documents d ON d.id = x.source_doc
        JOIN entities s ON s.id = x.src
        JOIN entities t0 ON t0.id = x.dst
        JOIN entities t ON t.id = COALESCE(t0.canonical_id, t0.id)
        WHERE x.source_doc IN ({marks}) AND x.valid_to IS NULL
          AND s.doc_id = x.source_doc AND x.rel NOT IN ({skip})
        ORDER BY x.source_doc, x.rel, t.name
        """,
        (*doc_ids, *ASK_FACT_RELS_SKIPPED),
    )
    seen: set[tuple[int, str, str]] = set()
    for r in rows:
        key = (r["doc_id"], r["rel"], r["name"])
        if key in seen or len(out[r["doc_id"]]) >= limit:
            continue
        seen.add(key)
        out[r["doc_id"]].append({"rel": r["rel"], "name": r["name"], "type": r["type"]})
    return out


@_guards("doc", lambda: None)
@_scrubbed
@_reading
def document_context(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    limit: int = CONTEXT_LIMIT,
    domain: str | None = None,
) -> dict[str, Any] | None:
    """Everything that places a document in the library, for its page: the
    extraction summary and entities, citations in and out of the library,
    the nearest documents by vector, documents sharing its entities or its
    authors, and its Zotero neighbours (parent item, siblings, collections,
    tags). None when the document does not exist."""
    doc = con.execute(
        "SELECT id, title, meta FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if doc is None:
        return None
    title = doc["title"] or ""
    meta = meta_of(doc["meta"])
    # the entities that are this document, of whatever document type: a
    # picture's node is its file and number, a recipe is no paper
    try:
        node = doc_node_ids(con, doc_id)
    except NotFound:  # no title yet, so no node
        node = []
    at_node = ",".join("?" * len(node))

    # the document's own edges, by the entity they point at
    entities: list[dict[str, Any]] = []
    author_ids: list[int] = []
    entity_ids: list[int] = []
    for r in con.execute(
        f"""
        SELECT x.rel, x.confidence, t.id AS tid, t.name, t.type FROM edges x
        JOIN entities t ON t.id = x.dst
        WHERE x.source_doc = ? AND x.valid_to IS NULL AND x.src IN ({at_node})
        ORDER BY t.type, t.name
        """,
        (doc_id, *node),
    ):
        if r["rel"] == "authored_by":
            author_ids.append(r["tid"])
            continue
        if r["rel"] in ("cites", "published_in"):
            continue
        entity_ids.append(r["tid"])
        entities.append(
            {
                "name": r["name"],
                "type": r["type"],
                "rel": r["rel"],
                "confidence": r["confidence"],
            }
        )

    # citations: what this document cites, and what cites it (source_doc is
    # the citing document); a cited node resolved to the library document it
    # is (``entities.doc_id``) where there is one
    # each with the surest confidence any producer gave it: Crossref's
    # and a printed id's edges are EXTRACTED, a title match INFERRED
    # (the score in its evidence), a tie between twins AMBIGUOUS
    cites_rows = con.execute(
        f"""
        SELECT t.name, min(t.doc_id) AS doc_id,
               min(CASE x.confidence WHEN 'EXTRACTED' THEN 0
                   WHEN 'INFERRED' THEN 1 ELSE 2 END) AS rank
        FROM edges x JOIN entities t ON t.id = x.dst
        WHERE x.rel = 'cites' AND x.valid_to IS NULL AND x.src IN ({at_node})
        GROUP BY t.name ORDER BY t.name
        """,
        node,
    ).fetchall()
    cites = sorted(
        (
            {
                "title": r["name"],
                "doc_id": r["doc_id"],
                "confidence": _CONFIDENCE_BY_RANK[r["rank"]],
            }
            for r in cites_rows
        ),
        key=lambda c: (c["doc_id"] is None, c["title"].lower()),
    )
    cited_by = [
        {
            "doc_id": r["doc_id"],
            "title": r["title"],
            "confidence": _CONFIDENCE_BY_RANK[r["rank"]],
        }
        for r in con.execute(
            f"""
            SELECT x.source_doc AS doc_id, d.title,
                   min(CASE x.confidence WHEN 'EXTRACTED' THEN 0
                       WHEN 'INFERRED' THEN 1 ELSE 2 END) AS rank
            FROM edges x JOIN documents d ON d.id = x.source_doc
            WHERE x.rel = 'cites' AND x.valid_to IS NULL AND x.dst IN ({at_node})
              AND x.source_doc != ?
            GROUP BY x.source_doc, d.title ORDER BY d.title
            """,
            (*node, doc_id),
        )
    ]

    # documents sharing this one's entities, most shared first
    shared: list[dict[str, Any]] = []
    if entity_ids:
        marks = ",".join("?" * len(entity_ids))
        for r in con.execute(
            f"""
            SELECT x.source_doc AS doc_id, d.title, count(DISTINCT x.dst) AS n,
                   group_concat(DISTINCT t.name) AS names
            FROM edges x JOIN entities t ON t.id = x.dst
            JOIN documents d ON d.id = x.source_doc
            WHERE x.dst IN ({marks}) AND x.valid_to IS NULL AND x.source_doc != ?
            GROUP BY x.source_doc ORDER BY n DESC, d.title LIMIT ?
            """,
            (*entity_ids, doc_id, limit),
        ):
            shared.append(
                {
                    "doc_id": r["doc_id"],
                    "title": r["title"],
                    "count": r["n"],
                    "entities": (r["names"] or "").split(",")[:4],
                }
            )

    # other documents by the same authors
    same_authors: list[dict[str, Any]] = []
    if author_ids:
        marks = ",".join("?" * len(author_ids))
        for r in con.execute(
            f"""
            SELECT x.source_doc AS doc_id, d.title,
                   group_concat(DISTINCT a.name) AS authors
            FROM edges x JOIN entities a ON a.id = x.dst
            JOIN documents d ON d.id = x.source_doc
            WHERE x.rel = 'authored_by' AND x.dst IN ({marks}) AND x.valid_to IS NULL
              AND x.source_doc != ?
            GROUP BY x.source_doc ORDER BY count(*) DESC, d.title LIMIT ?
            """,
            (*author_ids, doc_id, limit),
        ):
            same_authors.append(
                {
                    "doc_id": r["doc_id"],
                    "title": r["title"],
                    "authors": (r["authors"] or "").split(","),
                }
            )

    # Zotero neighbours
    z = meta.get("zotero") or {}
    parent = None
    if z.get("parent"):
        found = [d for d in _docs_by_zotero_key(con, z["parent"]) if d["id"] != doc_id]
        parent = {
            "key": z["parent"],
            "doc_id": found[0]["id"] if found else None,
            "title": found[0]["title"] if found else None,
        }
    siblings: list[dict[str, Any]] = []
    seen = {doc_id}
    for key in list(z.get("items") or []) + ([z["parent"]] if z.get("parent") else []):
        for d in _docs_by_zotero_key(con, key):
            if d["id"] not in seen:
                seen.add(d["id"])
                siblings.append(
                    {"doc_id": d["id"], "title": d["title"], "kind": d["kind"]}
                )

    # pages: notes written about this document, and a project's members
    notes = [
        dict(r)
        for r in con.execute(
            f"""
            SELECT DISTINCT x.source_doc AS doc_id, d.title, p.slug, p.kind
            FROM edges x
            JOIN documents d ON d.id = x.source_doc JOIN pages p ON p.doc_id = d.id
            WHERE x.rel = 'annotates' AND x.valid_to IS NULL AND x.dst IN ({at_node})
              AND x.source_doc != ?
            ORDER BY d.title
            """,
            (*node, doc_id),
        )
    ]
    members: list[dict[str, Any]] = []
    page_row = con.execute(
        "SELECT slug, kind FROM pages WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    if page_row and page_row["kind"] == "project":
        members = [
            dict(r)
            for r in con.execute(
                f"""
                SELECT DISTINCT s.name AS title, s.type, s.doc_id
                FROM edges x JOIN entities s ON s.id = x.src
                WHERE x.rel = 'part_of' AND x.valid_to IS NULL AND x.dst IN ({at_node})
                ORDER BY s.name
                """,
                node,
            )
        ]
    return {
        "doc_id": doc_id,
        "title": title,
        "page": dict(page_row) if page_row else None,
        "notes": notes,
        "members": members,
        "summary": meta.get("summary") or "",
        "extraction": meta.get("extraction"),
        "citations": meta.get("citations"),
        "entities": entities,
        "cites": cites,
        "cited_by": cited_by,
        "linked": _linked_documents(con, node, limit=limit),
        "similar": _similar_documents(con, doc_id, limit=limit, domain=domain),
        "shared": shared,
        "same_authors": same_authors,
        "zotero": {
            "parent": parent,
            "siblings": siblings[:limit],
            "collections": meta.get("collections") or [],
            "tags": meta.get("tags") or [],
        },
    }


def _linked_documents(
    con: sqlite3.Connection, node: list[int], *, limit: int
) -> dict[str, list[dict[str, Any]]]:
    """A document's edges to and from other documents (``links_to``,
    ``supersedes``, ``invalidates``, ``part_of``…; ``cites`` has its own
    lists): ``out`` where it is the subject, ``in`` where it is the object,
    each with the other document's id where the library holds it and the
    evidence. At most ``limit`` a side."""
    onto = ontology.current()
    out: dict[str, list[dict[str, Any]]] = {"out": [], "in": []}
    # the live edges at either end of the document's node, through their
    # indexes. Joining every live edge to its entities to keep the
    # title's took 383 ms a document page, most of a context (2026-10-18)
    rows = con.execute(
        """
        WITH ends(id) AS (SELECT value FROM json_each(?)),
        touching(id) AS (
            SELECT id FROM edges WHERE valid_to IS NULL AND src IN ends
             UNION SELECT id FROM edges WHERE valid_to IS NULL AND dst IN ends)
        SELECT x.rel, x.evidence, x.source_doc, s.id AS sid, s.name AS src,
               s.type AS src_type, t.id AS tid, t.name AS dst, t.type AS dst_type
        FROM edges x JOIN entities s0 ON s0.id = x.src
        JOIN entities s ON s.id = COALESCE(s0.canonical_id, s0.id)
        JOIN entities t0 ON t0.id = x.dst
        JOIN entities t ON t.id = COALESCE(t0.canonical_id, t0.id)
        WHERE x.id IN touching AND x.rel != 'cites'
        ORDER BY x.rel, x.id
        """,
        (json.dumps(node),),
    ).fetchall()
    mine = set(node)
    seen: set[tuple[str, str, str]] = set()
    for r in rows:
        side = "out" if r["sid"] in mine else "in"
        other, name, etype = (
            (r["tid"], r["dst"], r["dst_type"])
            if side == "out"
            else (r["sid"], r["src"], r["src_type"])
        )
        if other in mine or etype not in onto.entity_types:
            continue
        if not onto.is_a(etype, "document") or len(out[side]) >= limit:
            continue
        if (side, r["rel"], name) in seen:
            continue
        seen.add((side, r["rel"], name))
        other = con.execute(
            "SELECT doc_id FROM entities WHERE id = ? AND doc_id IS NOT NULL",
            (other,),
        ).fetchone()
        out[side].append(
            {
                "rel": r["rel"],
                "title": name,
                "type": etype,
                "doc_id": int(other[0]) if other else None,
                "evidence": r["evidence"],
            }
        )
    return out
