"""A retirement taken back (structure review finding 2): a retirement ends
its document's edges under a run of its own, and the undo states them
again as new edges with what held the old ids moved onto them, as every
repair's undo does (``restore_run``). The heal's edge repairs end under a
run too."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from prax import store
from prax.store.repair.common import _repair_edges, _repair_entities


def _doc(con: sqlite3.Connection, text: str, title: str) -> int:
    doc = int(
        store.register(con, text.encode(), mime="text/plain", title=title)["doc_id"]
    )
    store.index_text(con, doc, text)
    return doc


def _live(con: sqlite3.Connection, doc: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM edges WHERE source_doc = ? AND valid_to IS NULL ORDER BY id",
        (doc,),
    ).fetchall()


def _rows(con: sqlite3.Connection, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in con.execute(sql, args)]


def test_an_unretired_document_has_its_facts_again(con: sqlite3.Connection) -> None:
    doc = _doc(con, "A paper on wavelets, by Ada. " * 20, "Wavelets")
    old = store.link(
        con,
        store.Edge("Wavelets", "paper", "authored_by", "Ada", "person"),
        source_doc=doc,
        evidence="by Ada",
        producer="test",
        run="read-1",
    )
    got = store.retire_document(con, doc, reason="saved by mistake")
    assert got["edges"] == 1 and not _live(con, doc)
    run = json.loads(
        con.execute("SELECT meta FROM documents WHERE id = ?", (doc,)).fetchone()[0]
    )["retired"]["run"]
    assert run.startswith("retire-")
    assert _rows(con, "SELECT edge_id FROM edge_endings WHERE run = ?", run) == [(old,)]
    back = store.unretire_document(con, doc)
    assert back["edges"] == 1 and back["chunks"] > 0
    (new,) = _live(con, doc)
    # a new edge with the old one's fact and provenance; the old one stays
    # ended, so what prax held while it was retired is history (invariant 8)
    assert new["id"] != old
    assert (new["rel"], new["evidence"], new["producer"], new["run"]) == (
        "authored_by",
        "by Ada",
        "test",
        "read-1",
    )
    assert _rows(con, "SELECT valid_to FROM edges WHERE id = ?", old)[0][0]
    # retired and back again: the second retirement's own run
    store.retire_document(con, doc, reason="again")
    assert store.unretire_document(con, doc)["edges"] == 1
    assert len(_live(con, doc)) == 1


def test_what_held_the_old_edges_holds_the_new_ones(con: sqlite3.Connection) -> None:
    doc = _doc(con, "Ada wrote on wavelets in Paris. " * 20, "Notes")
    other = _doc(con, "Another document altogether. " * 20, "Other")
    premise = store.link(
        con,
        store.Edge("Notes", "document", "authored_by", "Ada", "person"),
        source_doc=doc,
    )
    elsewhere = store.link(
        con,
        store.Edge("Ada", "person", "affiliated_with", "Sorbonne", "organization"),
        source_doc=other,
    )
    # a derivation of another document that stands on this one's fact, and
    # one of this document's own that stands on another's
    leaning = store.link(
        con,
        store.Edge("Other", "document", "links_to", "Notes", "document"),
        source_doc=other,
        confidence="INFERRED",
        producer="rule:test",
    )
    derived = store.link(
        con,
        store.Edge("Notes", "document", "links_to", "Other", "document"),
        source_doc=doc,
        confidence="INFERRED",
        producer="rule:test",
    )
    con.executemany(
        "INSERT INTO edge_premises (edge_id, premise_id) VALUES (?, ?)",
        [(leaning, premise), (derived, elsewhere)],
    )
    con.execute(
        "INSERT INTO edge_conflicts (edge_a, edge_b, rel, producer, run, found_at)"
        " VALUES (?, ?, 'authored_by', 'rule:functional', 'c', 'now')",
        (premise, elsewhere),
    )
    con.execute(
        "INSERT INTO import_held (source, run, src_gid, dst_gid, record, edge_id)"
        " VALUES ('lab', 'import-1', 'e:a', 'e:b', '{}', ?)",
        (premise,),
    )
    con.commit()
    store.retire_document(con, doc, reason="mistake")
    store.unretire_document(con, doc)
    new = {r["rel"]: r["id"] for r in _live(con, doc)}
    assert premise not in new.values()
    premises = "SELECT premise_id FROM edge_premises WHERE edge_id = ?"
    # the live derivation of the other document stands on the new fact
    assert _rows(con, premises, leaning) == [(new["authored_by"],)]
    # this document's derivation, stated again, keeps its premise
    assert _rows(con, premises, new["links_to"]) == [(elsewhere,)]
    # and the ended derivation keeps its own, as history
    assert _rows(con, premises, derived) == [(elsewhere,)]
    assert _rows(con, "SELECT edge_a FROM edge_conflicts") == [(new["authored_by"],)]
    assert _rows(con, "SELECT edge_id FROM import_held") == [(new["authored_by"],)]


def test_a_retirement_from_before_its_run_brings_no_edges(
    con: sqlite3.Connection,
) -> None:
    doc = _doc(con, "Old notes on filters. " * 20, "Filters")
    store.link(
        con,
        store.Edge("Filters", "document", "authored_by", "Bob", "person"),
        source_doc=doc,
    )
    store.retire_document(con, doc, reason="old")
    meta = json.loads(
        con.execute("SELECT meta FROM documents WHERE id = ?", (doc,)).fetchone()[0]
    )
    del meta["retired"]["run"]
    con.execute("UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc))
    con.commit()
    assert store.unretire_document(con, doc)["edges"] == 0
    assert not _live(con, doc)


def test_the_heals_edge_repairs_can_be_undone(con: sqlite3.Connection) -> None:
    doc = _doc(con, "Notes on a placeholder. " * 20, "Placeholder")
    one = store.link(
        con,
        store.Edge("Placeholder", "document", "authored_by", "N.N.", "person"),
        source_doc=doc,
    )
    two = store.link(
        con,
        store.Edge("Placeholder", "document", "mentions", "Unknown", "concept"),
        source_doc=doc,
    )
    assert _repair_edges(con, [{"id": one}]) == 1
    entity = _rows(con, "SELECT dst FROM edges WHERE id = ?", two)[0][0]
    assert _repair_entities(con, [{"id": entity}]) == 1
    runs = dict(_rows(con, "SELECT edge_id, run FROM edge_endings"))
    assert runs[one].startswith("heal:edges:")
    assert runs[two].startswith("heal:entities:")
    assert store.restore_run(con, runs[one]) == {"retired": 0, "restated": 1}
    assert store.restore_run(con, runs[two]) == {"retired": 0, "restated": 1}
    assert len(_live(con, doc)) == 2
