"""What every ailment shares: its shape, the caps on one pass, and the
few repairs several ailments use (ending edges, closing review items and
jobs)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..graph import (
    invalidate_edge,
    resolve_review,
)
from ..jobs import job_finish

# How many rows one pass looks at and repairs; a bigger mess is cleared by
# running it again, which keeps a single call short and a single lock brief.
CAP = 5000


EXAMPLES = 6


@dataclass(frozen=True)
class Ailment:
    name: str
    what: str  # what is wrong, in a sentence
    fix: str  # what repairing does, or what to do about it by hand
    find: Callable[[sqlite3.Connection], list[dict[str, Any]]]
    repair: Callable[[sqlite3.Connection, list[dict[str, Any]]], int] | None = None
    # readings a person may ask for over everything found (a report-only
    # ailment's way on): each a label and the body of POST /readings/bulk
    offers: tuple[dict[str, Any], ...] = ()

    @property
    def repairable(self) -> bool:
        return self.repair is not None


def _live_edge_counts(con: sqlite3.Connection, ids: list[int]) -> dict[int, int]:
    """How many live edges each of these entities carries. Two indexed
    queries per batch: a correlated count with `src = ? OR dst = ?` cannot
    use either edge index and turns a check into a table scan per entity."""
    counts: dict[int, int] = {}
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        marks = ",".join("?" * len(batch))
        for side in ("src", "dst"):
            for entity_id, n in con.execute(
                f"SELECT {side}, count(*) FROM edges"
                f" WHERE valid_to IS NULL AND {side} IN ({marks})"
                f" GROUP BY {side}",
                batch,
            ):
                counts[entity_id] = counts.get(entity_id, 0) + n
    return counts


def _with_edge_counts(
    con: sqlite3.Connection, rows: list[sqlite3.Row]
) -> list[dict[str, Any]]:
    """Entity rows that carry at least one live edge, the busiest first."""
    counts = _live_edge_counts(con, [r["id"] for r in rows])
    found = [
        {"id": r["id"], "name": r["name"], "type": r["type"], "edges": counts[r["id"]]}
        for r in rows
        if counts.get(r["id"])
    ]
    return sorted(found, key=lambda r: -r["edges"])[:CAP]


def _entity_rows(
    con: sqlite3.Connection, where: str, args: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    return _with_edge_counts(
        con,
        con.execute(
            f"SELECT id, name, type FROM entities WHERE {where}", args
        ).fetchall(),
    )


def _invalidate(con: sqlite3.Connection, edge_ids: list[int]) -> int:
    done = 0
    for edge_id in edge_ids:
        try:
            invalidate_edge(con, edge_id)
        except (KeyError, ValueError):  # another pass ended it first
            continue
        done += 1
    return done


def _edges_of_entities(con: sqlite3.Connection, ids: list[int]) -> list[int]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return [
        r[0]
        for r in con.execute(
            f"SELECT id FROM edges WHERE valid_to IS NULL"
            f" AND (src IN ({marks}) OR dst IN ({marks}))",
            (*ids, *ids),
        )
    ]


def _repair_entities(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """End every live edge these entities carry. The entities themselves
    stay: an entity with no live edges is invisible, and its name is the
    record of what the extraction did."""
    return _invalidate(con, _edges_of_entities(con, [r["id"] for r in rows]))


def _repair_edges(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    return _invalidate(con, [r["id"] for r in rows])


def _repair_review(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    done = 0
    for row in rows:
        try:
            resolve_review(con, row["id"], "dropped")
        except (KeyError, ValueError):
            continue
        done += 1
    return done


def _repair_jobs(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    done = 0
    for row in rows:
        try:
            job_finish(con, row["id"], status="failed", note="closed by the heal pass")
        except (KeyError, ValueError):
            continue
        done += 1
    return done
