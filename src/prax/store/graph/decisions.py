"""What waits for a decision: the review queue of facts that did not fit,
and the candidate pairs of entity resolution."""

from __future__ import annotations

import sqlite3
from typing import Any

from prax import ontology

from ..base import _NOW, _reading, _serialized


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
