"""Two facts that cannot both hold (stage AN): a functional relation with
two values for one subject, kept beside the edges, shown and never
resolved by ending one."""

from __future__ import annotations

import sqlite3

from prax import store

E = store.Edge


def _published(
    con: sqlite3.Connection, paper: str, venue: str, doc: int | None = None
) -> int:
    return store.link(
        con,
        E(paper, "paper", "published_in", venue, "venue"),
        source_doc=doc,
        evidence=f"appeared in {venue}",
        producer="t",
    )


def test_two_venues_are_a_conflict_a_series_and_its_edition_are_not(
    con: sqlite3.Connection,
) -> None:
    a = _published(con, "Paper A", "DAFx-14")
    b = _published(con, "Paper A", "ICASSP 2014")
    _published(con, "Paper B", "NIME")
    _published(con, "Paper B", "NIME 2010")
    store.link(con, E("NIME 2010", "venue", "part_of", "NIME", "venue"), producer="t")
    report = store.find_conflicts(con)["published_in"]
    assert (report["open"], report["added"]) == (1, 1)
    assert [c["edge_id"] for c in store.edge_conflicts(con, a)] == [b]
    assert [c["edge_id"] for c in store.edge_conflicts(con, b)] == [a]
    again = store.find_conflicts(con)["published_in"]
    assert (again["added"], again["ended"]) == (0, 0)
    # no fact was ended: both still stand
    assert (
        con.execute(
            "SELECT count(*) FROM edges WHERE id IN (?, ?) AND valid_to IS NULL", (a, b)
        ).fetchone()[0]
        == 2
    )


def test_a_conflict_ends_when_its_fact_does(con: sqlite3.Connection) -> None:
    a = _published(con, "Paper A", "DAFx-14")
    _published(con, "Paper A", "ICASSP 2014")
    store.find_conflicts(con)
    store.invalidate_edge(con, a)
    out = store.find_conflicts(con)["published_in"]
    assert (out["open"], out["ended"]) == (0, 1)
    assert store.edge_conflicts(con, a) == []
    # kept as history
    assert con.execute("SELECT count(*) FROM edge_conflicts").fetchone()[0] == 1


def test_the_walk_marks_a_disputed_fact(con: sqlite3.Connection) -> None:
    _published(con, "Paper A", "DAFx-14")
    _published(con, "Paper A", "ICASSP 2014")
    store.find_conflicts(con)
    walk = store.traverse_map(con, "Paper A")
    assert sorted(e.get("disputed") for e in walk["edges"]) == [1, 1]


def test_a_hidden_documents_fact_is_no_disagreement_for_the_viewer(
    con: sqlite3.Connection,
) -> None:
    private = store.ingest_text(con, "my own notes on where it appeared " * 10)[
        "doc_id"
    ]
    store.set_sensitivity(con, private, "personal")
    a = _published(con, "Paper A", "DAFx-14")
    _published(con, "Paper A", "ICASSP 2014", private)
    store.find_conflicts(con)
    token = store.VIEWER.set(store.Viewer(name="t", domains=None, personal=False))
    try:
        assert store.edge_conflicts(con, a) == []
        walk = store.traverse_map(con, "Paper A")
        assert all("disputed" not in e for e in walk["edges"])
    finally:
        store.VIEWER.reset(token)
    assert len(store.edge_conflicts(con, a)) == 1
