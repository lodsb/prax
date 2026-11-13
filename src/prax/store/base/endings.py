"""An edge's ending and its undo (invariant 8): ``_end_edges`` records
what a run ended in ``edge_endings``, ``_restore_run`` states it again as
new edges, and ``_move_references`` moves what held an ended edge's id
onto its new one. ``EDGE_FACT`` and ``_insert_edge`` are the one writer
of an edge row."""

from __future__ import annotations

import sqlite3
from typing import Any

from prax.errors import NotFound  # the store's miss, store.NotFound

from .common import _NOW, rows_in

# ------------------------------------------------------ an edge's ending
# How an edge is ended with a record of it, and how a run's endings are
# undone. Here, below the documents and the graph, because both end edges
# this way: a repair or a revision of the graph's, and a document's
# retirement (``documents.library``), whose undo states its edges again.


def _end_edges(
    con: sqlite3.Connection,
    edge_ids: list[int],
    *,
    run: str | None,
    corrected_by: int | None = None,
) -> list[int]:
    """End these edges where they are still live and, with ``run``, record
    each ending (``edge_endings``, with the edge that corrects it where
    there is one), so ``restore_run`` undoes the run whole. The one place
    an edge is ended by id; does not commit. Returns the ids it ended."""
    live = [
        int(r[0])
        for r in rows_in(
            con,
            "SELECT id FROM edges WHERE id IN ({ids}) AND valid_to IS NULL",
            edge_ids,
        )
    ]
    for i in range(0, len(live), END_BATCH):
        part = live[i : i + END_BATCH]
        marks = ",".join("?" * len(part))
        con.execute(f"UPDATE edges SET valid_to = {_NOW} WHERE id IN ({marks})", part)
    if run is not None:
        con.executemany(
            "INSERT OR IGNORE INTO edge_endings (edge_id, run, ended_at, corrected_by)"
            f" VALUES (?, ?, {_NOW}, ?)",
            [(e, run, corrected_by) for e in live],
        )
    return live


# How many edges one commit of ``end_edges`` ends: a commit is an fsync,
# and one per edge held the write lock 11.4 s for 1,711 duplicates (the
# quality review of 2026-10-18); a batch keeps the lock brief either way.
END_BATCH = 500


# an edge's fact as written: every column an edge is stated with and a
# restatement copies from the ended one. The two writers of an edge row
# (``_link``, ``_restore_run``) listed the columns each by hand, and a
# column one forgot was dropped by every undo without an error (the
# quality review of 2026-11-11, finding 3); ``tests/test_schema.py`` holds
# this list to the table's columns
EDGE_FACT = (
    "src",
    "dst",
    "rel",
    "confidence",
    "weight",
    "source_doc",
    "ontology_version",
    "evidence",
    "producer",
    "run",
    "world_from",
    "world_from_precision",
    "world_to",
    "world_to_precision",
    "evidence_start",
    "evidence_end",
    "evidence_text_hash",
)
# the columns that are not the fact: its id, and when prax held it
EDGE_TIMES = ("id", "valid_from", "valid_to", "ingested_at")


def _insert_edge(con: sqlite3.Connection, fact: dict[str, Any]) -> int:
    """Write one edge row, valid from now: ``fact`` names each of
    ``EDGE_FACT``, a missing one None. Returns its id. Does not commit."""
    unknown = set(fact) - set(EDGE_FACT)
    if unknown:
        raise ValueError(f"not an edge's column: {sorted(unknown)}")
    cur = con.execute(
        f"INSERT INTO edges ({', '.join(EDGE_FACT)}, valid_from, ingested_at)"
        f" VALUES ({', '.join('?' * len(EDGE_FACT))}, {_NOW}, {_NOW})",
        tuple(fact.get(c) for c in EDGE_FACT),
    )
    return int(cur.lastrowid or 0)


def _restore_run(
    con: sqlite3.Connection, run: str, *, source_doc: int | None = None
) -> dict[str, int]:
    """Undo a repair run: its own live edges are ended (``retire_run``),
    and every edge it ended (``edge_endings``) is stated again as a new
    edge with the old one's fact, document, evidence, producer, run and
    world dates. The ended edge stays ended: when prax held what is
    history. A fact that is live again by other means is not doubled.
    What still holds an ended edge's id moves to the edge that states it
    again (``_move_references``). Does not commit.

    With ``source_doc`` only that document's part of the run is undone:
    the edges it ended of that document, and none of the run's own. A
    clean-up retires a set under one run, and taking one document of it
    back stated the facts of the whole set while the rest stayed retired
    (the safety review of 2026-11-11).

    Without ``source_doc``, a run that retired documents (a retirement's,
    a clean-up's: ``meta.retired.run``) is refused (ValueError): its undo
    is ``unretire_document`` or ``restore_set``, which bring the documents
    back with their facts, where this stated the facts again and left the
    documents retired. A run that wrote nothing and ended nothing is
    ``NotFound``, as ``restore_set`` says it (the quality review of
    2026-11-11, finding 39)."""
    if source_doc is None:
        retiring = con.execute(
            "SELECT id FROM documents WHERE json_extract(meta, '$.retired.run') = ?"
            " LIMIT 1",
            (run,),
        ).fetchone()
        if retiring is not None:
            raise ValueError(
                f"run {run} retired documents (document {retiring[0]} among"
                " them): take them back with unretire (POST /doc/{id}/unretire)"
                " or, for a clean-up, its restore (POST /cleanup-restore)"
            )
        if (
            con.execute("SELECT 1 FROM edges WHERE run = ? LIMIT 1", (run,)).fetchone()
            is None
            and con.execute(
                "SELECT 1 FROM edge_endings WHERE run = ? LIMIT 1", (run,)
            ).fetchone()
            is None
        ):
            raise NotFound(f"no run named {run} wrote or ended a fact")
    # not what an undo of the run stated again: a run that ends its own
    # edges (a sync's links:) restates them under its own name, and a second
    # undo ended them for good (the safety review of 2026-11-11)
    retired = (
        con.execute(
            f"UPDATE edges SET valid_to = {_NOW} WHERE run = ? AND valid_to IS NULL"
            " AND id NOT IN (SELECT restated_as FROM edge_endings"
            " WHERE run = ? AND restated_as IS NOT NULL)",
            (run, run),
        ).rowcount
        if source_doc is None
        else 0
    )
    rows = con.execute(
        """
        SELECT x.edge_id, e.*, s.name AS s_name, s.type AS s_type,
               t.name AS t_name, t.type AS t_type
        FROM edge_endings x JOIN edges e ON e.id = x.edge_id
        JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst
        WHERE x.run = ? AND x.restated_as IS NULL
          AND (? IS NULL OR e.source_doc = ?)
        """,
        (run, source_doc, source_doc),
    ).fetchall()
    restated = 0
    moved: dict[int, int] = {}
    for r in rows:
        live = con.execute(
            "SELECT id FROM edges WHERE src = ? AND rel = ? AND dst = ?"
            " AND source_doc IS ? AND valid_to IS NULL",
            (r["src"], r["rel"], r["dst"], r["source_doc"]),
        ).fetchone()
        if live is None:
            # the old row's fact whole: its document, evidence and where
            # the quote stood, producer, run and world dates
            new_id = _insert_edge(con, {c: r[c] for c in EDGE_FACT})
            restated += 1
        else:
            new_id = int(live["id"])
        con.execute(
            "UPDATE edge_endings SET restated_as = ? WHERE edge_id = ? AND run = ?",
            (new_id, r["edge_id"], run),
        )
        moved[int(r["edge_id"])] = new_id
    _move_references(con, moved)
    return {"retired": retired, "restated": restated}


def _move_references(con: sqlite3.Connection, moved: dict[int, int]) -> int:
    """Move what still holds an ended edge's id to the edge that states it
    again (``moved``: old id -> new). The live holders move: a live derived
    edge's premise, an open conflict, a face's live answer, an import's
    held edge. A restated derived edge gets its old one's premises. History
    stays as it was: an ended derivation, a closed conflict, a taken-back
    answer and ``edge_endings`` still name the edge they named. Returns
    the rows moved; does not commit."""
    n = 0
    for old, new in moved.items():
        if old == new:
            continue
        n += con.execute(
            "INSERT OR IGNORE INTO edge_premises (edge_id, premise_id)"
            " SELECT ?, premise_id FROM edge_premises WHERE edge_id = ?",
            (new, old),
        ).rowcount
    for old, new in moved.items():
        if old == new:
            continue
        n += con.execute(
            "UPDATE OR IGNORE edge_premises SET premise_id = ? WHERE premise_id = ?"
            " AND edge_id IN (SELECT id FROM edges WHERE valid_to IS NULL)",
            (new, old),
        ).rowcount
        for side in ("edge_a", "edge_b"):
            n += con.execute(
                f"UPDATE OR IGNORE edge_conflicts SET {side} = ?"
                f" WHERE {side} = ? AND ended_at IS NULL",
                (new, old),
            ).rowcount
        n += con.execute(
            "UPDATE face_names SET edge_id = ? WHERE edge_id = ? AND ended_at IS NULL",
            (new, old),
        ).rowcount
        n += con.execute(
            "UPDATE import_held SET edge_id = ? WHERE edge_id = ?", (new, old)
        ).rowcount
    return n


# how long a connection waits for another process's write transaction
# before "database is locked" (three writers share the file: the door,
# the watcher, a backlog pass)
