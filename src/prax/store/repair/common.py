"""What every ailment shares: its shape, the caps on one pass, and the
few repairs several ailments use (ending edges, closing review items and
jobs)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..base import rows_in, run_name
from ..graph import (
    end_edges,
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
    # a repair that takes the store's lock for each row itself (through a
    # store function that is serialized) runs outside the heal's one lock,
    # so a long one (2,000 documents re-indexed) does not hold every other
    # writer for its whole length
    locks_itself: bool = False

    @property
    def repairable(self) -> bool:
        return self.repair is not None


def _live_edge_counts(con: sqlite3.Connection, ids: list[int]) -> dict[int, int]:
    """How many live edges each of these entities carries. Two indexed
    queries per batch: a correlated count with `src = ? OR dst = ?` cannot
    use either edge index and turns a check into a table scan per entity."""
    counts: dict[int, int] = {}
    for side in ("src", "dst"):
        for entity_id, n in rows_in(
            con,
            f"SELECT {side}, count(*) FROM edges"
            f" WHERE valid_to IS NULL AND {side} IN ({{ids}})"
            f" GROUP BY {side}",
            ids,
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
    """End every live edge these entities carry, under a run of their own
    (``heal:entities-…``) that ``restore_run`` undoes. The entities
    themselves stay: an entity with no live edges is invisible, and its
    name is the record of what the extraction did."""
    return len(
        end_edges(
            con,
            _edges_of_entities(con, [r["id"] for r in rows]),
            run=run_name(ENTITIES_PRODUCER, sep=":"),
        )
    )


ENTITIES_PRODUCER = "heal:entities"
EDGES_PRODUCER = "heal:edges"


def _repair_edges(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """End these edges under a run of their own (``heal:edges-…``) that
    ``restore_run`` undoes."""
    return len(
        end_edges(con, [r["id"] for r in rows], run=run_name(EDGES_PRODUCER, sep=":"))
    )


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
