"""An edge whose two ends are one thing (AV, 2026-11-17: "digital
waveguide extends digital waveguide" in the BWS survey): refused when it
is written, hidden from a walk when a merge made one of an older edge."""

from __future__ import annotations

import sqlite3

import pytest

from prax import store
from prax.graph import extraction


def _entity(con: sqlite3.Connection, name: str) -> int:
    return int(
        con.execute("SELECT id FROM entities WHERE name = ?", (name,)).fetchone()[0]
    )


def test_link_refuses_an_edge_from_a_thing_to_itself(con: sqlite3.Connection) -> None:
    doc = store.register(con, b"waveguides", mime="text/plain", title="waveguides")
    edge = store.Edge(
        "digital waveguide", "method", "extends", "digital waveguide", "method"
    )
    before = con.execute("SELECT count(*) FROM entities").fetchone()[0]
    with pytest.raises(store.SelfLoop):
        store.link(con, edge, source_doc=doc["doc_id"])
    # refused before an entity was made for the name
    assert con.execute("SELECT count(*) FROM entities").fetchone()[0] == before
    # two names a merge made one are one thing too
    store.link(
        con,
        store.Edge("FDN", "method", "uses", "delay line", "method"),
        source_doc=doc["doc_id"],
    )
    store.link(
        con,
        store.Edge("feedback delay network", "method", "uses", "delay line", "method"),
        source_doc=doc["doc_id"],
    )
    store.merge_entities(
        con, _entity(con, "FDN"), _entity(con, "feedback delay network")
    )
    with pytest.raises(store.SelfLoop):
        store.link(
            con,
            store.Edge("FDN", "method", "extends", "feedback delay network", "method"),
            source_doc=doc["doc_id"],
        )
    # a refusal is a ValueError, which the door answers 400
    assert issubclass(store.SelfLoop, ValueError)


def test_a_walk_hides_an_edge_a_merge_turned_into_a_loop(
    con: sqlite3.Connection,
) -> None:
    doc = store.register(con, b"reverb", mime="text/plain", title="reverb")
    for edge in (
        store.Edge("reverb", "method", "extends", "reverberation", "method"),
        store.Edge("reverb", "method", "uses", "comb filter", "method"),
    ):
        store.link(con, edge, source_doc=doc["doc_id"])
    store.merge_entities(con, _entity(con, "reverb"), _entity(con, "reverberation"))
    walked = store.traverse(con, "reverberation", hops=1)
    assert [(e["src"], e["rel"], e["dst"]) for e in walked] == [
        ("reverberation", "uses", "comb filter")
    ]
    # kept, not ended: an unmerge makes it a fact again
    assert (
        con.execute(
            "SELECT count(*) FROM edges WHERE rel = 'extends' AND valid_to IS NULL"
        ).fetchone()[0]
        == 1
    )


def test_extraction_drops_a_loop_and_keeps_the_rest(con: sqlite3.Connection) -> None:
    doc = store.register(con, b"grains", mime="text/plain", title="grains")
    store.index_text(
        con, doc["doc_id"], "Grainlet synthesis extends granular synthesis."
    )
    triples = [
        extraction.Triple(
            "granular synthesis",
            "method",
            "extends",
            "granular synthesis",
            "method",
            "EXTRACTED",
            "granular synthesis",
        ),
        extraction.Triple(
            "grainlet synthesis",
            "method",
            "extends",
            "granular synthesis",
            "method",
            "EXTRACTED",
            "Grainlet synthesis extends granular synthesis.",
        ),
    ]
    report = extraction.apply(
        con, doc["doc_id"], extraction.Extraction(triples=triples), extractor="test"
    )
    assert report.linked == 1
    assert report.drops["an edge to itself"]["count"] == 1


def test_a_walk_says_when_the_name_it_walked_is_not_the_one_asked(
    con: sqlite3.Connection,
) -> None:
    doc = store.register(con, b"models", mime="text/plain", title="models")
    store.link(
        con,
        store.Edge("physical modeling", "method", "uses", "waveguide", "method"),
        source_doc=doc["doc_id"],
    )
    store.link(
        con,
        store.Edge("physical modelling", "method", "uses", "mass spring", "method"),
        source_doc=doc["doc_id"],
    )
    store.merge_entities(
        con, _entity(con, "physical modelling"), _entity(con, "physical modeling")
    )
    got = store.traverse_map(con, "physical modelling")
    assert got["entity"] == "physical modeling"
    assert got["resolved_from"] == "physical modelling"
    # asked by the name it walks, nothing to say
    assert "resolved_from" not in store.traverse_map(con, "physical modeling")
