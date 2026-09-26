"""Walking the graph from an entity: the first hop, every fact it carries,
and the second, a ranked and capped map of what the documents around it
are also about (invariant 6)."""

from __future__ import annotations

import sqlite3
from typing import Any

from prax import config, ontology

from ..base import _reading

MAX_HOPS = 2

# What the second hop keeps. Every edge within two hops of a
# well-connected entity is thousands of rows and megabytes of JSON, which
# is a map returned as the territory: 7,032 edges and 3.4 MB for
# `Fourier transform`, 22% of it the bibliographies of the papers the
# first hop reached. The measurement, and why a ranking by independent
# evidence beats the cleverer ones, is
# `docs/eval/traverse-neighbourhood-2026-09-25.md`.
EDGES = 150  # of an entity's own edges, the commonest relations first
NEIGHBOURS = 40  # hop-2 neighbours kept in all
PER_TYPE = 12  # …and at most this many of any one type, so that the
# papers and the authors do not crowd out the ideas
MIN_DOCUMENTS = 1  # …each backed by at least this many documents (94% of


def _neighbourhood_limits() -> tuple[int, int, int]:
    """How wide a map this host wants (``graph:`` in prax.yaml)."""
    return (
        config.whole("graph.neighbours", "PRAX_GRAPH_NEIGHBOURS", NEIGHBOURS),
        config.whole("graph.per_type", "PRAX_GRAPH_PER_TYPE", PER_TYPE),
        config.whole("graph.min_documents", "PRAX_GRAPH_MIN_DOCUMENTS", MIN_DOCUMENTS),
    )


def _first_hop(
    near: list[dict[str, Any]], limit: int
) -> tuple[list[dict[str, Any]], int]:
    """A hub's own edges, capped, with every relation represented.

    The first hop is a fact list and stays one — but `nonnegative matrix
    factorization` carries 642 edges and answers with 319 KB, which is
    the same breach of invariant 6 the second hop had, one level down.
    Taking the first 150 rows would take them in id order, which is the
    order they were extracted in: 229 `about` edges before the first
    `implements`. So the cap is spent round-robin over the relations,
    commonest first, and every relation an entity has appears before any
    relation has a second row.

    A caller that wants the whole list asks for it (``limit=0``), which
    is what the UI's canvas does.
    """
    if limit <= 0 or len(near) <= limit:
        return near, 0
    by_rel: dict[str, list[dict[str, Any]]] = {}
    for e in near:
        by_rel.setdefault(str(e.get("rel") or ""), []).append(e)
    order = sorted(by_rel, key=lambda r: (-len(by_rel[r]), r))
    kept: list[dict[str, Any]] = []
    round_ = 0
    while len(kept) < limit:
        took = False
        for rel in order:
            rows = by_rel[rel]
            if round_ < len(rows):
                kept.append(rows[round_])
                took = True
                if len(kept) >= limit:
                    break
        if not took:
            break
        round_ += 1
    keep = {id(e) for e in kept}
    return [e for e in near if id(e) in keep], len(near) - len(kept)


def _second_hop(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """The second hop as a map: which ideas the neighbourhood holds, not
    every edge that reaches them. Returns the shaped rows, and how many
    neighbours were left out of them.

    Three rules, each measured rather than guessed
    (``docs/eval/traverse-neighbourhood-2026-09-25.md``):

    *It may pass through a document but not land on one.* A document node
    is a record, and expanding one returns its catalogue card — the first
    hop reaches a hundred papers and the second returns their
    bibliographies, which was 22% of the payload. Passing through a
    document is how two ideas are connected at all, so only the
    destination is checked, and against the ontology's own hierarchy
    rather than a list of type names kept here.

    *Rank by independent evidence.* A neighbour is worth as many documents
    as separately say so. That is invariant 8 read as a ranking; it needs
    no degree lookup, which matters because degree by canonical id
    undercounts (edge rows keep the alias ids they were written with);
    and it beat both inverse-degree weighting and Zhou's resource
    allocation on the same neighbourhood.

    *A quota per type.* Ranking alone gives 44 papers in 50, because a
    paper accumulates edges mechanically while a concept does not.

    The first hop is untouched: it is a fact list rather than a map, it
    is already small, and the UI and the surfer only ever ask for one.
    """
    near = [r for r in rows if int(r["hop"]) < 2]
    far = [r for r in rows if int(r["hop"]) >= 2]
    if not far:
        return near, 0

    onto = ontology.current()
    reached = {r["src"] for r in near} | {r["dst"] for r in near}

    docs: dict[str, set[Any]] = {}
    kind: dict[str, str] = {}
    edges: dict[str, list[dict[str, Any]]] = {}
    for r in far:
        if r["src"] in reached:
            name, type_ = r["dst"], r["dst_type"]
        elif r["dst"] in reached:
            name, type_ = r["src"], r["src_type"]
        else:
            continue
        if name in reached:
            continue  # already met at the first hop
        if type_ and onto.is_a(type_, "document"):
            continue  # a bibliography entry, not a neighbour
        docs.setdefault(name, set()).add(r["source_doc"])
        kind[name] = type_
        edges.setdefault(name, []).append(r)

    limit, per_type, least = _neighbourhood_limits()
    taken: dict[str, int] = {}
    keep: list[str] = []
    for name in sorted(docs, key=lambda n: (-len(docs[n]), n)):
        if len(docs[name]) < least:
            continue
        type_ = kind[name] or "?"
        if taken.get(type_, 0) >= per_type:
            continue
        taken[type_] = taken.get(type_, 0) + 1
        keep.append(name)
        if len(keep) >= limit:
            break

    out = list(near)
    for name in keep:
        rows_ = edges[name]
        out.append(
            {
                "name": name,
                "type": kind[name],
                "via": sorted({str(r["rel"]) for r in rows_}),
                "documents": len(docs[name]),
                "source_docs": sorted(d for d in docs[name] if d is not None)[:5],
                "hop": 2,
            }
        )
    return out, len(docs) - len(keep)


@_reading
def traverse(
    con: sqlite3.Connection, entity_name: str, hops: int = 1, limit: int | None = None
) -> list[dict[str, Any]]:
    """The entity's own edges: every currently-valid one, with its evidence.

    This is the first hop, which is a fact list — what the UI draws and
    what the surfer reads. ``hops`` beyond 1 does not add edges here,
    because the second hop is a different kind of thing; ask
    ``traverse_map`` for it.
    """
    return [r for r in _walk(con, entity_name, hops, limit)[0] if int(r["hop"]) < 2]


@_reading
def traverse_map(
    con: sqlite3.Connection, entity_name: str, hops: int = 1, limit: int | None = None
) -> dict[str, Any]:
    """The neighbourhood of an entity: its own edges, the ideas around
    them, and how many of those did not fit.

    Two keys rather than one list, because the two hops are not the same
    kind of thing. ``edges`` is the fact list — every edge the entity
    itself carries, with its evidence. ``neighbours`` is the map: what
    the documents around it are also about, each with the relations that
    reach it and the number of documents that separately say so. A map
    that silently dropped the rest would be worse than a large one, so
    ``left_out`` counts the neighbours past the limits.
    """
    rows, left_out = _walk(con, entity_name, hops, limit)
    return {
        "entity": entity_name,
        "hops": max(0, min(hops, MAX_HOPS)),
        "edges": [r for r in rows if int(r["hop"]) < 2],
        "neighbours": [r for r in rows if int(r["hop"]) >= 2],
        "left_out": left_out,
    }


def _walk(
    con: sqlite3.Connection, entity_name: str, hops: int, limit: int | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    hops = max(0, min(hops, MAX_HOPS))
    if limit is None:
        limit = config.whole("graph.edges", "PRAX_GRAPH_EDGES", EDGES)
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
    shaped, neighbours_left = _second_hop([dict(r) for r in rows])
    near = [r for r in shaped if int(r["hop"]) < 2]
    far = [r for r in shaped if int(r["hop"]) >= 2]
    near, edges_left = _first_hop(near, limit)
    return near + far, {"edges": edges_left, "neighbours": neighbours_left}
