"""Walking the graph from an entity: the first hop, every fact it carries,
and the second, a ranked and capped map of what the documents around it
are also about (invariant 6)."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from prax import config
from prax.graph import ontology
from prax.text import dates

from ..base import VIEWER, _reading, domain_clause, hidden_documents, now
from .communities import community_of
from .edges import (
    _MOMENT_ISO,
    _end_of,
    changed_between,
    document_node,
    held_at,
    hidden_by_premise,
)

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

    A fact several documents state is one row per document, and each row
    says how many there are (``support``, absent for one). The cap is
    spent on distinct facts first, the best supported first within a
    relation, and a fact's further rows only after every distinct fact
    has had its place: twenty papers saying a method is `about` audio
    are one fact with twenty witnesses, not twenty slots.

    A caller that wants the whole list asks for it (``limit=0``), which
    is what the UI's canvas does.
    """
    witnesses: dict[tuple[str, str, str], set[Any]] = {}
    for e in near:
        witnesses.setdefault(_fact(e), set()).add(e.get("source_doc"))
    for e in near:
        n = len(witnesses[_fact(e)])
        if n > 1:
            e["support"] = n
    if limit <= 0 or len(near) <= limit:
        return near, 0
    firsts: dict[str, list[dict[str, Any]]] = {}
    repeats: dict[str, list[dict[str, Any]]] = {}
    met: set[tuple[str, str, str]] = set()
    for e in near:
        rel = str(e.get("rel") or "")
        (repeats if _fact(e) in met else firsts).setdefault(rel, []).append(e)
        met.add(_fact(e))
    for rows in firsts.values():
        rows.sort(key=lambda e: -int(e.get("support") or 1))  # stable: id order
    order = sorted(firsts, key=lambda r: (-len(firsts[r]), r))
    kept = _round_robin(firsts, order, limit)
    if len(kept) < limit:
        kept += _round_robin(repeats, sorted(repeats), limit - len(kept))
    keep = {id(e) for e in kept}
    return [e for e in near if id(e) in keep], len(near) - len(kept)


def _fact(e: dict[str, Any]) -> tuple[str, str, str]:
    """What an edge states, whoever states it: its two ends (canonical
    names) and its relation."""
    return (str(e.get("src")), str(e.get("rel")), str(e.get("dst")))


def _round_robin(
    by_rel: dict[str, list[dict[str, Any]]], order: list[str], limit: int
) -> list[dict[str, Any]]:
    """Up to ``limit`` rows, one from each relation in ``order`` per
    round, so every relation appears before any has a second row."""
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
    return kept


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
def senses(con: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    """The things a name reaches, most connected first: each canonical
    entity called ``name`` or answering to it as a label, with its
    ``type``, its live ``edges``, the ``documents`` behind them and the
    ``domains`` those are in (most first).

    A name is not an identity. 3,739 names are held by more than one
    entity, and most of those are a document beside its topic, the paper
    *Timbre* and the concept (docs/eval/fractured-names-2026-09-27.md):
    two things, which a walk from the name used to merge into one answer.
    """
    ids = [
        int(r[0])
        for r in con.execute(
            "SELECT COALESCE(canonical_id, id) FROM entities WHERE name = ?"
            " UNION SELECT COALESCE(e.canonical_id, e.id)"
            " FROM entity_labels l JOIN entities e ON e.id = l.entity_id"
            " WHERE l.label = ? COLLATE NOCASE",
            (name, name),
        )
    ]
    hidden = hidden_documents(con)
    out = []
    for cid in ids:
        row = con.execute(
            "SELECT id, name, type FROM entities WHERE id = ?", (cid,)
        ).fetchone()
        if row is None:
            continue
        # its edges read once, each end through its own index: an OR of the
        # two, asked again for the domains, was 200-400 ms in front of every
        # walk of a well-connected name
        group = [
            int(r[0])
            for r in con.execute(
                "SELECT id FROM entities WHERE id = ? OR canonical_id = ?", (cid, cid)
            )
        ]
        marks = ",".join("?" * len(group))
        edges = {
            int(r[0]): r[1]
            for end in ("src", "dst")
            for r in con.execute(
                f"SELECT id, source_doc FROM edges WHERE {end} IN ({marks})"
                " AND valid_to IS NULL",
                group,
            )
        }
        # what a hidden document says is not there for this viewer
        edges = {k: d for k, d in edges.items() if d not in hidden}
        docs = sorted({d for d in edges.values() if d is not None})
        domains = [
            str(r[0])
            for r in con.execute(
                "SELECT j.value, count(*) FROM documents d,"
                " json_each(d.meta, '$.domains') j"
                " WHERE d.id IN (SELECT value FROM json_each(?))"
                " GROUP BY 1 ORDER BY 2 DESC",
                (json.dumps(docs),),
            )
        ]
        out.append(
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "type": str(row["type"]),
                "edges": len(edges),
                "documents": len(docs),
                "domains": domains,
            }
        )
    # a sense nothing says anything about is a name left over, not a thing
    said = [x for x in out if x["edges"]]
    if VIEWER.get() is None:
        said = said or out  # a restricted viewer never meets a name left over
    return sorted(said, key=lambda x: (-x["edges"], x["id"]))


def _kinds(found: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The senses gathered into the things they are: one type twice (a
    merge resolution missed) or a type and its subtype (an author who is
    a person) are one thing; unrelated types are two. Most connected
    first."""
    onto = ontology.current()

    def related(a: str, b: str) -> bool:
        return a == b or onto.is_a(a, b) or onto.is_a(b, a)

    kinds: list[list[dict[str, Any]]] = []
    for sense in found:
        home = next(
            (k for k in kinds if any(related(sense["type"], x["type"]) for x in k)),
            None,
        )
        if home is None:
            kinds.append([sense])
        else:
            home.append(sense)
    return sorted(kinds, key=lambda k: -sum(x["edges"] for x in k))


_DOC_REF = re.compile(r"doc:(\d+)")


def _from_document(
    con: sqlite3.Connection, name: str, etype: str | None
) -> tuple[str, str | None]:
    """``doc:N`` as a walk's start: the document's node (its title and
    type, ``document_node``). A document that is missing or hidden stays
    ``doc:N``, which names nothing, so the walk is empty as for an unknown
    name."""
    m = _DOC_REF.fullmatch(name.strip())
    if not m:
        return name, etype
    try:
        title, own = document_node(con, int(m.group(1)))
    except KeyError:
        return name, etype
    return title, etype or own


def _choose(
    con: sqlite3.Connection, name: str, etype: str | None
) -> tuple[list[int], list[dict[str, Any]]]:
    """Which entities a walk from ``name`` starts at, and the senses to
    report. The thing of ``etype`` when one is asked for, else the most
    connected; the others are named, never merged in."""
    kinds = _kinds(senses(con, name))
    chosen: list[dict[str, Any]] | None = None
    if etype:
        onto = ontology.current()
        chosen = next(
            (
                k
                for k in kinds
                if any(
                    x["type"] == etype
                    or onto.is_a(x["type"], etype)
                    or onto.is_a(etype, x["type"])
                    for x in k
                )
            ),
            None,
        )
    elif kinds:
        chosen = kinds[0]
    report = []
    for k in kinds:
        lead = k[0]
        report.append(
            {
                "name": lead["name"],
                "type": lead["type"],
                "types": sorted({x["type"] for x in k}),
                "edges": sum(x["edges"] for x in k),
                "documents": sum(x["documents"] for x in k),
                "domains": list(dict.fromkeys(d for x in k for d in x["domains"]))[:3],
                "walked": k is chosen,
            }
        )
    return ([x["id"] for x in chosen] if chosen else []), report


@_reading
def _world_said(row: dict[str, Any]) -> dict[str, Any]:
    """An edge's world time, said only when its source gave one: the
    answer stays small for the facts with none (invariant 6)."""
    for key in ("world_from", "world_from_precision", "world_to", "world_to_precision"):
        if row.get(key) is None:
            row.pop(key, None)
    return row


def traverse(
    con: sqlite3.Connection,
    entity_name: str,
    hops: int = 1,
    limit: int | None = None,
    *,
    type: str | None = None,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    """The entity's own edges: every currently-valid one, with its evidence.

    This is the first hop, which is a fact list — what the UI draws and
    what the surfer reads. ``hops`` beyond 1 does not add edges here,
    because the second hop is a different kind of thing; ask
    ``traverse_map`` for it. A name that reaches several things walks one
    (``senses``): the one of ``type``, else the most connected.

    ``as_of`` walks the edges prax held then (``held_at``): what the graph
    said on a day, before a later reading ended some of them. Entities
    are those of now: a merge since is followed as it stands.
    """
    entity_name, type = _from_document(con, entity_name, type)
    ids, _ = _choose(con, entity_name, type)
    return [
        r for r in _walk(con, ids, hops, limit, as_of=as_of)[0] if int(r["hop"]) < 2
    ]


@_reading
def traverse_map(
    con: sqlite3.Connection,
    entity_name: str,
    hops: int = 1,
    limit: int | None = None,
    *,
    type: str | None = None,
    domain: str | None = None,
    as_of: str | None = None,
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

    A name that reaches several things (`apple` the ingredient and Apple
    the company) walks one, the one of ``type`` or else the most
    connected, and ``senses`` names them all with the one walked marked:
    never merged, and never one-sided without saying so. A name that
    reaches one thing carries no ``senses``.

    ``community`` names the region of the library the entity walked is in
    and the part of it, with their labels and sizes (``prax.graph.communities``):
    a way from a fact to what region of the library it belongs to, a few
    dozen bytes. Absent for an entity outside the partition.

    ``domain`` keeps what that module's documents (and those of the
    modules built on it) say, as the overview's does (``graph_overview``):
    the documents no module was set for are left out.

    ``as_of`` walks the edges prax held then, as ``traverse`` does.
    """
    entity_name, type = _from_document(con, entity_name, type)
    ids, report = _choose(con, entity_name, type)
    rows, left_out = _walk(
        con, ids, hops, limit, within=_domain_documents(con, domain), as_of=as_of
    )
    out: dict[str, Any] = {
        "entity": entity_name,
        "hops": max(0, min(hops, MAX_HOPS)),
        "edges": [r for r in rows if int(r["hop"]) < 2],
        "neighbours": [r for r in rows if int(r["hop"]) >= 2],
        "left_out": left_out,
    }
    if len(report) > 1 or (type and not ids):
        out["senses"] = report
    region = community_of(con, ids[0]) if ids else []
    if region:
        out["community"] = region
    return out


def _domain_documents(
    con: sqlite3.Connection, domain: str | None
) -> frozenset[int] | None:
    """The documents whose set holds ``domain`` or a module built on it;
    None for no domain (everything)."""
    if not domain:
        return None

    clause, args = domain_clause(con, ontology.current().within(domain), unset=False)
    rows = con.execute(f"SELECT d.id FROM documents d WHERE 1 = 1{clause}", args)
    return frozenset(int(r[0]) for r in rows)


def _walk(
    con: sqlite3.Connection,
    start_ids: list[int],
    hops: int,
    limit: int | None = None,
    *,
    within: frozenset[int] | None = None,
    as_of: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    hops = max(0, min(hops, MAX_HOPS))
    held, held_args = held_at("e", as_of)
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
        f"""
        WITH RECURSIVE
        start(cid) AS (
            -- the canonical entities the walk was asked to start at
            -- (``_choose``: one thing a name reaches, not all of them)
            SELECT value FROM json_each(?)
        ),
        walk(id, depth) AS (
            SELECT n.id, 0 FROM entities n
            JOIN start s ON COALESCE(n.canonical_id, n.id) = s.cid
            UNION
            SELECT m.id, w.depth + 1
            FROM walk w
            JOIN edges e ON e.src = w.id AND {held}
            JOIN entities nb ON nb.id = e.dst
            JOIN entities m
                 ON COALESCE(m.canonical_id, m.id) = COALESCE(nb.canonical_id, nb.id)
            WHERE w.depth < ?
            UNION
            SELECT m.id, w.depth + 1
            FROM walk w
            JOIN edges e ON e.dst = w.id AND {held}
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
               e.valid_from, e.world_from, e.world_from_precision,
               e.world_to, e.world_to_precision,
               MAX(rs.depth, rd.depth) AS hop
        FROM edges e
        JOIN reach rs ON rs.id = e.src
        JOIN reach rd ON rd.id = e.dst
        JOIN entities s ON s.id = e.src
        JOIN entities cs ON cs.id = COALESCE(s.canonical_id, s.id)
        JOIN entities d ON d.id = e.dst
        JOIN entities cd ON cd.id = COALESCE(d.canonical_id, d.id)
        WHERE {held}
        ORDER BY hop, e.id
        """,
        (json.dumps(start_ids), *held_args, hops, *held_args, hops, *held_args),
    ).fetchall()
    hidden = hidden_documents(con)
    seen = [
        _world_said(dict(r))
        for r in rows
        if r["source_doc"] not in hidden
        and (within is None or r["source_doc"] in within)
    ]
    veiled = hidden_by_premise(
        con,
        [
            int(r["edge_id"])
            for r in seen
            if str(r.get("producer") or "").startswith("rule:")
        ],
        hidden,
    )
    if veiled:
        seen = [r for r in seen if int(r["edge_id"]) not in veiled]
    shaped, neighbours_left = _second_hop(seen)
    near = [r for r in shaped if int(r["hop"]) < 2]
    far = [r for r in shaped if int(r["hop"]) >= 2]
    near, edges_left = _first_hop(near, limit)
    _mark_disputed(con, near, hidden)
    return near + far, {"edges": edges_left, "neighbours": neighbours_left}


def _mark_disputed(
    con: sqlite3.Connection, rows: list[dict[str, Any]], hidden: frozenset[int]
) -> None:
    """A fact another one contradicts says so (``disputed``: how many),
    counting only the others the viewer may see; ``why`` lists them."""
    ids = [int(r["edge_id"]) for r in rows if r.get("edge_id") is not None]
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    counts: dict[int, int] = {}
    for mine, doc in con.execute(
        "SELECT c.edge_a, o.source_doc FROM edge_conflicts c"
        " JOIN edges o ON o.id = c.edge_b"
        f" WHERE c.ended_at IS NULL AND c.edge_a IN ({marks})"
        " UNION ALL SELECT c.edge_b, o.source_doc FROM edge_conflicts c"
        " JOIN edges o ON o.id = c.edge_a"
        f" WHERE c.ended_at IS NULL AND c.edge_b IN ({marks})",
        ids + ids,
    ):
        if doc is None or int(doc) not in hidden:
            counts[int(mine)] = counts.get(int(mine), 0) + 1
    for r in rows:
        n = counts.get(int(r.get("edge_id") or 0))
        if n:
            r["disputed"] = n


CHANGES_SHOWN = 20  # facts a side of a changes answer lists at most


def _span(since: str, until: str | None) -> tuple[str, str, str, str]:
    """A period as moments (record time) and as dates (world time): a date
    is its whole span, so "2026-09" runs from the first to the last moment
    of September; no ``until`` is now."""

    def parsed(value: str) -> tuple[str, str] | None:
        got = dates.parse(value)
        if got is None and not _MOMENT_ISO.fullmatch(value):
            raise ValueError(f"a date or a UTC moment, not {value!r}")
        return got

    start = parsed(since)
    if start is None:
        begin, begin_date = since, since[:10]
    else:
        begin_date = start[0]
        begin = begin_date + "-01-01"[len(begin_date) - 4 :] + "T00:00:00Z"
    if until:
        stop = parsed(until)
        end = until if stop is None else _end_of(stop)
        end_date = until[:10] if stop is None else stop[0]
    else:
        end = now()
        end_date = end[:10]
    if end < begin:
        raise ValueError("until comes before since")
    return begin, end, begin_date, end_date


def _visible_ids(
    con: sqlite3.Connection, ids: list[int], hidden: frozenset[int]
) -> list[int]:
    """Of these entities, those a live edge from a visible document (or
    from none) speaks of."""
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT src, dst, source_doc FROM edges WHERE valid_to IS NULL"
        f" AND (src IN ({marks}) OR dst IN ({marks}))",
        ids + ids,
    ).fetchall()
    keep = set(ids)
    seen: set[int] = set()
    for src, dst, doc in rows:
        if doc is None or int(doc) not in hidden:
            seen.update(x for x in (int(src), int(dst)) if x in keep)
    return [i for i in ids if i in seen]


def _entity_ids(con: sqlite3.Connection, name: str) -> list[int]:
    """Every entity that answers to a name (its own or a label), with the
    ones merged into it."""
    ids = {
        int(r[0])
        for r in con.execute(
            "SELECT id FROM entities WHERE name = ? COLLATE NOCASE UNION"
            " SELECT entity_id FROM entity_labels WHERE label = ? COLLATE NOCASE",
            (name, name),
        )
    }
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    canon = {
        int(r[0])
        for r in con.execute(
            f"SELECT COALESCE(canonical_id, id) FROM entities WHERE id IN ({marks})",
            sorted(ids),
        )
    }
    marks = ",".join("?" * len(canon))
    ids |= canon | {
        int(r[0])
        for r in con.execute(
            f"SELECT id FROM entities WHERE canonical_id IN ({marks})", sorted(canon)
        )
    }
    return sorted(ids)


# a world date meets a period when their spans meet: "2026" meets
# September 2026, and "2026-09-14" does too
def _world_side(column: str) -> str:
    """A world date that meets the period ``?1`` to ``?2``: each side read
    at the coarser of the two precisions."""
    c = f"e.{column}"
    return (
        f"{c} IS NOT NULL"
        f" AND substr({c}, 1, length(?1)) >= substr(?1, 1, length({c}))"
        f" AND substr({c}, 1, length(?2)) <= substr(?2, 1, length({c}))"
    )


_WORLD_SIDES = {
    "began": (_world_side("world_from"), "e.world_from"),
    "ended": (_world_side("world_to") + " AND e.world_to != 'unknown'", "e.world_to"),
}


def _change_sides(
    world: bool,
    rereadings: bool,
    span: tuple[str, str, str, str],
    hidden: frozenset[int],
) -> dict[str, tuple[str, str, list[str]]]:
    """The two sides of a period on one time, each its condition, the
    column that says when, and the condition's arguments."""
    begin, end, begin_date, end_date = span
    veil = json.dumps(sorted(hidden))
    sides: dict[str, tuple[str, str, list[str]]] = {}
    if world:
        for k, (cond, column) in _WORLD_SIDES.items():
            sides[k] = (cond, column, [begin_date, end_date])
    else:
        held, held_args = held_at("p", begin)
        for k in ("added", "ended"):
            cond, bounds, column = changed_between("e", k, begin, end)
            if not rereadings:
                # news only: a fact added that was held when the period
                # began, or ended while another edge still states it, is
                # a re-reading (a re-extraction, a citation matched again)
                same = "p.src = e.src AND p.rel = e.rel AND p.dst = e.dst"
                # only what the viewer may see makes a fact a re-reading
                seen, seen_args = "", []
                if hidden:
                    seen = (
                        " AND (p.source_doc IS NULL OR p.source_doc NOT IN"
                        " (SELECT value FROM json_each(?)))"
                    )
                    seen_args = [veil]
                if k == "added":
                    cond += (
                        f" AND NOT EXISTS (SELECT 1 FROM edges p WHERE {same}"
                        f" AND {held}{seen})"
                    )
                    bounds = [*bounds, *held_args, *seen_args]
                else:
                    cond += (
                        f" AND NOT EXISTS (SELECT 1 FROM edges p WHERE {same}"
                        f" AND p.id != e.id AND p.valid_to IS NULL{seen})"
                    )
                    bounds = [*bounds, *seen_args]
            sides[k] = (cond, column, bounds)
    return sides


@_reading
def changes(
    con: sqlite3.Connection,
    since: str,
    until: str | None = None,
    *,
    world: bool = False,
    entity: str | None = None,
    rel: str | None = None,
    domain: str | None = None,
    derived: bool = False,
    rereadings: bool = False,
    limit: int = CHANGES_SHOWN,
) -> dict[str, Any]:
    """What changed in a period, on one of the two times an edge carries.

    Record time (the default): the facts prax wrote in the period
    (``added``, by ``valid_from``) and those a later reading ended in it
    (``ended``, by ``valid_to``). World time (``world``): the facts that
    began in the world in the period (``began``, by ``world_from``) and
    those that ended in it (``ended``, by ``world_to``), as their sources
    state; a fact dated more coarsely than the period counts when its
    span meets it ("2026" meets September 2026). Each side gives its count
    by relation and the newest ``limit`` facts, newest first, with
    ``left_out``. ``entity`` keeps the facts an entity takes part in (by
    its name or a label), ``rel`` one relation, ``domain`` what that
    module's documents say. On record time a re-reading is left out unless
    ``rereadings``: a fact added that was already held when the period
    began, or ended while another edge still states it (a re-extraction,
    a citation matched again), so what is listed is what the library came
    to know or stopped knowing. The rule pass's derivations are left out
    unless ``derived``: it re-derives every night. A document hidden from
    the viewer counts as absent (the wall)."""
    begin, end, begin_date, end_date = _span(since, until)
    limit = max(1, min(int(limit), 200))
    where: list[str] = []
    args: list[Any] = []
    if rel:
        where.append("e.rel = ?")
        args.append(rel)
    hidden = hidden_documents(con)
    veil = json.dumps(sorted(hidden))
    if not derived:
        where.append("COALESCE(e.producer, '') NOT LIKE 'rule:%'")
    elif hidden:
        # a derivation stands on its premises: hidden with any of them
        where.append(
            "NOT (COALESCE(e.producer, '') LIKE 'rule:%' AND EXISTS (SELECT 1"
            " FROM edge_premises pp JOIN edges pe ON pe.id = pp.premise_id"
            " WHERE pp.edge_id = e.id AND pe.source_doc IN"
            " (SELECT value FROM json_each(?))))"
        )
        args.append(veil)
    if entity:
        ids = _entity_ids(con, entity)
        if ids and hidden:
            # a name only hidden documents speak of is as unknown as one
            # nobody does (the wall: hidden means absent, existence too)
            ids = _visible_ids(con, ids, hidden)
        if not ids:
            return {"since": since, "until": until, "unknown_entity": entity}
        marks = ",".join("?" * len(ids))
        where.append(f"(e.src IN ({marks}) OR e.dst IN ({marks}))")
        args += ids + ids
    join = ""
    if domain:
        clause, dargs = domain_clause(con, [domain], unset=False)
        join = " JOIN documents d ON d.id = e.source_doc"
        where.append("1 = 1" + clause)
        args += dargs
    sides = _change_sides(world, rereadings, (begin, end, begin_date, end_date), hidden)
    out: dict[str, Any] = {
        "since": since,
        "until": until,
        "time": "world" if world else "record",
    }
    for side, (cond, at, bounds) in sides.items():
        sql_where = " AND ".join([cond, *where])
        counts: dict[str, int] = {}
        total = 0
        for r in con.execute(
            f"SELECT e.rel, e.source_doc, count(*) FROM edges e{join}"
            f" WHERE {sql_where} GROUP BY e.rel, e.source_doc",
            [*bounds, *args],
        ):
            if r[1] is not None and int(r[1]) in hidden:
                continue
            counts[r[0]] = counts.get(r[0], 0) + int(r[2])
            total += int(r[2])
        facts: list[dict[str, Any]] = []
        offset = 0
        while len(facts) < min(limit, total):
            rows = con.execute(
                f"SELECT e.id, s.name AS src, e.rel, t.name AS dst, e.source_doc,"
                f" {at} AS at FROM edges e{join}"
                " JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
                f" WHERE {sql_where} ORDER BY {at} DESC, e.id DESC LIMIT ? OFFSET ?",
                [*bounds, *args, limit * 2, offset],
            ).fetchall()
            if not rows:
                break
            offset += len(rows)
            for r in rows:
                doc = r["source_doc"]
                if doc is not None and int(doc) in hidden:
                    continue
                fact = {
                    "edge_id": int(r["id"]),
                    "src": r["src"],
                    "rel": r["rel"],
                    "dst": r["dst"],
                    "at": r["at"],
                }
                if doc is not None:
                    fact["source_doc"] = int(doc)
                facts.append(fact)
                if len(facts) >= limit:
                    break
        out[side] = {
            "count": total,
            "by_rel": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "facts": facts,
            "left_out": total - len(facts),
        }
    return out
