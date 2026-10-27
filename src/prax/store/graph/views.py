"""What the web UI draws from the graph: the map of the biggest hubs,
the marks on an entity, a document's own graph, the documents a view is
seeded from and the edges between them, and the Review page's lists of
likely pairs and suspect merges."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from prax.graph import ontology
from prax.text import names

from ..base import _guards, _reading, domain_clause, hidden_documents
from .edges import held_in_world
from .lookups import entity_named_in

HUB_TYPES = ("concept", "method", "tool", "dataset")


# How long the graph view's hubs are served after the graph has changed.
# Counting every entity's live edges is most of an overview, 0.97 s of
# its 1.0 on 296,505 live edges (2026-10-18), and while extraction runs
# the edges change every few seconds; the view is a map, and a minute
# old is new enough. The edges a hub carries are read live.
HUB_REFRESH = 60.0
HUBS_KEPT = 32  # distinct hub questions held (a domain, a scope, a date…)

_HUBS: dict[tuple[Any, ...], tuple[tuple[Any, ...], float, list[sqlite3.Row]]] = {}
_HUBS_LOCK = threading.Lock()


def graph_stamp(con: sqlite3.Connection) -> tuple[Any, ...]:
    """What moves when the graph does: an edge written or ended, an entity
    merged, a name changed (a rename writes a label). Each read on an
    index, about 25 ms together."""
    row = con.execute(
        "SELECT (SELECT max(id) FROM edges),"
        " (SELECT max(valid_to) FROM edges WHERE valid_to IS NOT NULL),"
        " (SELECT count(*) FROM entities WHERE canonical_id IS NULL),"
        " (SELECT max(id) FROM entity_labels)"
    ).fetchone()
    return tuple(row)


def _hubs(
    con: sqlite3.Connection, sql: str, args: tuple[Any, ...]
) -> list[sqlite3.Row]:
    """The rows of the hubs' query, served again while the graph has not
    changed, or changed less than ``HUB_REFRESH`` seconds ago."""
    where = str(con.execute("PRAGMA database_list").fetchone()[2])
    key = (where, sql, args)
    stamp = graph_stamp(con)
    now = time.monotonic()
    with _HUBS_LOCK:
        held = _HUBS.get(key)
    if held and (held[0] == stamp or now - held[1] < HUB_REFRESH):
        return held[2]
    rows = con.execute(sql, args).fetchall()
    with _HUBS_LOCK:
        _HUBS[key] = (stamp, now, rows)
        while len(_HUBS) > HUBS_KEPT:
            _HUBS.pop(next(iter(_HUBS)))
    return rows


@_reading
def hub_graph(
    con: sqlite3.Connection,
    *,
    limit: int = 30,
    types: tuple[str, ...] = HUB_TYPES,
    min_shared: int = 2,
    domain: str | None = None,
    within: frozenset[int] | None = None,
    entities: list[int] | None = None,
    world_at: str | None = None,
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
    in every module, and would put the whole library back.

    ``within`` keeps to the facts of those documents (a page's tree, a
    project, the personal ones left out: ``store.scope_documents``).
    ``entities`` draws those entities instead of the most connected (a
    region's members), of any type, with their degree and the facts among
    them. ``world_at`` keeps the facts that hold in the world at that
    date and the undated ones (``held_in_world``).

    A node carries ``docs``, the documents that say something of it in
    what is drawn, and ``region``, the region of the library it is in
    (level 0 of ``entity_communities``; absent outside the partition)."""
    limit = max(1, min(limit, 200))
    docs, docs_args = "", tuple[str, ...]()
    if domain:
        onto = ontology.current()
        modules = sorted(onto.within(domain))
        own = {
            t
            for name in modules
            if name in onto.modules
            for t in onto.modules[name].types
            if t not in onto.self_types
        }
        types = tuple(sorted(set(types) | own))
        # the documents no module was set for are left out (see above)
        clause, held = domain_clause(con, modules, unset=False)
        docs = f" AND source_doc IN (SELECT d.id FROM documents d WHERE 1 = 1{clause})"
        docs_args = tuple(held)
    if within is not None:
        docs += " AND source_doc IN (SELECT value FROM json_each(?))"
        docs_args = (*docs_args, json.dumps(sorted(within)))
    hidden = hidden_documents(con)
    if hidden:
        # a viewer's hubs: from what it may see, never a rule's edge
        docs += (
            " AND source_doc IS NOT NULL"
            " AND source_doc NOT IN (SELECT value FROM json_each(?))"
        )
        docs_args = (*docs_args, json.dumps(sorted(hidden)))
    if world_at:
        world, world_args = held_in_world("edges", world_at)
        docs += f" AND {world}"
        docs_args = (*docs_args, *world_args)
    pick, pick_args = f"e.type IN ({','.join('?' * len(types))})", tuple(types)
    if entities is not None:
        pick = "e.id IN (SELECT value FROM json_each(?))"
        pick_args = (json.dumps(sorted(set(entities))),)
        limit = max(1, min(len(entities), 200))
    nodes = _hubs(
        con,
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
        WHERE {pick} ORDER BY d.degree DESC, e.name LIMIT ?
        """,
        (*docs_args, *docs_args, *pick_args, limit),
    )
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
            {_on_x(docs)}
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
              AND x.{side} IN ({mm}){_on_x(docs)}
            """,
            (*members, *docs_args),
        ):
            touch.setdefault(cid, set()).add(doc)
    regions = {
        int(r[0]): int(r[1])
        for r in con.execute(
            "SELECT entity_id, community_id FROM entity_communities"
            f" WHERE level = 0 AND entity_id IN ({idmarks})",
            ids,
        )
    }
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
            {
                "name": r["name"],
                "type": r["type"],
                "degree": r["degree"],
                "docs": len(touch.get(r["id"], ())),
                **({"region": regions[r["id"]]} if r["id"] in regions else {}),
            }
            for r in nodes
        ],
        "edges": edges,
        "links": links[:300],
    }


MARKED = 200  # entities one call to ``entity_marks`` reads


@_reading
def entity_marks(
    con: sqlite3.Connection,
    things: list[tuple[str, str]],
    *,
    within: frozenset[int] | None = None,
) -> list[dict[str, Any]]:
    """What the graph view sizes and colours a node by, for things already
    drawn (``(name, type)`` pairs, at most ``MARKED``): ``docs``, how many
    documents say something of it (in ``within`` when given, never a hidden
    one), and ``region``, its region of the library, as ``hub_graph`` gives
    them for the hubs. A name that is no entity is left out."""
    hidden = hidden_documents(con)
    out: list[dict[str, Any]] = []
    for name, etype in things[:MARKED]:
        row = con.execute(
            "SELECT COALESCE(canonical_id, id) FROM entities"
            " WHERE name = ? AND type = ?",
            (name, etype),
        ).fetchone()
        if row is None:
            continue
        cid = int(row[0])
        docs = {
            int(r[0])
            for side in ("src", "dst")
            for r in con.execute(
                f"""
                SELECT DISTINCT x.source_doc FROM edges x
                WHERE x.valid_to IS NULL AND x.source_doc IS NOT NULL AND x.{side} IN
                  (SELECT id FROM entities WHERE COALESCE(canonical_id, id) = ?)
                """,
                (cid,),
            )
        } - hidden
        if within is not None:
            docs &= within
        got: dict[str, Any] = {"name": name, "type": etype, "docs": len(docs)}
        region = con.execute(
            "SELECT community_id FROM entity_communities"
            " WHERE entity_id = ? AND level = 0",
            (cid,),
        ).fetchone()
        if region is not None:
            got["region"] = int(region[0])
        out.append(got)
    return out


def _on_x(clause: str) -> str:
    """A clause written over ``edges`` unaliased, for a query that calls
    the table ``x``."""
    return clause.replace("edges.", "x.").replace("source_doc", "x.source_doc")


DOCUMENT_EDGES = 300  # a document's graph: a book says more than a page holds


@_guards("doc", list)
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
    """The likely pairs nobody has decided, the held ones first (a model
    said one thing and the merge waits for a person, ``held`` saying why),
    then the closest names, each side with its edges and a document naming
    it: the review page's "same thing?" list."""
    where = (
        " FROM entity_candidates c JOIN entities ea ON ea.id = c.a"
        " JOIN entities eb ON eb.id = c.b WHERE c.decided IS NULL"
        " AND ea.canonical_id IS NULL AND eb.canonical_id IS NULL"
        + (" AND c.type = ?" if etype else "")
    )
    args: list[Any] = [etype] if etype else []
    total = con.execute("SELECT count(*)" + where, args).fetchone()[0]
    rows = con.execute(
        "SELECT c.a, c.b, c.type, c.score, c.p_same, c.held"
        + where
        + " ORDER BY c.held IS NULL, c.score DESC, c.a, c.b LIMIT ? OFFSET ?",
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
                    # the adjudicator said one thing, and the merge waits
                    # for a person: why (``store.merge_risk``)
                    "held": r["held"],
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

    x, y = names.words(alias), names.words(into)
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


# -------------------------------------- a sub-graph, for export (prax.graph.graphio)


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
    if domain:  # the module alone already: ``only:craft`` is craft here
        clause, args = domain_clause(
            con, [domain.removeprefix(ontology.ONLY)], unset=False
        )
        ids |= {
            int(r[0])
            for r in con.execute(
                f"SELECT d.id FROM documents d WHERE 1 = 1{clause}{live}", args
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
