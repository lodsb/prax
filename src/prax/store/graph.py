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

from .base import _NOW, _like_prefix, _reading, _serialized
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


@_reading
def find_entities(
    con: sqlite3.Connection, q: str, *, limit: int = 20
) -> list[dict[str, Any]]:
    """Entities whose name — or a name they are *known by* — contains
    ``q`` (case-insensitive), with their number of currently valid edges,
    most connected first.

    The labels matter as much as the names. A pass that renames
    ``Knoblauchzehen`` to ``garlic cloves`` leaves the German as a label,
    and a search that read only ``entities.name`` would answer nothing
    for the word the document actually used: 522 names reached nothing
    that way on 2026-09-24. A hit found only through a label says so
    (``as``), so a caller can show what it matched.

    The degree is two counts, one per end: an OR across ``src`` and
    ``dst`` made the subquery scan the edges for every matching name (340
    names, 14 s; two indexed counts, 46 ms, 2026-09-22).
    """
    pattern = "%" + _like_prefix(q.lower())[:-1] + "%"
    # SQLite's own lower() is ASCII-only: it leaves Ä and Ü alone, so
    # `lower(name) LIKE '%ästhetik%'` never matched "Ästhetik der Lüge" —
    # in a library a fifth of which is German, silently. Python's does the
    # whole of Unicode, and is used only when the query needs it, because
    # a callback per row costs a few times the scan
    lower = "lower"
    if not q.isascii():
        con.create_function("unicode_lower", 1, lambda s: s.lower() if s else s)
        lower = "unicode_lower"
    rows = con.execute(
        f"""
        WITH hit(id, matched) AS (
            SELECT id, NULL FROM entities
             WHERE {lower}(name) LIKE ? ESCAPE '!' AND canonical_id IS NULL
            UNION
            SELECT COALESCE(e.canonical_id, e.id), l.label
              FROM entity_labels l JOIN entities e ON e.id = l.entity_id
             WHERE {lower}(l.label) LIKE ? ESCAPE '!'
        )
        SELECT e.id, e.name, e.type, min(hit.matched) AS "as",
               (SELECT count(*) FROM edges x
                WHERE x.src = e.id AND x.valid_to IS NULL)
               + (SELECT count(*) FROM edges x
                  WHERE x.dst = e.id AND x.valid_to IS NULL) AS degree
        FROM hit JOIN entities e ON e.id = hit.id
        WHERE e.canonical_id IS NULL
        GROUP BY e.id
        ORDER BY degree DESC, e.name LIMIT ?
        """,
        (pattern, pattern, max(1, min(limit, 200))),
    ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        # the name itself matched: nothing to explain
        if pattern.strip("%") in row["name"].lower():
            row.pop("as", None)
        out.append(row)
    return out


@dataclass
class Edge:
    src: str
    src_type: str
    rel: str
    dst: str
    dst_type: str


def _entity_id(con: sqlite3.Connection, name: str, etype: str) -> int:
    """The entity of this type called ``name``, created if there is none.

    A name the graph already knows this thing by counts: ``Olivenöl`` is
    a label of ``olive oil``, so a German recipe naming it again lands on
    the entity the vocabulary pass folded it into rather than starting
    the split over (`docs/stratification.md`, step 5). Only an unmerged
    entity of the same type, and only when exactly one answers — two
    entities of one type sharing a label is a question, and a question is
    not resolved by picking the lower id.
    """
    row = con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
    ).fetchone()
    if row is not None:
        return int(row["id"])
    known = con.execute(
        "SELECT DISTINCT e.id FROM entity_labels l JOIN entities e"
        " ON e.id = l.entity_id WHERE l.label = ? COLLATE NOCASE"
        " AND e.type = ? AND e.canonical_id IS NULL LIMIT 2",
        (name, etype),
    ).fetchall()
    if len(known) == 1:
        return int(known[0]["id"])
    con.execute(
        "INSERT OR IGNORE INTO entities (name, type) VALUES (?,?)", (name, etype)
    )
    made = con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
    ).fetchone()["id"]
    # a new entity answers to its own name from the start, so the labels
    # are the whole truth about what a thing is called and the name
    # column can be rebuilt from them (migration 23, docs/identity.md)
    con.execute(
        "INSERT OR IGNORE INTO entity_labels (entity_id, label, kind, producer)"
        " VALUES (?, ?, 'pref', 'baseline')",
        (made, name),
    )
    return int(made)


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


@_reading
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
    producer: str | None = None,
    run: str | None = None,
    confidence: str | None = None,
) -> None:
    """Record that ``duplicate_id`` is the same thing as ``into_id``.

    Nothing is deleted or rewritten: the duplicate keeps its name and its
    edges (they are evidence), and gets ``canonical_id`` pointing at the
    survivor; ``traverse`` and lookups follow the pointer. Chains are
    flattened so every alias points straight at the final survivor.

    The duplicate's name becomes a label of the survivor
    (``entity_labels``) with its language and, when the caller says so,
    the producer and run that decided it. That is what makes a round of
    merging retirable (``unmerge_run``): a merge is a claim like an edge,
    and a claim nobody signed cannot be taken back.
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
    _label_from_merge(con, survivor, duplicate_id, producer, run, confidence)
    con.commit()


def _label_from_merge(
    con: sqlite3.Connection,
    survivor: int,
    duplicate_id: int,
    producer: str | None,
    run: str | None,
    confidence: str | None,
) -> None:
    """The duplicate's name, kept as a label of the survivor."""
    from prax import language

    row = con.execute(
        "SELECT name FROM entities WHERE id = ?", (duplicate_id,)
    ).fetchone()
    if row is None:
        return
    name = str(row["name"] if hasattr(row, "keys") else row[0])
    con.execute(
        "INSERT OR IGNORE INTO entity_labels (entity_id, label, lang, kind,"
        " from_entity, producer, run, confidence) VALUES (?, ?, ?, 'alt', ?, ?, ?, ?)",
        (
            survivor,
            name,
            language.detect(name),
            duplicate_id,
            producer,
            run,
            confidence,
        ),
    )


def display_language() -> str:
    """Which of a thing's names this host shows: the language the library
    is written in (`prax.language.canonical`, `graph.language` in
    prax.yaml). The graph in German for a German reader, one node either
    way — the point of the label table."""
    from prax import language

    return language.canonical()


def _display_name(con: sqlite3.Connection, entity_id: int) -> str | None:
    """The name to show for an entity: its preferred label in the host's
    language, then in English, then any preferred label it has.

    None when it has none, which after migration 23 means the entity was
    made by something that bypassed ``_entity_id`` — the caller then
    leaves the name it has.
    """
    from prax import language

    want = display_language()
    rows = con.execute(
        "SELECT label, lang FROM entity_labels WHERE entity_id = ? AND kind = 'pref'",
        (entity_id,),
    ).fetchall()
    if not rows:
        return None
    by_lang = {r["lang"]: r["label"] for r in rows}
    for lang in (want, language.CANONICAL, None):
        if lang in by_lang:
            return str(by_lang[lang])
    return str(rows[0]["label"])


def _refresh_name(con: sqlite3.Connection, entity_id: int) -> str | None:
    """Rewrite the cached name from the labels. Returns it when it moved.

    ``entities.name`` is a cache of the preferred label in the host's
    language (docs/identity.md): the labels say what a thing is called,
    the column is what a join reads and what the unique index guards. A
    name another entity of that type already shows is left alone — two
    things may not display the same, which is the type clash the review
    queue is for, not something to resolve by overwriting.
    """
    show = _display_name(con, entity_id)
    if show is None:
        return None
    row = con.execute(
        "SELECT name, type FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None or row["name"] == show:
        return None
    taken = con.execute(
        "SELECT 1 FROM entities WHERE name = ? AND type = ? AND id != ?",
        (show, row["type"], entity_id),
    ).fetchone()
    if taken is not None:
        return None
    # the name being replaced is a name this entity answers to, and is
    # recorded before it goes. `Chomsky-Normalform` was lost because
    # migration 23 had skipped entities that already had a preferred
    # label, so their own name was in no row and a rebuild renamed them
    # with nothing left to put back. A guard would have caught that one
    # path; this makes the loss impossible on all of them.
    # a name appears once per entity whatever its language, so this asks
    # for the text rather than leaning on the unique index, which counts
    # a different `lang` as a different label
    con.execute(
        "INSERT INTO entity_labels (entity_id, label, kind, producer)"
        " SELECT ?, ?, 'alt', 'baseline' WHERE NOT EXISTS ("
        "   SELECT 1 FROM entity_labels WHERE entity_id = ?"
        "    AND label = ? COLLATE NOCASE)",
        (entity_id, row["name"], entity_id, row["name"]),
    )
    con.execute("UPDATE entities SET name = ? WHERE id = ?", (show, entity_id))
    return show


@_serialized
def rename_display_language(con: sqlite3.Connection) -> dict[str, int]:
    """Every entity's shown name rebuilt from its labels: what a host
    runs after changing `graph.language`. The maintain pass calls it."""
    moved = 0
    rows = con.execute(
        "SELECT DISTINCT entity_id FROM entity_labels WHERE kind = 'pref'"
    ).fetchall()
    for n, r in enumerate(rows, 1):
        entity_id = int(r["entity_id"])
        if _refresh_name(con, entity_id):
            moved += 1
        if n % 2000 == 0:
            con.commit()
    con.commit()
    return {
        "entities": len(rows),
        "renamed": moved,
        "language": display_language(),
    }


@_serialized
def add_label(
    con: sqlite3.Connection,
    entity_id: int,
    label: str,
    *,
    lang: str | None = None,
    kind: str = "alt",
    producer: str | None = None,
    run: str | None = None,
    source_doc: int | None = None,
    confidence: str | None = None,
    was: bool = False,
) -> int:
    """A name this entity is also known by, in a language.

    ``kind`` is ``pref`` for the name to show in that language and ``alt``
    otherwise, the two SKOS gives a concept. There is one preferred name
    per language (migration 22 holds it), so a new one demotes the one
    already there rather than colliding with it — a language whose
    preferred name is decided twice should end with the later answer, not
    with an error.

    ``was`` marks the name the entity carried before a pass renamed it,
    which is what ``unmerge_run`` puts back. It is a fact about the label,
    not a kind of label.

    Returns how many rows were written (0 when it was already there).
    """
    if not label.strip():
        raise ValueError("a label needs a name")
    # a name appears once per entity: its language is a property of the
    # label, not part of which label it is. Migration 23 gives every
    # entity a label with no language, and the passes then learn one — so
    # without this a rename left two rows differing only in `lang`, and
    # the `languages` backfill would collide on the unique index trying
    # to place the first (2026-09-24)
    if lang is not None:
        con.execute(
            "UPDATE entity_labels SET lang = ? WHERE entity_id = ? AND label = ?"
            " AND lang IS NULL",
            (lang, entity_id, label),
        )
    if kind == "pref" and lang is not None:
        con.execute(
            "UPDATE entity_labels SET kind = 'alt'"
            " WHERE entity_id = ? AND lang = ? AND kind = 'pref' AND label != ?",
            (entity_id, lang, label),
        )
    cur = con.execute(
        "INSERT OR IGNORE INTO entity_labels (entity_id, label, lang, kind,"
        " source_doc, producer, run, confidence, was)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            label,
            lang,
            kind,
            source_doc,
            producer,
            run,
            confidence,
            1 if was else 0,
        ),
    )
    if not cur.rowcount:
        # the name was already a label of this entity: the caller is
        # saying something about it — which kind it is, whether it is the
        # name the entity used to carry, and who says so. The provenance
        # has to move with it, or a pass cannot mark what it decided and
        # `unmerge_run` cannot find its own work: every entity carries a
        # `baseline` label from migration 23, so this is the common path
        # now rather than the rare one (2026-09-24)
        con.execute(
            # a caller naming a label it did not give a kind to is saying
            # the name exists, not that it stopped being the preferred
            # one: the kind goes up, never down
            "UPDATE entity_labels SET"
            " kind = CASE WHEN ? = 'pref' THEN 'pref' ELSE kind END,"
            " was = max(was, ?),"
            " producer = COALESCE(?, producer), run = COALESCE(?, run),"
            " confidence = COALESCE(?, confidence)"
            " WHERE entity_id = ? AND label = ?"
            " AND coalesce(lang, '') = coalesce(?, '')",
            (
                kind,
                1 if was else 0,
                producer,
                run,
                confidence,
                entity_id,
                label,
                lang,
            ),
        )
    if kind == "pref":
        _refresh_name(con, entity_id)  # the column follows the labels
    con.commit()
    return int(cur.rowcount or 0)


@_reading
def entity_labels(
    con: sqlite3.Connection, entity_id: int, *, lang: str | None = None
) -> list[dict[str, Any]]:
    """Every name an entity is known by, newest first; ``lang`` narrows to
    one language and the labels nobody could place."""
    where = " AND (lang = ? OR lang IS NULL)" if lang else ""
    args: tuple[Any, ...] = (entity_id, lang) if lang else (entity_id,)
    return [
        dict(r)
        for r in con.execute(
            "SELECT id, label, lang, kind, from_entity, source_doc, producer,"
            f" run, confidence, at FROM entity_labels WHERE entity_id = ?{where}"
            " ORDER BY kind = 'pref' DESC, id DESC",
            args,
        )
    ]


@_reading
def entities_by_label(con: sqlite3.Connection, label: str) -> list[int]:
    """The entities known by this name, whatever their own name is: how a
    German word reaches an entity the library calls something else.

    A merged alias answers for its survivor, not for itself: every entity
    carries its own name as a label since migration 23, so without that a
    lookup would return the fold and the thing it folded into.
    """
    return [
        int(r[0])
        for r in con.execute(
            "SELECT DISTINCT COALESCE(e.canonical_id, e.id) FROM entity_labels l"
            " JOIN entities e ON e.id = l.entity_id"
            " WHERE l.label = ? COLLATE NOCASE",
            (label,),
        )
    ]


@_serialized
def unmerge_run(con: sqlite3.Connection, run: str) -> int:
    """Undo a round: every entity the run folded away stands on its own
    again, every entity it renamed is called what it was called, and the
    labels it wrote are gone. Returns how many entities came back.

    The counterpart of ``retire_run`` for edges. A merge is a claim, and
    a pass that claimed wrongly has to be undoable, or nobody can try a
    new rule on the live graph. A rename is a claim too — the vocabulary
    pass makes both — which is why the name an entity had is written as a
    ``was`` label under the same run and put back here. Without that a
    pass could be taken back halfway: the folds undone, the wrong names
    left behind.
    """
    rows = con.execute(
        "SELECT from_entity FROM entity_labels WHERE run = ? AND from_entity"
        " IS NOT NULL",
        (run,),
    ).fetchall()
    ids = [int(r[0]) for r in rows]
    for entity_id in ids:
        con.execute(
            "UPDATE entities SET canonical_id = NULL WHERE id = ? OR canonical_id = ?",
            (entity_id, entity_id),
        )
    renamed = con.execute(
        "SELECT entity_id, label FROM entity_labels WHERE run = ? AND was = 1",
        (run,),
    ).fetchall()
    # what the run *wrote* goes; what it only *claimed* stays. A `was`
    # label is the entity's own older name, which the run demoted rather
    # than made — deleting it with the rest took away the very name the
    # undo exists to put back (2026-09-24)
    con.execute("DELETE FROM entity_labels WHERE run = ? AND was = 0", (run,))
    for r in renamed:
        con.execute(
            "UPDATE entity_labels SET kind = 'pref', was = 0, run = NULL"
            " WHERE entity_id = ? AND label = ?",
            (r["entity_id"], r["label"]),
        )
        _refresh_name(con, int(r["entity_id"]))
    con.commit()
    return len(ids) + len(renamed)


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


def entity_names(con: sqlite3.Connection, etype: str) -> list[tuple[int, str]]:
    """The unmerged entities of one type, id and name, by id: what a
    worker embeds for the likely tier of resolution (``prax.work``)."""
    rows = con.execute(
        "SELECT id, name FROM entities WHERE type = ? AND canonical_id IS NULL"
        " ORDER BY id",
        (etype,),
    ).fetchall()
    return [(int(r["id"]), str(r["name"])) for r in rows]


def _named_by_language(con: sqlite3.Connection, entity_id: int) -> str | None:
    """The language of a document that named this entity, where they all
    agree. Two languages naming one thing say nothing about the name."""
    rows = con.execute(
        "SELECT DISTINCT json_extract(d.meta, '$.lang') AS lang FROM edges x"
        " JOIN documents d ON d.id = x.source_doc"
        " WHERE x.valid_to IS NULL AND (x.src = ? OR x.dst = ?)"
        " AND json_extract(d.meta, '$.lang') IS NOT NULL LIMIT 3",
        (entity_id, entity_id),
    ).fetchall()
    return str(rows[0]["lang"]) if len(rows) == 1 else None


@_serialized
def name_in_english(
    con: sqlite3.Connection,
    entity_id: int,
    english: str,
    *,
    lang: str | None = None,
    producer: str = "vocabulary",
    run: str | None = None,
    confidence: str | None = None,
) -> dict[str, Any]:
    """Give an entity the name English uses, keeping the one the document
    used as a label in its own language.

    Three outcomes, and the caller is told which. An entity of the same
    type already called that: the two are merged, and the German name
    becomes a label of the survivor. An entity of *another* type called
    that: nothing is merged — ``Olivenöl`` is an ingredient where ``olive
    oil`` is a concept, and which of the two is right is the review
    queue's question, not this pass's. Nobody called that yet: the entity
    is renamed and the old name kept as a label, so a German search still
    reaches it.

    Everything this writes carries ``producer`` and ``run``, so a round
    is undoable whole (``unmerge_run``).
    """
    from prax import language

    english = " ".join(english.split())
    if not english:
        raise ValueError("a name cannot be empty")
    row = con.execute(
        "SELECT id, name, type, canonical_id FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such entity: {entity_id}")
    if row["canonical_id"]:
        # already folded into another entity, which `foreign_names` skips
        return {
            "entity": entity_id,
            "action": "merged away",
            "into": row["canonical_id"],
        }
    was = str(row["name"])
    # a name is one to three words, which is under the detector's floor, so
    # it says nothing about nearly all of them — and a label whose language
    # is unknown cannot answer "show me this in German". The document that
    # named the entity knows: `meta.lang` of a document with an edge to it
    lang = lang or language.detect(was) or _named_by_language(con, entity_id)
    # the same name can be several entities, of several types: the one to
    # fold into is one of this entity's own type that is still standing.
    # Without the ordering `page table` the concept (itself already merged
    # away) answered for `page table` the method, and a fold that should
    # have happened was reported as a clash instead (2026-09-24)
    twin = con.execute(
        "SELECT id, type, canonical_id FROM entities WHERE name = ? COLLATE NOCASE"
        " AND id != ? ORDER BY (type = ?) DESC, (canonical_id IS NULL) DESC, id"
        " LIMIT 1",
        (english, entity_id, row["type"]),
    ).fetchone()
    out: dict[str, Any] = {"entity": entity_id, "was": was, "name": english}
    # what a fold would land on is the twin's survivor, not the twin: a
    # twin of this entity's own type may itself have been folded into one
    # of another type, and comparing the twin let that through to
    # `merge_entities`, which refused it — silently, because the worker's
    # summary line does not print errors (2026-09-24)
    into = None
    if twin is not None:
        into = con.execute(
            "SELECT id, type FROM entities WHERE id = ?",
            (int(twin["canonical_id"] or twin["id"]),),
        ).fetchone()
    if into is not None and into["type"] != row["type"]:
        # the same words, a different kind of thing: a question, not a fold
        queue_review(
            con,
            src=was,
            src_type=row["type"],
            rel="same_as",
            dst=english,
            dst_type=str(into["type"]),
            reason=(
                f"{producer}: {was!r} is {english!r} in English, but the library"
                f" has that as a {into['type']} and this as a {row['type']}"
            ),
            source_doc=None,
        )
        add_label(
            con,
            entity_id,
            english,
            lang=language.canonical(),
            kind="alt",
            producer=producer,
            run=run,
            confidence=confidence,
        )
        out["action"] = "type clash"
        out["twin"] = int(into["id"])
        return out
    if into is not None:
        survivor = int(into["id"])
        if survivor == entity_id:
            # the English name is already one of this entity's own: an
            # earlier pass folded it in. Nothing to do — but say so with a
            # label, because an outcome that writes nothing leaves the
            # entity a candidate and the pass asks about it for ever
            # (62 of them, on the night of 2026-09-24)
            add_label(
                con,
                entity_id,
                english,
                lang=language.canonical(),
                kind="pref",
                producer=producer,
                run=run,
                confidence=confidence,
            )
            return {**out, "action": "already"}
        merge_entities(
            con,
            entity_id,
            survivor,
            producer=producer,
            run=run,
            confidence=confidence,
        )
        out["action"] = "merged"
        out["into"] = survivor
        return out
    # a rename is two label writes and nothing else: the name the
    # document used stops being preferred in its language, the English one
    # starts being preferred in English, and `entities.name` follows the
    # labels (docs/identity.md). `was` stays only so `unmerge_run` knows
    # which label to prefer again.
    add_label(
        con,
        entity_id,
        was,
        lang=lang,
        kind="alt",
        producer=producer,
        run=run,
        confidence=confidence,
        was=True,
    )
    add_label(
        con,
        entity_id,
        english,
        lang=language.canonical(),
        kind="pref",
        producer=producer,
        run=run,
        confidence=confidence,
    )
    con.commit()
    out["action"] = "renamed"
    return out


@_reading
def foreign_names(
    con: sqlite3.Connection, *, limit: int = 200, skip: tuple[int, ...] = ()
) -> list[dict[str, Any]]:
    """Entities whose name is not the word English uses for the thing.

    Three conditions, cheapest first. The type names a kind of thing, so
    the name may be translated at all (``naming: common``, invariant 9).
    Every document behind it is in one language and that language is not
    English — a term that an English document also uses is that
    document's word, not a translation. And the name occurs nowhere in
    the English half of the library (``vocabulary.in_english_text``),
    which is the dictionary this uses instead of a rule per language.

    An entity a pass has already decided is passed over — a label under
    ``vocabulary`` — so a run picks up where the last one stopped. The
    most connected first: the whole point is the edges the two halves of
    a name divide between them.

    **What the corpus rules out is recorded.** The third condition costs
    an FTS lookup a name, and on an exhausted queue it was paid for every
    candidate on every ask: 87 seconds to answer "nothing", which is why
    this step could not be one a worker asks for by itself. The ruling is
    monotone — a name that occurs in an English document will always
    occur in one, since documents are retired and never deleted — so it
    is worth keeping. An entity the corpus rules out is marked
    ``vocabulary:corpus``, which is a truthful thing to say about it: the
    library's own text was asked, and this name is already the word the
    library uses.
    """
    from prax import ontology, vocabulary

    common = sorted(ontology.current().common_types)
    if not common:
        return []
    marks = ",".join("?" * len(common))
    rows = con.execute(
        f"""
        SELECT e.id, e.name, e.type, count(DISTINCT x.doc) AS docs,
               count(*) AS edges,
               group_concat(DISTINCT x.lang) AS langs
          FROM entities e
          JOIN (SELECT src AS ent, source_doc AS doc,
                       json_extract(d.meta, '$.lang') AS lang
                  FROM edges JOIN documents d ON d.id = source_doc
                 WHERE valid_to IS NULL
                 UNION ALL
                SELECT dst, source_doc, json_extract(d.meta, '$.lang')
                  FROM edges JOIN documents d ON d.id = source_doc
                 WHERE valid_to IS NULL) x ON x.ent = e.id
         WHERE e.canonical_id IS NULL AND e.type IN ({marks})
           AND NOT EXISTS (SELECT 1 FROM entity_labels l
                            WHERE l.entity_id = e.id
                              AND l.producer LIKE 'vocabulary%')
         GROUP BY e.id
        HAVING langs IS NOT NULL AND langs NOT LIKE '%en%' AND langs NOT LIKE '%,%'
         ORDER BY edges DESC, docs DESC, e.id
        """,
        tuple(common),
    ).fetchall()
    out: list[dict[str, Any]] = []
    ruled_out: list[tuple[int, str]] = []
    for r in rows:
        if r["id"] in skip:
            continue
        if vocabulary.in_english_text(con, r["name"]):
            ruled_out.append((int(r["id"]), str(r["name"])))
            continue
        out.append(
            {
                "id": int(r["id"]),
                "name": str(r["name"]),
                "type": str(r["type"]),
                "lang": str(r["langs"]),
                "docs": int(r["docs"]),
                "edges": int(r["edges"]),
            }
        )
        if len(out) >= limit:
            break
    if ruled_out:
        _mark_corpus_ruling(con, ruled_out)
    return out


@_serialized
def _mark_corpus_ruling(
    con: sqlite3.Connection, ruled_out: list[tuple[int, str]]
) -> int:
    """Record that the library's own text answered for these names.

    The entity already carries the label (every one does since migration
    23); this says who decided it and on what evidence, which is what
    keeps the pass from asking the corpus about it again.
    """
    con.executemany(
        "UPDATE entity_labels SET producer = 'vocabulary:corpus'"
        " WHERE entity_id = ? AND label = ? COLLATE NOCASE"
        " AND (producer IS NULL OR producer = 'baseline')",
        ruled_out,
    )
    con.commit()
    return len(ruled_out)


@_serialized
def replace_entity_candidates(
    con: sqlite3.Connection,
    etype: str,
    pairs: list[tuple[int, int, float]],
    *,
    producer: str,
) -> int:
    """A worker's likely pairs for one type replace the type's earlier
    undecided ones: an unordered pair each (``a < b``) with the cosine of
    the two names. Pairs naming an entity that is not of that type, or no
    longer unmerged, are left out; a pair already decided keeps its
    decision (the question cost money once)."""
    ids = {
        int(r["id"])
        for r in con.execute(
            "SELECT id FROM entities WHERE type = ? AND canonical_id IS NULL", (etype,)
        )
    }
    rows = []
    for x, y, score in pairs:
        a, b = (int(x), int(y)) if int(x) < int(y) else (int(y), int(x))
        if a != b and a in ids and b in ids:
            rows.append((a, b, etype, float(score), producer))
    con.execute(
        "DELETE FROM entity_candidates WHERE type = ? AND decided IS NULL", (etype,)
    )
    con.executemany(
        "INSERT OR IGNORE INTO entity_candidates (a, b, type, score, producer, at)"
        f" VALUES (?, ?, ?, ?, ?, {_NOW})",
        rows,
    )
    con.commit()
    return len(rows)


@_serialized
def decide_candidates(
    con: sqlite3.Connection, pairs: list[tuple[int, int]], *, by: str
) -> int:
    """An adjudicator (a model, a person) said these pairs are different
    things: marked so, they leave the plan and are not asked about again."""
    rows = [
        ((min(a, b), max(a, b)), by) for a, b in ((int(x), int(y)) for x, y in pairs)
    ]
    before = con.total_changes
    # ``at`` stays the computation's time: it is what says a type is due
    con.executemany(
        "UPDATE entity_candidates SET decided = 'different', decided_by = ?"
        " WHERE a = ? AND b = ? AND decided IS NULL",
        [(who, a, b) for (a, b), who in rows],
    )
    n = con.total_changes - before
    con.commit()
    return n


def entity_candidates(
    con: sqlite3.Connection, etype: str | None = None
) -> list[dict[str, Any]]:
    """The likely pairs a worker left and nobody has decided, highest
    cosine first, with both names; a pair one of whose entities has been
    merged since is skipped (it was decided, or is moot)."""
    rows = con.execute(
        "SELECT c.a, c.b, c.type, c.score, c.producer, c.at,"
        " ea.name AS a_name, eb.name AS b_name"
        " FROM entity_candidates c"
        " JOIN entities ea ON ea.id = c.a JOIN entities eb ON eb.id = c.b"
        " WHERE ea.canonical_id IS NULL AND eb.canonical_id IS NULL"
        " AND c.decided IS NULL"
        + (" AND c.type = ?" if etype else "")
        + " ORDER BY c.score DESC, c.a, c.b",
        (etype,) if etype else (),
    ).fetchall()
    return [dict(r) for r in rows]


def candidate_runs(con: sqlite3.Connection) -> dict[str, str]:
    """When each type's likely pairs were last computed (its newest row's
    stamp); a type with no pairs left has no entry."""
    rows = con.execute(
        "SELECT type, max(at) AS at FROM entity_candidates GROUP BY type"
    ).fetchall()
    return {str(r["type"]): str(r["at"]) for r in rows}


@_reading
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


@_reading
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


@_reading
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


@_reading
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


@_reading
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


@_reading
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
    (``meta.extraction.ontology_version``): the ones whose text was read
    again out from under an extraction first, then oldest first. ``min_chars``
    skips documents whose chunks hold less text than that (Zotero notes,
    scans without a text layer): nothing to extract, a call wasted.
    ``domain`` keeps the documents assigned to that module; with ``onto``
    a document with a domain set is compared against the version of its
    own subset of the ontology, not the whole (one query: a CASE over
    the domain sets in use). ``sources`` keeps documents of those
    ``meta.source`` values (captures); ``skip_mime_prefix`` drops images
    and the like."""
    stamp = "json_extract(meta, '$.extraction.ontology_version')"
    base = (
        "SELECT id FROM documents WHERE text_hash IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    )
    sql = ""  # the predicates every variant shares, after the scope's own
    args: list[Any] = []
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
    # a document whose text was read again out from under its extraction
    # (meta.extraction_stale) goes before the never-extracted backlog:
    # someone cared enough to re-read it, and the graph speaks of a text
    # that is gone until it is read again
    sql += " ORDER BY json_extract(meta, '$.extraction_stale') IS NULL, id"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    if sources is None:
        return [r["id"] for r in con.execute(base + sql, args)]
    # a scope of sources (the captures): the source index answers it. A
    # document whose reading a person asked for is due whatever its
    # source, like a promote flag; it goes first, from its own partial
    # index (an OR of the two turned the source index into a scan)
    asked = " AND json_extract(meta, '$.extraction_stale.requested') IS NOT NULL"
    marks = ",".join("?" * len(sources))
    by_source = f" AND json_extract(meta, '$.source') IN ({marks})"
    out = [r["id"] for r in con.execute(base + asked + sql, args)]
    seen = set(out)
    for r in con.execute(base + by_source + sql, [*sources, *args]):
        if r["id"] not in seen:
            out.append(r["id"])
    return out[:limit] if limit is not None else out


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


@_reading
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
            UNION
            -- a name the entity is known by is an entry too: after a
            -- rename the document's own word is only a label, and a walk
            -- from it would otherwise start nowhere
            SELECT COALESCE(e.canonical_id, e.id)
              FROM entity_labels l JOIN entities e ON e.id = l.entity_id
             WHERE l.label = ? COLLATE NOCASE
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
        (entity_name, entity_name, hops, hops),
    ).fetchall()
    return [dict(r) for r in rows]
