"""The fixes of the review of 2026-10-04: each test fails on the code the
review read, and holds the behaviour the finding asked for."""

from __future__ import annotations

import sqlite3

from prax import store
from prax.graph import resolution
from prax.store import repair

E = store.Edge


def _live(con: sqlite3.Connection, edge_id: int) -> bool:
    row = con.execute("SELECT valid_to FROM edges WHERE id = ?", (edge_id,)).fetchone()
    return row is not None and row[0] is None


# ----------------------------------------------------------- data safety


def test_a_derivation_a_merge_doubled_ends_with_its_premise(
    con: sqlite3.Connection,
) -> None:
    """Finding 4: two derived edges folded onto one pair by a merge; the
    one the pass did not track used to outlive its premises."""
    org = "organization"
    lab = store.link(con, E("Lab", org, "part_of", "Dept", org), producer="t")
    store.link(con, E("Dept", org, "part_of", "Faculty", org), producer="t")
    store.link(con, E("Dept", org, "part_of", "Fac", org), producer="t")
    store.derive_rules(con)  # Lab->Faculty and Lab->Fac
    fac, faculty = (
        int(con.execute("SELECT id FROM entities WHERE name = ?", (n,)).fetchone()[0])
        for n in ("Fac", "Faculty")
    )
    store.merge_entities(con, fac, faculty)
    store.derive_rules(con)
    store.invalidate_edge(con, lab)
    store.derive_rules(con)
    derived = con.execute(
        "SELECT count(*) FROM edges WHERE producer LIKE 'rule:%' AND rel = 'part_of'"
        " AND valid_to IS NULL"
    ).fetchone()[0]
    assert derived == 0


def test_a_heal_run_is_undone_whole(con: sqlite3.Connection) -> None:
    """Finding 5: retiring a heal run ended its replacements but not what
    it had ended; restore_run states those again."""
    doc = store.ingest_text(con, "a monograph on counterpoint " * 20)["doc_id"]
    old = store.link(
        con,
        E("A monograph", "paper", "published_in", "Sommersemester 2009", "venue"),
        source_doc=doc,
        evidence="on the title page",
        producer="extractor",
        run="x1",
    )
    repair.heal(con, only=["not-venues"])
    assert not _live(con, old)
    out = store.restore_run(con, repair.NOT_VENUE_PRODUCER)
    assert out["restated"] == 1
    row = con.execute(
        "SELECT producer, run, evidence, source_doc FROM edges"
        " WHERE rel = 'published_in' AND valid_to IS NULL"
    ).fetchone()
    assert tuple(row) == ("extractor", "x1", "on the title page", doc)
    assert not _live(con, old)  # the ended edge stays ended: history
    assert store.restore_run(con, repair.NOT_VENUE_PRODUCER)["restated"] == 0


def test_undoing_a_resolve_round_ends_its_edition_links(
    con: sqlite3.Connection,
) -> None:
    """Finding 16: unmerge_run took the merges back and left the venue
    tier's part_of edges of the same run."""
    for paper, venue in (("P1", "NIME"), ("P2", "NIME 2010"), ("P3", "NIME2010")):
        store.link(con, E(paper, "paper", "published_in", venue, "venue"), producer="t")
    resolution.apply(con, resolution.plan(con, etype="venue", likely=False), run="r1")
    links = "SELECT count(*) FROM edges WHERE run = 'r1' AND valid_to IS NULL"
    assert con.execute(links).fetchone()[0] == 1
    store.unmerge_run(con, "r1")
    assert con.execute(links).fetchone()[0] == 0
