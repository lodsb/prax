"""What the graph says about a document, for ask and the document page, and
the map of the biggest hubs."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..base import _guards, _reading, _scrubbed, hidden_documents
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
    domain: str | None = None,
) -> dict[str, Any]:
    """The most connected entities of the given types, the currently valid
    edges among them, and ``links``: pairs of hubs that share at least
    ``min_shared`` source documents (co-occurrence, the topic map). Degrees
    and edges are counted over canonical ids, like ``traverse``.

    Every step walks the edge indexes: the ends of the live edges as one
    list, each end mapped to its canonical entity by primary key. A join
    on ``c.id = x.src OR c.id = x.dst`` can use neither index and took
    two seconds on 126k edges; this takes a tenth of that.

    ``domain`` draws one module's graph: only what that module's documents
    (and those of the modules built on it) say, and its own kinds of thing
    among the hubs, the kitchen's ingredients and dishes beside the
    concepts. The documents no module was set for are left out: they are
    in every module, and would put the whole library back."""
    limit = max(1, min(limit, 200))
    docs, docs_args = "", tuple[str, ...]()
    if domain:
        from prax import ontology

        onto = ontology.current()
        within = sorted(onto.within(domain))
        own = {
            t
            for name in within
            if name in onto.modules
            for t in onto.modules[name].types
            if t not in onto.self_types
        }
        types = tuple(sorted(set(types) | own))
        dm = ",".join("?" * len(within))
        docs = (
            " AND source_doc IN (SELECT d.id FROM documents d WHERE EXISTS"
            " (SELECT 1 FROM json_each(d.meta, '$.domains') j"
            f" WHERE j.value IN ({dm})))"
        )
        docs_args = tuple(within)
    marks = ",".join("?" * len(types))
    nodes = con.execute(
        f"""
        WITH ends(id) AS (
            SELECT src FROM edges WHERE valid_to IS NULL{docs}
            UNION ALL
            SELECT dst FROM edges WHERE valid_to IS NULL{docs}
        ),
        deg(cid, degree) AS (
            SELECT COALESCE(n.canonical_id, n.id), count(*)
            FROM ends JOIN entities n ON n.id = ends.id
            GROUP BY COALESCE(n.canonical_id, n.id)
        )
        SELECT e.id, e.name, e.type, d.degree FROM deg d JOIN entities e ON e.id = d.cid
        WHERE e.type IN ({marks}) ORDER BY d.degree DESC, e.name LIMIT ?
        """,
        (*docs_args, *docs_args, *types, limit),
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
            {docs.replace("source_doc", "x.source_doc")}
            ORDER BY x.id
            """,
            (*members, *members, *docs_args),
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
              AND x.{side} IN ({mm}){docs.replace("source_doc", "x.source_doc")}
            """,
            (*members, *docs_args),
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


DOCUMENT_EDGES = 300  # a document's graph: a book says more than a page holds


@_reading
def document_edges(
    con: sqlite3.Connection, doc_id: int, *, limit: int = DOCUMENT_EDGES
) -> list[dict[str, Any]]:
    """What one document says, as the graph view draws it: its live edges
    in the shape ``traverse`` gives (canonical names, the evidence), its
    own entity's first, at most ``limit``. The way from a document page
    into the graph (niggles.txt: "per document maybe a link into its
    subgraph")."""
    rows = con.execute(
        """
        SELECT x.id AS edge_id, s.name AS src, s.type AS src_type, x.rel,
               t.name AS dst, t.type AS dst_type, x.confidence, x.source_doc,
               x.evidence, x.producer, x.run, 1 AS hop,
               (s0.name = d.title) AS own
        FROM edges x
        JOIN documents d ON d.id = x.source_doc
        JOIN entities s0 ON s0.id = x.src
        JOIN entities t0 ON t0.id = x.dst
        JOIN entities s ON s.id = COALESCE(s0.canonical_id, s0.id)
        JOIN entities t ON t.id = COALESCE(t0.canonical_id, t0.id)
        WHERE x.source_doc = ? AND x.valid_to IS NULL
        ORDER BY own DESC, x.id LIMIT ?
        """,
        (doc_id, max(1, limit)),
    ).fetchall()
    return [{k: v for k, v in dict(r).items() if k != "own"} for r in rows]


def _sides(con: sqlite3.Connection, ids: list[int]) -> dict[int, dict[str, Any]]:
    """Each entity with what a person needs to judge it: its name, type,
    the live edges of it and its aliases, and a document naming it. The
    edges are counted end by end through their indexes: a join on
    ``id IN (src, dst)`` scanned every edge, ten seconds a page."""
    out: dict[int, dict[str, Any]] = {}
    for eid in dict.fromkeys(ids):
        row = con.execute(
            "SELECT id, name, type FROM entities WHERE id = ?", (eid,)
        ).fetchone()
        if row is None:
            continue
        group = [eid] + [
            int(r[0])
            for r in con.execute(
                "SELECT id FROM entities WHERE canonical_id = ?", (eid,)
            )
        ]
        marks = ",".join("?" * len(group))
        edges = sum(
            con.execute(
                f"SELECT count(*) FROM edges WHERE valid_to IS NULL AND {side}"
                f" IN ({marks})",
                group,
            ).fetchone()[0]
            for side in ("src", "dst")
        )
        out[eid] = {
            "id": eid,
            "name": row["name"],
            "type": row["type"],
            "edges": int(edges),
            "document": entity_named_in(con, eid),
        }
    return out


@_reading
def candidates_page(
    con: sqlite3.Connection,
    *,
    etype: str | None = None,
    offset: int = 0,
    limit: int = 30,
) -> dict[str, Any]:
    """The likely pairs nobody has decided, closest names first, each
    side with its edges and a document naming it: the review page's
    "same thing?" list."""
    where = (
        " FROM entity_candidates c JOIN entities ea ON ea.id = c.a"
        " JOIN entities eb ON eb.id = c.b WHERE c.decided IS NULL"
        " AND ea.canonical_id IS NULL AND eb.canonical_id IS NULL"
        + (" AND c.type = ?" if etype else "")
    )
    args: list[Any] = [etype] if etype else []
    total = con.execute("SELECT count(*)" + where, args).fetchone()[0]
    rows = con.execute(
        "SELECT c.a, c.b, c.type, c.score, c.p_same"
        + where
        + " ORDER BY c.score DESC, c.a, c.b LIMIT ? OFFSET ?",
        [*args, max(1, min(limit, 200)), max(0, offset)],
    ).fetchall()
    sides = _sides(con, [x for r in rows for x in (r["a"], r["b"])])
    items = []
    for r in rows:
        a, b = sides.get(r["a"]), sides.get(r["b"])
        if a and b:
            # the one with more edges first: the one to keep, by default
            first, second = (a, b) if a["edges"] >= b["edges"] else (b, a)
            items.append(
                {
                    "type": r["type"],
                    "score": round(float(r["score"]), 4),
                    "p_same": None if r["p_same"] is None else round(r["p_same"], 3),
                    "keep": first,
                    "other": second,
                }
            )
    by_type = dict(
        con.execute(
            "SELECT c.type, count(*) FROM entity_candidates c"
            " JOIN entities ea ON ea.id = c.a JOIN entities eb ON eb.id = c.b"
            " WHERE c.decided IS NULL AND ea.canonical_id IS NULL"
            " AND eb.canonical_id IS NULL GROUP BY c.type"
        ).fetchall()
    )
    return {"total": int(total), "types": by_type, "items": items}


MERGE_TYPES = ("concept", "method", "tool", "dataset", "venue")
SPELLING = 0.8  # two words this alike (difflib) are one word spelled twice


def _suspect(alias: str, into: str) -> str | None:
    """Why a merge of these two names deserves a look, or None. Two shapes
    were wrong in the confidence pilot (docs/PLAN.md, Q): a general name
    folded into a variant of it (``Kalman smoother`` into ``extended
    Kalman smoother``: one name's words a strict part of the other's),
    and two names one word apart where the words are not one word spelled
    twice (``preorder traversal``, ``postorder traversal``). Spelling
    variants and translations share no such shape and are not listed."""
    import difflib

    from prax import resolution

    x, y = resolution.normalize(alias).split(), resolution.normalize(into).split()
    wx, wy = set(x), set(y)
    if wx == wy:
        return None
    if wx < wy or wy < wx:
        return "narrower"
    # the rest shared: two one-word names that differ share nothing, and a
    # translation is such a pair
    if len(x) == len(y) >= 2 and len(wx - wy) == 1 and len(wy - wx) == 1:
        (a,), (b,) = wx - wy, wy - wx
        if difflib.SequenceMatcher(None, a, b).ratio() < SPELLING:
            return "one word apart"
    return None


@_reading
def merges_page(
    con: sqlite3.Connection,
    *,
    offset: int = 0,
    limit: int = 30,
) -> dict[str, Any]:
    """Merges worth a second look (``_suspect``), in the likely tier's
    types, none a person has confirmed, none a translation (the vocabulary
    pass's merges differ by design), one word apart first. What a
    model doubts is the better order once measured (docs/PLAN.md, Q)."""
    confirmed = {
        (int(r[0]), int(r[1]))
        for r in con.execute(
            "SELECT a, b FROM entity_candidates WHERE decided = 'same'"
            " AND decided_by = 'human'"
        )
    }
    marks = ",".join("?" * len(MERGE_TYPES))
    found = []
    for r in con.execute(
        "SELECT a.id, a.name, a.type, a.merged_by, a.merged_run, b.id AS into_id,"
        " b.name AS into_name FROM entities a JOIN entities b ON b.id = a.canonical_id"
        f" WHERE a.canonical_id IS NOT NULL AND a.type IN ({marks})"
        " AND coalesce(a.merged_by, '') != 'vocabulary'",
        MERGE_TYPES,
    ):
        pair = (min(r["id"], r["into_id"]), max(r["id"], r["into_id"]))
        if pair in confirmed:
            continue
        why = _suspect(r["name"], r["into_name"])
        if why:
            found.append((why, r))
    # one word apart first: the smaller list and the more often wrong (a
    # sample of twenty: costs, traversals, MIDI files, archive calls)
    found.sort(key=lambda t: (t[0] != "one word apart", t[1]["into_name"], t[1]["id"]))
    page = found[max(0, offset) : max(0, offset) + max(1, min(limit, 200))]
    sides = _sides(con, [x for _, r in page for x in (r["id"], r["into_id"])])
    return {
        "total": len(found),
        "items": [
            {
                "why": why,
                "by": r["merged_by"],
                "run": r["merged_run"],
                "alias": sides.get(r["id"]),
                "into": sides.get(r["into_id"]),
            }
            for why, r in page
        ],
    }


# ------------------------------------------ a sub-graph, for export (prax.graphio)


@_reading
def seed_documents(
    con: sqlite3.Connection,
    *,
    project: str | None = None,
    domain: str | None = None,
    tag: str | None = None,
) -> list[int]:
    """The live documents a seed names: a project's (tagged
    ``project:<name>``, and its page ``project-<name>``), a domain's (set
    on the document; one without a set is in every module and is left
    out here), or a tag's. Ascending ids."""
    ids: set[int] = set()
    live = " AND json_extract(d.meta, '$.retired') IS NULL"
    for t in ([f"project:{project}"] if project else []) + ([tag] if tag else []):
        ids |= {
            int(r[0])
            for r in con.execute(
                "SELECT d.id FROM documents d WHERE EXISTS (SELECT 1 FROM"
                " json_each(d.meta, '$.tags') WHERE value = ?)" + live,
                (t,),
            )
        }
    if project:
        ids |= {
            int(r[0])
            for r in con.execute(
                "SELECT doc_id FROM pages WHERE slug = ?", (f"project-{project}",)
            )
        }
    if domain:
        ids |= {
            int(r[0])
            for r in con.execute(
                "SELECT d.id FROM documents d WHERE EXISTS (SELECT 1 FROM"
                " json_each(d.meta, '$.domains') WHERE value = ?)" + live,
                (domain,),
            )
        }
    return sorted(ids)


_SUBGRAPH_EDGE = """
    SELECT x.id, s.name AS src, s.type AS src_type, x.rel, t.name AS dst,
           t.type AS dst_type, x.confidence, x.evidence, x.producer, x.run,
           x.ontology_version, x.valid_from, x.valid_to, x.ingested_at,
           x.source_doc, d.hash AS source_hash
    FROM edges x
    JOIN entities s0 ON s0.id = x.src
    JOIN entities t0 ON t0.id = x.dst
    JOIN entities s ON s.id = COALESCE(s0.canonical_id, s0.id)
    JOIN entities t ON t.id = COALESCE(t0.canonical_id, t0.id)
    LEFT JOIN documents d ON d.id = x.source_doc
"""


@_reading
def subgraph_edges(
    con: sqlite3.Connection,
    *,
    doc_ids: list[int] | None = None,
    edge_ids: list[int] | None = None,
    history: bool = False,
) -> list[dict[str, Any]]:
    """The edges of those documents, or those edges, under the canonical
    names, with every provenance column and the source document's hash
    (its identity in another library). Live ones only unless ``history``.
    In a stable order (source document hash, then the triple) so an export
    diffs well."""
    out: list[dict[str, Any]] = []
    live = "" if history else " AND x.valid_to IS NULL"
    for column, ids in (("x.source_doc", doc_ids), ("x.id", edge_ids)):
        for start in range(0, len(ids or []), 500):
            part = (ids or [])[start : start + 500]
            marks = ",".join("?" * len(part))
            out.extend(
                dict(r)
                for r in con.execute(
                    _SUBGRAPH_EDGE + f" WHERE {column} IN ({marks})" + live, part
                )
            )
    seen: dict[int, dict[str, Any]] = {int(e["id"]): e for e in out}
    return sorted(
        seen.values(),
        key=lambda e: (
            e["source_hash"] or "",
            e["src_type"],
            e["src"],
            e["rel"],
            e["dst_type"],
            e["dst"],
            e["valid_from"] or "",
        ),
    )


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
