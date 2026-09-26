"""What the graph says about a document, for ask and the document page, and
the map of the biggest hubs."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..base import _reading
from ..retrieval import CONTEXT_LIMIT, _similar_documents

HUB_TYPES = ("concept", "method", "tool", "dataset")


def _doc_ids_by_title(con: sqlite3.Connection, titles: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(0, len(titles), 400):
        part = titles[i : i + 400]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            f"SELECT id, title FROM documents WHERE title IN ({marks}) ORDER BY id",
            part,
        ):
            out.setdefault(r["title"], r["id"])
    return out


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
          AND s.name = d.title AND x.rel NOT IN ({skip})
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
    meta = json.loads(doc["meta"] or "{}")

    # the document's own edges, by the entity they point at
    entities: list[dict[str, Any]] = []
    author_ids: list[int] = []
    entity_ids: list[int] = []
    for r in con.execute(
        """
        SELECT x.rel, x.confidence, t.id AS tid, t.name, t.type FROM edges x
        JOIN entities s ON s.id = x.src JOIN entities t ON t.id = x.dst
        WHERE x.source_doc = ? AND x.valid_to IS NULL AND s.name = ?
        ORDER BY t.type, t.name
        """,
        (doc_id, title),
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
    # the citing document); titles resolved to library documents where present
    # each with the surest confidence any producer gave it: Crossref's
    # and a printed id's edges are EXTRACTED, a title match INFERRED
    # (the score in its evidence), a tie between twins AMBIGUOUS
    cites_rows = con.execute(
        """
        SELECT t.name, min(CASE x.confidence WHEN 'EXTRACTED' THEN 0
                           WHEN 'INFERRED' THEN 1 ELSE 2 END) AS rank
        FROM edges x
        JOIN entities s ON s.id = x.src JOIN entities t ON t.id = x.dst
        WHERE x.rel = 'cites' AND x.valid_to IS NULL AND s.name = ? AND s.type = 'paper'
        GROUP BY t.name ORDER BY t.name
        """,
        (title,),
    ).fetchall()
    cited_titles = [r["name"] for r in cites_rows]
    in_library = _doc_ids_by_title(con, cited_titles) if cited_titles else {}
    cites = sorted(
        (
            {
                "title": r["name"],
                "doc_id": in_library.get(r["name"]),
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
            """
            SELECT x.source_doc AS doc_id, d.title,
                   min(CASE x.confidence WHEN 'EXTRACTED' THEN 0
                       WHEN 'INFERRED' THEN 1 ELSE 2 END) AS rank
            FROM edges x
            JOIN entities t ON t.id = x.dst JOIN documents d ON d.id = x.source_doc
            WHERE x.rel = 'cites' AND x.valid_to IS NULL AND t.name = ?
              AND t.type = 'paper'
              AND x.source_doc != ?
            GROUP BY x.source_doc, d.title ORDER BY d.title
            """,
            (title, doc_id),
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
            """
            SELECT DISTINCT x.source_doc AS doc_id, d.title, p.slug, p.kind
            FROM edges x JOIN entities t ON t.id = x.dst
            JOIN documents d ON d.id = x.source_doc JOIN pages p ON p.doc_id = d.id
            WHERE x.rel = 'annotates' AND x.valid_to IS NULL AND t.name = ?
              AND x.source_doc != ?
            ORDER BY d.title
            """,
            (title, doc_id),
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
                """
                SELECT DISTINCT s.name AS title, s.type,
                       (SELECT id FROM documents WHERE title = s.name
                        ORDER BY id LIMIT 1)
                           AS doc_id
                FROM edges x JOIN entities s ON s.id = x.src
                JOIN entities t ON t.id = x.dst
                WHERE x.rel = 'part_of' AND x.valid_to IS NULL AND t.name = ?
                ORDER BY s.name
                """,
                (title,),
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


@_reading
def hub_graph(
    con: sqlite3.Connection,
    *,
    limit: int = 30,
    types: tuple[str, ...] = HUB_TYPES,
    min_shared: int = 2,
) -> dict[str, Any]:
    """The most connected entities of the given types, the currently valid
    edges among them, and ``links``: pairs of hubs that share at least
    ``min_shared`` source documents (co-occurrence, the topic map). Degrees
    and edges are counted over canonical ids, like ``traverse``.

    Every step walks the edge indexes: the ends of the live edges as one
    list, each end mapped to its canonical entity by primary key. A join
    on ``c.id = x.src OR c.id = x.dst`` can use neither index and took
    two seconds on 126k edges; this takes a tenth of that."""
    limit = max(1, min(limit, 200))
    marks = ",".join("?" * len(types))
    nodes = con.execute(
        f"""
        WITH ends(id) AS (
            SELECT src FROM edges WHERE valid_to IS NULL
            UNION ALL
            SELECT dst FROM edges WHERE valid_to IS NULL
        ),
        deg(cid, degree) AS (
            SELECT COALESCE(n.canonical_id, n.id), count(*)
            FROM ends JOIN entities n ON n.id = ends.id
            GROUP BY COALESCE(n.canonical_id, n.id)
        )
        SELECT e.id, e.name, e.type, d.degree FROM deg d JOIN entities e ON e.id = d.cid
        WHERE e.type IN ({marks}) ORDER BY d.degree DESC, e.name LIMIT ?
        """,
        (*types, limit),
    ).fetchall()
    ids = [r["id"] for r in nodes]
    if not ids:
        return {"nodes": [], "edges": [], "links": []}
    # every alias of the hubs, so an edge written under an alias counts
    idmarks = ",".join("?" * len(ids))
    members = [
        r[0]
        for r in con.execute(
            f"SELECT id FROM entities WHERE COALESCE(canonical_id, id) IN ({idmarks})",
            ids,
        )
    ]
    mm = ",".join("?" * len(members))
    edges = [
        dict(r)
        for r in con.execute(
            f"""
            SELECT x.id AS edge_id, s.name AS src, s.type AS src_type, x.rel,
                   t.name AS dst, t.type AS dst_type, x.confidence,
                   x.source_doc, x.evidence, x.producer, x.run
            FROM edges x
            JOIN entities a ON a.id = x.src
            JOIN entities b ON b.id = x.dst
            JOIN entities s ON s.id = COALESCE(a.canonical_id, a.id)
            JOIN entities t ON t.id = COALESCE(b.canonical_id, b.id)
            WHERE x.valid_to IS NULL AND x.src IN ({mm}) AND x.dst IN ({mm})
            ORDER BY x.id
            """,
            (*members, *members),
        ).fetchall()
    ]
    # co-occurrence: which documents each hub (through any alias) appears in
    touch: dict[int, set[int]] = {}
    for side in ("src", "dst"):
        for cid, doc in con.execute(
            f"""
            SELECT COALESCE(n.canonical_id, n.id), x.source_doc
            FROM edges x JOIN entities n ON n.id = x.{side}
            WHERE x.valid_to IS NULL AND x.source_doc IS NOT NULL
              AND x.{side} IN ({mm})
            """,
            members,
        ):
            touch.setdefault(cid, set()).add(doc)
    by_id = {r["id"]: r for r in nodes}
    links: list[dict[str, Any]] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            shared = len(touch.get(a, set()) & touch.get(b, set()))
            if shared >= max(1, min_shared):
                links.append(
                    {
                        "a": by_id[a]["name"],
                        "a_type": by_id[a]["type"],
                        "b": by_id[b]["name"],
                        "b_type": by_id[b]["type"],
                        "weight": shared,
                    }
                )
    links.sort(key=lambda r: -r["weight"])
    return {
        "nodes": [
            {"name": r["name"], "type": r["type"], "degree": r["degree"]} for r in nodes
        ],
        "edges": edges,
        "links": links[:300],
    }
