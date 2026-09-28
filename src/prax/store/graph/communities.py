"""The regions of the library as the store keeps them (``prax.graph.communities``
computes them): what the partition reads, the rebuild that keeps ids and
summaries across nights, and the reads that name an entity's region."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from typing import Any

from ..base import _reading, _serialized, now

CARRY = 0.5  # a new community takes the id and summary of an old one this close
FRESH = 0.8  # below this, what the summary was written about has moved
SUMMARY_MIN = 5  # communities smaller than this get no summary
LIST_MEMBERS = 8  # members named in a list of communities


@_reading
def communities_input(
    con: sqlite3.Connection, topical: frozenset[str]
) -> tuple[dict[int, set[int]], Counter[tuple[int, int]]]:
    """What the partition reads: per document, the canonical topical
    entities its live edges name, and the direct edges between two such
    entities, counted."""
    docs: dict[int, set[int]] = defaultdict(set)
    direct: Counter[tuple[int, int]] = Counter()
    rows = con.execute(
        """
        SELECT x.source_doc, s.id, s.type, t.id, t.type
        FROM edges x
        JOIN entities s0 ON s0.id = x.src
        JOIN entities t0 ON t0.id = x.dst
        JOIN entities s ON s.id = COALESCE(s0.canonical_id, s0.id)
        JOIN entities t ON t.id = COALESCE(t0.canonical_id, t0.id)
        WHERE x.valid_to IS NULL
        """
    )
    for doc, a, ta, b, tb in rows:
        ina, inb = ta in topical, tb in topical
        if doc is not None:
            if ina:
                docs[doc].add(a)
            if inb:
                docs[doc].add(b)
        if ina and inb and a != b:
            direct[(min(a, b), max(a, b))] += 1
    return dict(docs), direct


@_serialized
def replace_communities(
    con: sqlite3.Connection,
    regions: list[frozenset[int]],
    parts: list[tuple[int, frozenset[int]]],
    weight: dict[int, float],
    *,
    run: str,
) -> dict[str, Any]:
    """Replace the partition. A new community that overlaps an old one of
    its level by ``CARRY`` (Jaccard) or more takes its id, label and
    summary; the summary is marked stale when the overlap is under
    ``FRESH``, so the summaries step writes it again. Returns what
    happened, per level."""
    from prax.graph.communities import jaccard

    old: dict[int, dict[str, Any]] = {}
    for r in con.execute(
        "SELECT id, level, label, summary, summary_meta FROM communities"
    ):
        old[r["id"]] = {
            "level": r["level"],
            "label": r["label"],
            "summary": r["summary"],
            "meta": json.loads(r["summary_meta"]) if r["summary_meta"] else None,
            "members": set(),
        }
    where: dict[tuple[int, int], int] = {}  # (entity, level) -> old community
    for r in con.execute(
        "SELECT entity_id, level, community_id FROM entity_communities"
    ):
        if r["community_id"] in old:
            old[r["community_id"]]["members"].add(r["entity_id"])
            where[(r["entity_id"], r["level"])] = r["community_id"]
    taken: set[int] = set()
    next_id = max(old, default=0) + 1
    stamp = now()
    report: dict[str, Any] = {"levels": {}, "carried": 0, "stale": 0, "new": 0}

    def place(level: int, members: frozenset[int]) -> tuple[int, dict[str, Any]]:
        nonlocal next_id
        votes = Counter(where[(e, level)] for e in members if (e, level) in where)
        best, best_j = None, 0.0
        for cid, _ in votes.most_common(5):
            if cid in taken:
                continue
            j = jaccard(members, old[cid]["members"])
            if j > best_j:
                best, best_j = cid, j
        if best is not None and best_j >= CARRY:
            taken.add(best)
            report["carried"] += 1
            was = old[best]
            meta = dict(was["meta"] or {})
            if was["summary"] and (best_j < FRESH or meta.get("stale")):
                meta["stale"] = True
                report["stale"] += 1
            return best, {
                "label": was["label"],
                "summary": was["summary"],
                "meta": meta or None,
            }
        cid = next_id
        next_id += 1
        report["new"] += 1
        return cid, {"label": None, "summary": None, "meta": None}

    rows: list[tuple[Any, ...]] = []
    members_rows: list[tuple[int, int, int, float]] = []
    region_ids: list[int] = []
    for level, groups in ((0, [(None, r) for r in regions]), (1, parts)):
        report["levels"][level] = len(groups)
        for parent_index, members in groups:
            cid, kept = place(level, members)
            parent = region_ids[parent_index] if parent_index is not None else None
            if level == 0:
                region_ids.append(cid)
            rows.append(
                (
                    cid,
                    level,
                    parent,
                    len(members),
                    kept["label"],
                    kept["summary"],
                    json.dumps(kept["meta"]) if kept["meta"] else None,
                    run,
                    stamp,
                )
            )
            members_rows.extend(
                (e, level, cid, float(weight.get(e, 0.0))) for e in members
            )
    con.execute("DELETE FROM entity_communities")
    con.execute("DELETE FROM communities")
    con.executemany(
        "INSERT INTO communities (id, level, parent, size, label, summary,"
        " summary_meta, run, built_at) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.executemany(
        "INSERT INTO entity_communities (entity_id, level, community_id, weight)"
        " VALUES (?,?,?,?)",
        members_rows,
    )
    con.commit()
    return report


def _members(con: sqlite3.Connection, cid: int, limit: int) -> list[dict[str, Any]]:
    return [
        {"id": r["id"], "name": r["name"], "type": r["type"]}
        for r in con.execute(
            "SELECT e.id, e.name, e.type FROM entity_communities m"
            " JOIN entities e ON e.id = m.entity_id"
            " WHERE m.community_id = ? ORDER BY m.weight DESC, e.name LIMIT ?",
            (cid, limit),
        )
    ]


def _summary_state(row: sqlite3.Row) -> str:
    if not row["summary"]:
        return "none"
    meta = json.loads(row["summary_meta"]) if row["summary_meta"] else {}
    return "stale" if meta.get("stale") else "fresh"


@_reading
def list_communities(
    con: sqlite3.Connection,
    *,
    level: int = 0,
    parent: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """The communities of a level, largest first (a region's parts with
    ``parent``): the label, the size, the first sentence of the summary,
    the members that weigh most."""
    sql = "SELECT * FROM communities WHERE level = ?"
    args: list[Any] = [level]
    if parent is not None:
        sql += " AND parent = ?"
        args.append(parent)
    sql += " ORDER BY size DESC, id LIMIT ?"
    args.append(max(1, min(limit, 500)))
    out = []
    for r in con.execute(sql, args).fetchall():
        out.append(
            {
                "id": r["id"],
                "level": r["level"],
                "parent": r["parent"],
                "size": r["size"],
                "label": r["label"],
                "summary": (r["summary"] or "").split(". ")[0][:300] or None,
                "summary_state": _summary_state(r),
                "members": [m["name"] for m in _members(con, r["id"], LIST_MEMBERS)],
            }
        )
    return out


@_reading
def community(
    con: sqlite3.Connection,
    cid: int,
    *,
    members: int = 30,
    documents: int = 10,
) -> dict[str, Any] | None:
    """One community: its summary, its members by weight, its parts or its
    region, and the documents that name the most of its members."""
    r = con.execute("SELECT * FROM communities WHERE id = ?", (cid,)).fetchone()
    if r is None:
        return None
    docs = con.execute(
        """
        SELECT d.id, d.title, COUNT(DISTINCT m.entity_id) AS named
        FROM entity_communities m
        JOIN entities e0 ON COALESCE(e0.canonical_id, e0.id) = m.entity_id
        JOIN edges x ON (x.src = e0.id OR x.dst = e0.id) AND x.valid_to IS NULL
        JOIN documents d ON d.id = x.source_doc
        WHERE m.community_id = ? AND json_extract(d.meta, '$.retired') IS NULL
        GROUP BY d.id ORDER BY named DESC, d.id LIMIT ?
        """,
        (cid, max(1, min(documents, 50))),
    ).fetchall()
    return {
        "id": r["id"],
        "level": r["level"],
        "parent": r["parent"],
        "size": r["size"],
        "label": r["label"],
        "summary": r["summary"],
        "summary_state": _summary_state(r),
        "built_at": r["built_at"],
        "members": _members(con, cid, max(1, min(members, 200))),
        "parts": list_communities(con, level=1, parent=cid) if r["level"] == 0 else [],
        "documents": [
            {"id": d["id"], "title": d["title"], "named": d["named"]} for d in docs
        ],
    }


@_reading
def community_of(con: sqlite3.Connection, entity_id: int) -> list[dict[str, Any]]:
    """The region and the part an entity is in, coarse first; ``[]`` for
    an entity outside the partition (named once, or not a topical kind)."""
    rows = con.execute(
        """
        SELECT c.id, c.level, c.label, c.size FROM entity_communities m
        JOIN communities c ON c.id = m.community_id
        WHERE m.entity_id = (SELECT COALESCE(canonical_id, id) FROM entities
                             WHERE id = ?)
        ORDER BY c.level
        """,
        (entity_id,),
    ).fetchall()
    return [
        {"id": r["id"], "level": r["level"], "label": r["label"], "size": r["size"]}
        for r in rows
    ]


@_reading
def communities_to_summarize(con: sqlite3.Connection, *, limit: int = 10) -> list[int]:
    """Communities of ``SUMMARY_MIN`` members or more without a summary or
    with a stale one: the regions first, then the largest."""
    rows = con.execute(
        """
        SELECT id FROM communities
        WHERE size >= ? AND (summary IS NULL
              OR json_extract(summary_meta, '$.stale') = 1)
        ORDER BY level, size DESC, id LIMIT ?
        """,
        (SUMMARY_MIN, max(1, limit)),
    ).fetchall()
    return [int(r[0]) for r in rows]


@_serialized
def set_community_summary(
    con: sqlite3.Connection,
    cid: int,
    *,
    label: str,
    summary: str,
    source: str,
    run: str | None = None,
) -> bool:
    """A community's name and summary, written by the summaries step; the
    stale mark goes. False when the community is gone (a rebuild in
    between that did not carry it)."""
    meta = {"source": source, "run": run, "at": now()}
    cur = con.execute(
        "UPDATE communities SET label = ?, summary = ?, summary_meta = ? WHERE id = ?",
        (label.strip()[:120] or None, summary.strip() or None, json.dumps(meta), cid),
    )
    con.commit()
    return cur.rowcount > 0


REGION_MEMBERS = 30  # the members a region is described by for matching a query


@_reading
def regions_for_matching(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every described region and part, with what a query is matched
    against (``prax.graph.regions``): the name, the summary, the members
    that weigh most, and the stamp of the partition, so a cached index
    knows when to be built again."""
    out = []
    for r in con.execute(
        "SELECT id, level, parent, size, label, summary, run FROM communities"
        " WHERE label IS NOT NULL AND summary IS NOT NULL ORDER BY level, size DESC"
    ).fetchall():
        out.append(
            {
                "id": r["id"],
                "level": r["level"],
                "parent": r["parent"],
                "size": r["size"],
                "label": r["label"],
                "summary": r["summary"],
                "run": r["run"],
                "members": [m["name"] for m in _members(con, r["id"], REGION_MEMBERS)],
            }
        )
    return out


# Where a search's hits live ("A way in", docs/PLAN.md): the region and the
# part most of their entities belong to, each entity counted once (weighed
# by how central it was, one hub such as "Mac OS X" outvoted a recipe's
# ingredients), shown when the best one holds this share of them. Measured on
# the library's 62 queries (scripts/eval_regions.py,
# docs/eval/regions-2026-09-28.md): at half of them the region is right
# for 92% of the 82% of queries it is shown for; the part, among its
# region's parts, only at nine tenths (87% of 24%). Matching the query's
# own words against the regions' names and summaries was right under half
# the time and is not used.
REGION_HITS = 5
REGION_SHARE = 0.5
PART_SHARE = 0.9


@_reading
def regions_of(
    con: sqlite3.Connection,
    doc_ids: list[int],
    *,
    share: float = REGION_SHARE,
    part_share: float = PART_SHARE,
) -> dict[str, Any] | None:
    """The region and the part of the library these documents' entities
    belong to most, each with its share of their weight, when the region
    holds at least ``share`` of it; the part, among the region's parts, when
    it holds ``part_share`` of theirs.
    None when no region holds that much: the hits are spread."""
    if not doc_ids:
        return None
    marks = ",".join("?" * len(doc_ids))
    weight: dict[int, dict[int, float]] = {0: {}, 1: {}}
    for r in con.execute(
        "SELECT m.level, m.community_id, m.weight FROM edges e"
        " JOIN entities x ON x.id IN (e.src, e.dst)"
        " JOIN entity_communities m ON m.entity_id = coalesce(x.canonical_id, x.id)"
        f" WHERE e.valid_to IS NULL AND e.source_doc IN ({marks})",
        list(doc_ids),
    ):
        level = weight.setdefault(int(r["level"]), {})
        cid = int(r["community_id"])
        level[cid] = level.get(cid, 0.0) + 1.0  # one entity, one vote

    def top(
        level: int, need: float, parent: int | None = None
    ) -> dict[str, Any] | None:
        counts = weight.get(level) or {}
        if parent is not None:
            # a part of the region shown, never one of another region
            own = {
                int(r[0])
                for r in con.execute(
                    "SELECT id FROM communities WHERE parent = ?", (parent,)
                )
            }
            counts = {c: w for c, w in counts.items() if c in own}
        if not counts:
            return None
        cid = max(counts, key=lambda c: counts[c])
        held = counts[cid] / sum(counts.values())
        if held < need:
            return None
        row = con.execute(
            "SELECT id, label, size, summary FROM communities WHERE id = ?", (cid,)
        ).fetchone()
        if row is None or not row["label"]:
            return None
        return {
            "id": row["id"],
            "label": row["label"],
            "size": row["size"],
            "share": round(held, 2),
            "summary": (row["summary"] or "").split(". ")[0][:200] or None,
        }

    region = top(0, share)
    if region is None:
        return None
    return {
        "kind": "region",
        "region": region,
        "part": top(1, part_share, region["id"]),
    }
