"""What changed in a period, on the two times an edge carries (AL step 5):
what prax wrote and ended, and what began and ended in the world."""

from __future__ import annotations

import sqlite3

import pytest

from prax import store

E = store.Edge


def _set_record(con: sqlite3.Connection, edge_id: int, written: str) -> None:
    """Move an edge's record time into the past, as if written then: the
    append-only triggers guard the fact, not when prax wrote it."""
    con.execute("UPDATE edges SET valid_from = ? WHERE id = ?", (written, edge_id))
    con.commit()


def _day(con: sqlite3.Connection, edge_id: int) -> str:
    """The day an edge was written: a period taken from a later now()
    crosses midnight now and then (the review of 2026-10-04)."""
    return str(
        con.execute("SELECT valid_from FROM edges WHERE id = ?", (edge_id,)).fetchone()[
            0
        ]
    )[:10]


def test_record_time_says_what_was_written_and_ended(con: sqlite3.Connection) -> None:
    old = store.link(con, E("Lab", "organization", "part_of", "Uni", "organization"))
    new = store.link(con, E("Group", "organization", "part_of", "Uni", "organization"))
    _set_record(con, old, "2026-08-15T10:00:00Z")
    store.invalidate_edge(con, old)  # ended now
    got = store.changes(con, "2026-08", "2026-08")
    assert got["time"] == "record"
    assert [f["src"] for f in got["added"]["facts"]] == ["Lab"]
    assert got["ended"]["count"] == 0  # it ended later
    today = store.changes(con, _day(con, new))
    assert {f["edge_id"] for f in today["added"]["facts"]} == {new}
    assert {f["edge_id"] for f in today["ended"]["facts"]} == {old}
    assert today["added"]["by_rel"] == {"part_of": 1}


def test_world_time_meets_a_period_at_its_precision(con: sqlite3.Connection) -> None:
    store.link(
        con,
        E("Plan A", "document", "supersedes", "Plan 0", "document"),
        world_from="2026",
    )
    store.link(
        con,
        E("Plan B", "document", "supersedes", "Plan A", "document"),
        world_from="2026-09-14",
    )
    store.link(
        con,
        E("Plan C", "document", "supersedes", "Plan B", "document"),
        world_from="2025-03",
    )
    got = store.changes(con, "2026-09", "2026-09", world=True)
    # "2026" meets September 2026; March 2025 does not
    assert {f["src"] for f in got["began"]["facts"]} == {"Plan A", "Plan B"}
    assert got["began"]["facts"][0]["at"] == "2026-09-14"  # newest first


def test_an_entity_by_a_label_and_the_rules_left_out(con: sqlite3.Connection) -> None:
    for a, b in (("Lab", "Dept"), ("Dept", "Uni"), ("Shop", "Mall")):
        store.link(con, E(a, "organization", "part_of", b, "organization"))
    store.derive_rules(con)
    plain = store.changes(con, "2000", entity="Dept")
    assert plain["added"]["count"] == 2  # Lab->Dept, Dept->Uni; no Lab->Uni
    derived = store.changes(con, "2000", entity="Lab", derived=True)
    assert {f["dst"] for f in derived["added"]["facts"]} == {"Dept", "Uni"}
    assert store.changes(con, "2000", entity="Nowhere") == {
        "since": "2000",
        "until": None,
        "unknown_entity": "Nowhere",
    }


def test_the_list_is_capped_and_says_what_it_left_out(con: sqlite3.Connection) -> None:
    for i in range(7):
        store.link(
            con, E(f"Unit {i}", "organization", "part_of", "Uni", "organization")
        )
    got = store.changes(con, "2000", limit=3)
    assert len(got["added"]["facts"]) == 3 and got["added"]["left_out"] == 4


def test_a_period_that_does_not_parse_is_refused(con: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        store.changes(con, "last tuesday")
    with pytest.raises(ValueError, match="before"):
        store.changes(con, "2026-09", "2026-08")


def test_a_filter_binds_beside_the_world_period(con: sqlite3.Connection) -> None:
    store.link(
        con,
        E("Plan B", "document", "supersedes", "Plan A", "document"),
        world_from="2026-09",
    )
    store.link(
        con,
        E("Plan B", "document", "links_to", "Plan A", "document"),
        world_from="2026-09",
    )
    got = store.changes(con, "2026", world=True, rel="links_to", entity="Plan A")
    assert got["began"]["by_rel"] == {"links_to": 1}


def test_a_rereading_is_not_news(con: sqlite3.Connection) -> None:
    """The same fact ended and written again (a re-extraction) is neither
    added nor ended; a fact still held elsewhere is not ended."""
    first = store.link(con, E("Lab", "organization", "part_of", "Uni", "organization"))
    _set_record(con, first, "2026-08-01T00:00:00Z")
    store.invalidate_edge(con, first)
    again = store.link(con, E("Lab", "organization", "part_of", "Uni", "organization"))
    today = store.changes(con, _day(con, again))
    assert (today["added"]["count"], today["ended"]["count"]) == (0, 0)
    raw = store.changes(con, _day(con, again), rereadings=True)
    assert (raw["added"]["count"], raw["ended"]["count"]) == (1, 1)
