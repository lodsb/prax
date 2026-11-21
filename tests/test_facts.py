"""An edge is one reader's assertion, a fact is what edges state (AV,
2026-11-21; ``docs/one-edge-per-fact.md``): a writer asks whether its own
source states a fact already, under any name of its things, and a walk
lists a fact once with its sources."""

from __future__ import annotations

import sqlite3

from prax import store
from prax.graph import extraction


def _doc(con: sqlite3.Connection, title: str) -> int:
    text = f"# {title}\n\n" + "The vocoder splits the signal into bands. " * 8
    return int(store.ingest_text(con, text, title=title)["doc_id"])


def _says(con: sqlite3.Connection, doc_id: int, extractor: str = "stub") -> None:
    triple = extraction.Triple(
        "Vocoders",
        "paper",
        "about",
        "filter bank",
        "concept",
        "EXTRACTED",
        "splits the signal into bands",
    )
    got = extraction.Extraction(triples=[triple, triple])  # said twice
    extraction.apply(con, doc_id, got, extractor=extractor)


def test_a_second_document_stating_a_fact_is_its_own_evidence(
    con: sqlite3.Connection,
) -> None:
    """Before, an extraction wrote no edge for a fact any document already
    stated under the same names: the second document's evidence was lost,
    and the fact went with the first document if it was retired."""
    first, second = _doc(con, "One"), _doc(con, "Two")
    _says(con, first)
    _says(con, second)
    _says(con, second, extractor="other-model")  # another reader of it
    edge = store.Edge("Vocoders", "paper", "about", "filter bank", "concept")
    assert len(store.find_edges(con, edge)) == 3
    assert len(store.find_edges(con, edge, source_doc=second)) == 2
    assert len(store.find_edges(con, edge, source_doc=first, producer="stub")) == 1
    # the first document retired, the fact stands on the second
    store.retire_document(con, first, reason="test")
    assert len(store.find_edges(con, edge)) == 2


def test_a_fact_is_found_under_any_name_of_its_thing(
    con: sqlite3.Connection,
) -> None:
    """A merge since the edge was written does not hide it: asked under the
    survivor's name or the folded one, the fact is held, so a pass that
    asks again writes it no second time."""
    doc_id = _doc(con, "Oscillators")
    store.link(
        con,
        store.Edge("Oscillators", "paper", "about", "LFO", "concept"),
        source_doc=doc_id,
        evidence="the LFO of a synthesizer",
        producer="test",
    )
    store.link(
        con,
        store.Edge("Other", "paper", "about", "low frequency oscillator", "concept"),
        source_doc=doc_id,
        producer="test",
    )
    ids = {
        str(r[0]): int(r[1])
        for r in con.execute("SELECT name, id FROM entities WHERE type = 'concept'")
    }
    store.merge_entities(con, ids["LFO"], ids["low frequency oscillator"])
    for name in ("LFO", "low frequency oscillator"):
        edge = store.Edge("Oscillators", "paper", "about", name, "concept")
        assert len(store.find_edges(con, edge, source_doc=doc_id)) == 1, name
    unknown = store.Edge("Oscillators", "paper", "about", "nothing", "concept")
    assert store.find_edges(con, unknown) == []


def test_a_walk_lists_a_fact_once_with_its_sources(
    con: sqlite3.Connection,
) -> None:
    """Two documents and two readers state one fact: one row, shown as its
    surest edge, with ``sources``, ``support`` and ``also``; another fact
    stays a row of its own."""
    first, second = _doc(con, "One"), _doc(con, "Two")
    _says(con, first)
    _says(con, second)
    store.link(
        con,
        store.Edge("Vocoders", "paper", "about", "filter bank", "concept"),
        confidence="INFERRED",
        source_doc=second,
        evidence="a guess",
        producer="typing-rules",
    )
    store.link(
        con,
        store.Edge("Vocoders", "paper", "about", "phase", "concept"),
        source_doc=first,
        evidence="phase",
        producer="test",
    )
    got = store.traverse_map(con, "Vocoders", 1)
    rows = [r for r in got["edges"] if r["dst"] == "filter bank"]
    assert len(rows) == 1 and len(got["edges"]) == 2
    (fact,) = rows
    assert (fact["sources"], fact["support"], len(fact["also"])) == (3, 2, 2)
    assert fact["confidence"] == "EXTRACTED" and fact["source_doc"] == first
    assert {a["producer"] for a in fact["also"]} == {"stub", "typing-rules"}
    assert not {"_edges", "_docs", "src_id", "dst_id"} & set(fact)
    # capped: one fact a relation, and left_out counts facts, not edges
    capped = store.traverse_map(con, "Vocoders", 1, limit=1)
    assert len(capped["edges"]) == 1 and capped["left_out"]["edges"] == 1


def test_two_versions_of_one_work_are_one_witness(con: sqlite3.Connection) -> None:
    """A preprint and its journal version both state a fact: two sources,
    one witness, so no ``support``. A third document that is no version of
    the work makes it two."""
    preprint, journal, other = _doc(con, "One"), _doc(con, "Two"), _doc(con, "Three")
    for doc_id in (preprint, journal):
        _says(con, doc_id)
    store.state_versions(
        con, [preprint, journal], by="test", run="versions:test", evidence="a test"
    )
    walk = store.traverse_map(con, "Vocoders", 1)
    (fact,) = [r for r in walk["edges"] if r["dst"] == "filter bank"]
    assert fact["sources"] == 2 and "support" not in fact
    _says(con, other)
    walk = store.traverse_map(con, "Vocoders", 1)
    (fact,) = [r for r in walk["edges"] if r["dst"] == "filter bank"]
    assert (fact["sources"], fact["support"]) == (3, 2)


def test_a_persons_page_lists_a_fact_once(con: sqlite3.Connection) -> None:
    """Two documents saying who wrote a paper: one fact on the author's
    page and in the list of people, two documents behind it."""
    first, second = _doc(con, "One"), _doc(con, "Two")
    for doc_id in (first, second):
        store.link(
            con,
            store.Edge("A Paper", "paper", "authored_by", "Ada Example", "author"),
            source_doc=doc_id,
            producer="test",
        )
    (ada,) = [
        int(r[0])
        for r in con.execute("SELECT id FROM entities WHERE name = 'Ada Example'")
    ]
    page = store.person_page(con, ada)
    assert page["facts"] == 1 and page["documents_total"] == 2
    (rel,) = page["relations"]
    assert rel["count"] == 1 and rel["facts"][0]["support"] == 2
    (listed,) = [p for p in store.people(con) if p["id"] == ada]
    assert listed["facts"] == 1
