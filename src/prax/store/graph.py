"""The graph: entities, edges as evidence, and the review queue.

Edges carry their provenance and are invalidated, never deleted (invariant
8); every triple is validated against the composed ontology (invariant 9)
and a misfit goes to the review queue instead of into the graph. Traversal
walks canonical ids, so a merged alias and its survivor are one node.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from prax import ontology

from .base import _NOW, _like_prefix, _serialized
from .retrieval import CONTEXT_LIMIT, _similar_documents

MAX_HOPS = 2


CONFIDENCE_LEVELS = ("EXTRACTED", "INFERRED", "AMBIGUOUS")


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


@_serialized
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


@_serialized
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
    cites_rows = con.execute(
        """
        SELECT DISTINCT t.name FROM edges x
        JOIN entities s ON s.id = x.src JOIN entities t ON t.id = x.dst
        WHERE x.rel = 'cites' AND x.valid_to IS NULL AND s.name = ? AND s.type = 'paper'
        ORDER BY t.name
        """,
        (title,),
    ).fetchall()
    cited_titles = [r["name"] for r in cites_rows]
    in_library = _doc_ids_by_title(con, cited_titles) if cited_titles else {}
    cites = sorted(
        ({"title": t, "doc_id": in_library.get(t)} for t in cited_titles),
        key=lambda c: (c["doc_id"] is None, c["title"].lower()),
    )
    cited_by = [
        dict(r)
        for r in con.execute(
            """
            SELECT DISTINCT x.source_doc AS doc_id, d.title FROM edges x
            JOIN entities t ON t.id = x.dst JOIN documents d ON d.id = x.source_doc
            WHERE x.rel = 'cites' AND x.valid_to IS NULL AND t.name = ?
              AND t.type = 'paper'
              AND x.source_doc != ?
            ORDER BY d.title
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


@_serialized
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


@_serialized
def find_entities(
    con: sqlite3.Connection, q: str, *, limit: int = 20
) -> list[dict[str, Any]]:
    """Entities whose name contains ``q`` (case-insensitive), with their
    number of currently valid edges, most connected first."""
    pattern = "%" + _like_prefix(q.lower())[:-1] + "%"
    rows = con.execute(
        """
        SELECT e.id, e.name, e.type,
               (SELECT count(*) FROM edges x
                WHERE (x.src = e.id OR x.dst = e.id) AND x.valid_to IS NULL) AS degree
        FROM entities e WHERE lower(e.name) LIKE ? ESCAPE '!'
          AND e.canonical_id IS NULL
        ORDER BY degree DESC, e.name LIMIT ?
        """,
        (pattern, max(1, min(limit, 200))),
    ).fetchall()
    return [dict(r) for r in rows]


@dataclass
class Edge:
    src: str
    src_type: str
    rel: str
    dst: str
    dst_type: str


def _entity_id(con: sqlite3.Connection, name: str, etype: str) -> int:
    con.execute(
        "INSERT OR IGNORE INTO entities (name, type) VALUES (?,?)", (name, etype)
    )
    return con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
    ).fetchone()["id"]


@_serialized
def link(
    con: sqlite3.Connection,
    edge: Edge,
    *,
    confidence: str = "EXTRACTED",
    source_doc: int | None = None,
    ontology_version: str | None = None,
    evidence: str | None = None,
    producer: str | None = None,
    run: str | None = None,
) -> int:
    """Insert a currently-valid edge; entities are created on demand.

    Types are validated against the current ontology (invariant 9); the edge
    is stamped with that ontology's version unless one is given. ``evidence``
    is a short quote from ``source_doc`` that supports the edge.
    """
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}")
    onto = ontology.current()
    onto.check_edge(edge.src_type, edge.rel, edge.dst_type)
    if ontology_version is None:
        ontology_version = onto.version
    src = _entity_id(con, edge.src, edge.src_type)
    dst = _entity_id(con, edge.dst, edge.dst_type)
    cur = con.execute(
        "INSERT INTO edges (src, dst, rel, confidence, source_doc,"
        " ontology_version, evidence, producer, run, valid_from)"
        f" VALUES (?,?,?,?,?,?,?,?,?, {_NOW})",
        (
            src,
            dst,
            edge.rel,
            confidence,
            source_doc,
            ontology_version,
            evidence,
            producer,
            run,
        ),
    )
    con.commit()
    return cur.lastrowid


@_serialized
def find_edges(con: sqlite3.Connection, edge: Edge) -> list[int]:
    """Ids of currently-valid edges with exactly this src, rel and dst.

    Lets importers seed edges idempotently without touching SQL themselves.
    """
    rows = con.execute(
        """
        SELECT e.id FROM edges e
        JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst
        WHERE s.name = ? AND s.type = ? AND e.rel = ?
          AND t.name = ? AND t.type = ? AND e.valid_to IS NULL
        ORDER BY e.id
        """,
        (edge.src, edge.src_type, edge.rel, edge.dst, edge.dst_type),
    ).fetchall()
    return [r["id"] for r in rows]


@_serialized
def merge_entities(
    con: sqlite3.Connection,
    duplicate_id: int,
    into_id: int,
    *,
    across_types: bool = False,
) -> None:
    """Record that ``duplicate_id`` is the same thing as ``into_id``.

    Nothing is deleted or rewritten: the duplicate keeps its name and its
    edges (they are evidence), and gets ``canonical_id`` pointing at the
    survivor; ``traverse`` and lookups follow the pointer. Chains are
    flattened so every alias points straight at the final survivor.
    """
    if duplicate_id == into_id:
        raise ValueError("an entity cannot be merged into itself")
    rows = {
        r["id"]: r
        for r in con.execute(
            "SELECT id, type, canonical_id FROM entities WHERE id IN (?, ?)",
            (duplicate_id, into_id),
        )
    }
    if len(rows) != 2:
        raise KeyError("no such entity")
    if rows[duplicate_id]["type"] != rows[into_id]["type"] and not across_types:
        raise ValueError("entities of different types cannot be merged")
    survivor = rows[into_id]["canonical_id"] or into_id
    if survivor == duplicate_id:
        raise ValueError("that merge would form a cycle")
    con.execute(
        "UPDATE entities SET canonical_id = ? WHERE id = ? OR canonical_id = ?",
        (survivor, duplicate_id, duplicate_id),
    )
    con.commit()


@_serialized
def rename_entity(con: sqlite3.Connection, entity_id: int, name: str) -> str:
    """Give an entity a better name — what the heal pass does with a title
    a citation importer left markup in. When another entity of the same
    type already carries that name, this one is merged into it instead
    (two rows with one name is what the unique index forbids and what the
    graph means anyway). Returns ``renamed``, ``merged`` or ``unchanged``."""
    row = con.execute(
        "SELECT name, type FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such entity: {entity_id}")
    name = name.strip()
    if not name or name == row["name"]:
        return "unchanged"
    other = con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (name, row["type"])
    ).fetchone()
    if other is not None and other["id"] != entity_id:
        merge_entities(con, entity_id, other["id"])
        return "merged"
    con.execute("UPDATE entities SET name = ? WHERE id = ?", (name, entity_id))
    con.commit()
    return "renamed"


@_serialized
def canonical_entity(con: sqlite3.Connection, entity_id: int) -> int:
    row = con.execute(
        "SELECT canonical_id FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such entity: {entity_id}")
    return row["canonical_id"] or entity_id


@_serialized
def invalidate_edge(
    con: sqlite3.Connection,
    edge_id: int,
    *,
    successor: Edge | None = None,
    confidence: str = "EXTRACTED",
    source_doc: int | None = None,
    evidence: str | None = None,
    producer: str | None = None,
    run: str | None = None,
) -> int | None:
    """End an edge's validity now (``valid_to``), optionally inserting the
    edge that supersedes it. The old edge stays as history (invariant 8).
    Returns the successor's id."""
    cur = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE id = ? AND valid_to IS NULL",
        (edge_id,),
    )
    if cur.rowcount == 0:
        raise KeyError(f"no such currently valid edge: {edge_id}")
    con.commit()
    if successor is None:
        return None
    return link(
        con,
        successor,
        confidence=confidence,
        source_doc=source_doc,
        evidence=evidence,
        producer=producer,
        run=run,
    )


@_serialized
def retire_reading(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    producer: str,
    except_version: str,
) -> int:
    """End the live edges ``producer`` wrote from this document under any
    ontology version but ``except_version``: a producer re-reading a
    document under its current subset (another domain, a grown module)
    supersedes its own earlier reading. Other producers' edges stay.
    History is kept (invariant 8); returns how many edges."""
    cur = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE valid_to IS NULL"
        " AND source_doc = ? AND producer = ?"
        " AND coalesce(ontology_version, '') != ?",
        (doc_id, producer, except_version),
    )
    # the earlier reading's open review items are superseded as well: the
    # new reading queues its own misfits against the ontology it was read
    # under (review items carry no producer, so this is per document)
    con.execute(
        f"UPDATE review_queue SET resolution = 'dropped', resolved_at = {_NOW}"
        " WHERE source_doc = ? AND resolution IS NULL"
        " AND coalesce(ontology_version, '') != ?",
        (doc_id, except_version),
    )
    con.commit()
    return cur.rowcount


@_serialized
def retire_run(
    con: sqlite3.Connection,
    *,
    producer: str | None = None,
    run: str | None = None,
) -> int:
    """End every live edge a producer or a run wrote (both when both are
    given): what "upgrade" means once a better extractor has re-read the
    documents. History is kept (invariant 8); returns how many edges."""
    if producer is None and run is None:
        raise ValueError("retire_run needs a producer or a run")
    clauses = ["valid_to IS NULL"]
    args: list[Any] = []
    if producer is not None:
        clauses.append("producer = ?")
        args.append(producer)
    if run is not None:
        clauses.append("run = ?")
        args.append(run)
    cur = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE " + " AND ".join(clauses), args
    )
    con.commit()
    return cur.rowcount


@_serialized
def provenance_summary(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Live and retired edge counts per producer and run."""
    rows = con.execute(
        """
        SELECT producer, run, sum(valid_to IS NULL) AS live,
               sum(valid_to IS NOT NULL) AS retired, min(ingested_at) AS first_at
        FROM edges GROUP BY producer, run ORDER BY first_at
        """
    ).fetchall()
    return [dict(r) for r in rows]


@_serialized
def backfill_provenance(con: sqlite3.Connection) -> dict[str, int]:
    """Fill ``producer`` and ``run`` on edges written before migration 0007,
    from the evidence prefix (citations, pages) and the source document's
    stamps (Zotero seeds, extraction). Idempotent: only NULL producers."""
    counts: dict[str, int] = {}

    def tag(
        where: str, producer: str, run_expr: str, args: tuple[Any, ...] = ()
    ) -> None:
        cur = con.execute(
            f"UPDATE edges SET producer = ?, run = {run_expr}"
            f" WHERE producer IS NULL AND {where}",
            (producer, *args),
        )
        counts[producer] = counts.get(producer, 0) + cur.rowcount

    tag("evidence LIKE 'crossref %'", "crossref", "'backfill'")
    tag("evidence LIKE 'openalex %'", "openalex", "'backfill'")
    tag("evidence LIKE 'page %' OR evidence LIKE 'project %'", "page", "'backfill'")
    tag(
        "evidence IS NULL AND rel IN ('authored_by', 'published_in') AND source_doc IN"
        " (SELECT id FROM documents WHERE json_extract(meta, '$.source') = 'zotero')",
        "zotero",
        "'backfill'",
    )
    # extraction: the source document's stamp names the model; the edge's
    # ontology version names the run
    cur = con.execute(
        """
        UPDATE edges SET
            producer = COALESCE(
                (SELECT json_extract(meta, '$.extraction.extractor') FROM documents d
                 WHERE d.id = edges.source_doc), 'extraction'),
            run = 'ontology-v' || COALESCE(ontology_version, '?')
        WHERE producer IS NULL AND evidence IS NOT NULL
        """
    )
    counts["extraction"] = cur.rowcount
    cur = con.execute(
        "UPDATE edges SET producer = 'manual', run = 'backfill' WHERE producer IS NULL"
    )
    counts["manual"] = cur.rowcount
    con.commit()
    return counts


@_serialized
def queue_review(
    con: sqlite3.Connection,
    *,
    src: str,
    src_type: str | None,
    rel: str,
    dst: str,
    dst_type: str | None,
    reason: str,
    source_doc: int | None = None,
    evidence: str | None = None,
    ontology_version: str | None = None,
) -> int:
    """Park a triple that does not fit the ontology (invariant 9): it is
    kept for a person to decide, never written to the graph."""
    version = ontology_version or ontology.current().version
    cur = con.execute(
        "INSERT INTO review_queue (source_doc, src, src_type, rel, dst, dst_type,"
        " evidence, reason, ontology_version) VALUES (?,?,?,?,?,?,?,?,?)",
        (source_doc, src, src_type, rel, dst, dst_type, evidence, reason, version),
    )
    con.commit()
    return cur.lastrowid


def _review_where(
    open_only: bool, rel: str | None, unmapped: bool | None
) -> tuple[str, list[Any]]:
    """The filter shared by listing, counting and bulk resolution: open
    items only, a relation, and whether the item is an ``unmapped`` triple
    (no types, the model's own relation name) or a typed misfit."""
    clauses: list[str] = []
    args: list[Any] = []
    if open_only:
        clauses.append("resolved_at IS NULL")
    if rel:
        clauses.append("lower(rel) = ?")
        args.append(rel.lower())
    if unmapped is True:
        clauses.append("reason LIKE 'unmapped:%'")
    elif unmapped is False:
        clauses.append("reason NOT LIKE 'unmapped:%'")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


@_serialized
def list_review(
    con: sqlite3.Connection,
    *,
    open_only: bool = True,
    limit: int = 100,
    offset: int = 0,
    rel: str | None = None,
    unmapped: bool | None = None,
) -> list[dict[str, Any]]:
    where, args = _review_where(open_only, rel, unmapped)
    rows = con.execute(
        f"SELECT * FROM review_queue{where} ORDER BY id LIMIT ? OFFSET ?",
        (*args, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


@_serialized
def count_review(
    con: sqlite3.Connection,
    *,
    open_only: bool = True,
    rel: str | None = None,
    unmapped: bool | None = None,
) -> int:
    where, args = _review_where(open_only, rel, unmapped)
    row = con.execute(f"SELECT count(*) FROM review_queue{where}", args).fetchone()
    return int(row[0])


@_serialized
def resolve_review_many(
    con: sqlite3.Connection,
    resolution: str,
    *,
    rel: str | None = None,
    unmapped: bool | None = None,
) -> int:
    """Close every open item matching the filter; returns how many."""
    if resolution not in ("linked", "dropped", "ontology"):
        raise ValueError("resolution must be linked, dropped or ontology")
    where, args = _review_where(True, rel, unmapped)
    cur = con.execute(
        f"UPDATE review_queue SET resolved_at = {_NOW}, resolution = ?{where}",
        (resolution, *args),
    )
    con.commit()
    return cur.rowcount


@_serialized
def get_review(con: sqlite3.Connection, review_id: int) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT * FROM review_queue WHERE id = ?", (review_id,)
    ).fetchone()
    return dict(row) if row else None


@_serialized
@_serialized
def retype_review(
    con: sqlite3.Connection, review_id: int, src_type: str, dst_type: str
) -> None:
    """Write the types a model (or a person) gave an untyped item onto
    it, the item staying open: a typed misfit now, which the rules and a
    replay against a later ontology work on, and which the typing pass
    does not put to the model again."""
    con.execute(
        "UPDATE review_queue SET src_type = ?, dst_type = ?"
        " WHERE id = ? AND resolved_at IS NULL",
        (src_type, dst_type, review_id),
    )
    con.commit()


def resolve_review(con: sqlite3.Connection, review_id: int, resolution: str) -> None:
    """Close a review item: ``linked`` (written as an edge by hand),
    ``dropped`` or ``ontology`` (the ontology grew to fit it)."""
    if resolution not in ("linked", "dropped", "ontology"):
        raise ValueError("resolution must be linked, dropped or ontology")
    cur = con.execute(
        f"UPDATE review_queue SET resolved_at = {_NOW}, resolution = ? WHERE id = ?",
        (resolution, review_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"no such review item: {review_id}")
    con.commit()


@_serialized
def select_for_extraction(
    con: sqlite3.Connection,
    *,
    ontology_version: str,
    limit: int | None = None,
    mime_prefix: str | None = None,
    min_chars: int = 0,
    domain: str | None = None,
    onto: ontology.Ontology | None = None,
    sources: tuple[str, ...] | None = None,
    skip_mime_prefix: str | None = None,
) -> list[int]:
    """Indexed documents not yet extracted under ``ontology_version``
    (``meta.extraction.ontology_version``), oldest first. ``min_chars``
    skips documents whose chunks hold less text than that (Zotero notes,
    scans without a text layer): nothing to extract, a call wasted.
    ``domain`` keeps the documents assigned to that module; with ``onto``
    a document with a domain set is compared against the version of its
    own subset of the ontology, not the whole (one query: a CASE over
    the domain sets in use). ``sources`` keeps documents of those
    ``meta.source`` values (captures); ``skip_mime_prefix`` drops images
    and the like."""
    stamp = "json_extract(meta, '$.extraction.ontology_version')"
    sql = (
        "SELECT id FROM documents WHERE text_hash IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    )
    args: list[Any] = []
    if sources is not None:
        sql += (
            f" AND json_extract(meta, '$.source') IN ({','.join('?' * len(sources))})"
        )
        args.extend(sources)
    want, want_args = _subset_version_case(con, onto, ontology_version)
    sql += f" AND ({stamp} IS NULL OR {stamp} != {want})"
    args.extend(want_args)
    # a reading that failed under this version is not tried every pass
    # (extraction.note_failure); the version moving on re-selects it
    failed = "json_extract(meta, '$.extraction_error.ontology_version')"
    sql += f" AND ({failed} IS NULL OR {failed} != {want})"
    args.extend(want_args)
    if skip_mime_prefix:
        sql += " AND coalesce(mime, '') NOT LIKE ? ESCAPE '!'"
        args.append(_like_prefix(skip_mime_prefix))
    if domain:
        sql += (
            " AND EXISTS (SELECT 1 FROM json_each(documents.meta, '$.domains')"
            " WHERE value = ?)"
        )
        args.append(domain)
    if min_chars > 0:
        sql += (
            " AND (SELECT coalesce(sum(length(text)), 0) FROM chunks"
            "      WHERE chunks.doc_id = documents.id) >= ?"
        )
        args.append(min_chars)
    if mime_prefix:
        sql += " AND mime LIKE ? ESCAPE '!'"
        args.append(_like_prefix(mime_prefix))
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return [r["id"] for r in con.execute(sql, args)]


def _subset_version_case(
    con: sqlite3.Connection, onto: ontology.Ontology | None, whole: str
) -> tuple[str, list[Any]]:
    """The SQL for "the version this document's reading should carry": a
    CASE over the domain sets in use (few) mapping each to the version of
    its subset of ``onto``, ``whole`` for a document without a set. Without
    ``onto`` every document is held to ``whole``."""
    if onto is None:
        return "?", [whole]
    whens: list[str] = []
    args: list[Any] = []
    for (raw,) in con.execute(
        "SELECT DISTINCT json_extract(meta, '$.domains') FROM documents"
        " WHERE json_extract(meta, '$.domains') IS NOT NULL"
    ):
        try:
            domains = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not domains:
            continue
        whens.append("WHEN ? THEN ?")
        args.extend([raw, onto.for_domains(domains).version])
    if not whens:
        return "?", [whole]
    args.append(whole)
    return f"CASE json_extract(meta, '$.domains') {' '.join(whens)} ELSE ? END", args


@_serialized
def traverse(
    con: sqlite3.Connection, entity_name: str, hops: int = 1
) -> list[dict[str, Any]]:
    """Currently-valid edges within ``hops`` (max MAX_HOPS) of an entity.

    An edge is returned only when both of its endpoints are reachable within
    the hop limit; ``hop`` is the distance of its farther endpoint.
    """
    hops = max(0, min(hops, MAX_HOPS))
    # The walk runs over raw entity ids and, at every step, expands the
    # entity reached to its whole alias group (idx_entities_canonical), so
    # a merged alias and its survivor are one node and the recursion uses
    # the edge indexes (a canon CTE in the recursion had no index and
    # scanned every live edge per frontier row: minutes on a hub at two
    # hops). Edges are reported under the canonical names (invariant 8:
    # the rows keep the alias ids they were written with).
    rows = con.execute(
        """
        WITH RECURSIVE
        start(cid) AS (
            SELECT COALESCE(canonical_id, id) FROM entities WHERE name = ?
        ),
        walk(id, depth) AS (
            SELECT n.id, 0 FROM entities n
            JOIN start s ON COALESCE(n.canonical_id, n.id) = s.cid
            UNION
            SELECT m.id, w.depth + 1
            FROM walk w
            JOIN edges e ON e.src = w.id AND e.valid_to IS NULL
            JOIN entities nb ON nb.id = e.dst
            JOIN entities m
                 ON COALESCE(m.canonical_id, m.id) = COALESCE(nb.canonical_id, nb.id)
            WHERE w.depth < ?
            UNION
            SELECT m.id, w.depth + 1
            FROM walk w
            JOIN edges e ON e.dst = w.id AND e.valid_to IS NULL
            JOIN entities nb ON nb.id = e.src
            JOIN entities m
                 ON COALESCE(m.canonical_id, m.id) = COALESCE(nb.canonical_id, nb.id)
            WHERE w.depth < ?
        ),
        reach(id, depth) AS (
            SELECT id, MIN(depth) FROM walk GROUP BY id
        )
        SELECT e.id AS edge_id,
               cs.name AS src, cs.type AS src_type, e.rel,
               cd.name AS dst, cd.type AS dst_type,
               e.confidence, e.source_doc, e.evidence, e.ontology_version,
               e.producer, e.run,
               e.valid_from,
               MAX(rs.depth, rd.depth) AS hop
        FROM edges e
        JOIN reach rs ON rs.id = e.src
        JOIN reach rd ON rd.id = e.dst
        JOIN entities s ON s.id = e.src
        JOIN entities cs ON cs.id = COALESCE(s.canonical_id, s.id)
        JOIN entities d ON d.id = e.dst
        JOIN entities cd ON cd.id = COALESCE(d.canonical_id, d.id)
        WHERE e.valid_to IS NULL
        ORDER BY hop, e.id
        """,
        (entity_name, hops, hops),
    ).fetchall()
    return [dict(r) for r in rows]
