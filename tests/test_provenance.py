"""Provenance on edges: producer and run at every door, the backfill for
older edges, retiring a run, the extraction history on documents."""

from __future__ import annotations

import sqlite3

import pytest

from prax import extraction, review, store

E = store.Edge
T = extraction.Triple


def test_link_records_producer_and_run(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "t", title="P")["doc_id"]
    eid = store.link(
        con,
        E("P", "paper", "about", "grains", "concept"),
        source_doc=doc,
        producer="claude-sonnet-5",
        run="msgbatch_1",
    )
    row = con.execute("SELECT producer, run FROM edges WHERE id = ?", (eid,)).fetchone()
    assert tuple(row) == ("claude-sonnet-5", "msgbatch_1")
    hit = store.traverse(con, "P", hops=1)[0]
    assert (hit["producer"], hit["run"]) == ("claude-sonnet-5", "msgbatch_1")
    # apply stamps the extractor as producer and keeps a history of stamps
    ex = extraction.Extraction(
        triples=[T("P", "paper", "uses", "phase vocoder", "method", "EXTRACTED", "q")],
        summary="s",
    )
    extraction.apply(con, doc, ex, extractor="stub", run="run-1")
    extraction.apply(con, doc, ex, extractor="stub", run="run-2")
    meta = store.get_meta(con, doc)
    assert meta["extraction"]["run"] == "run-2"
    assert [h["run"] for h in meta["extraction_history"]] == ["run-1"]
    assert {r["producer"] for r in store.traverse(con, "P", hops=1)} == {
        "claude-sonnet-5",
        "stub",
    }
    # pages and replay tag themselves
    page = store.write_page(con, "note-p", "Note.", kind="addendum", annotates=[doc])
    edge = store.traverse(con, "Note p", hops=1)[0]
    assert (edge["producer"], edge["run"]) == ("page", "note-p@1")
    store.queue_review(
        con,
        src="P",
        src_type="paper",
        rel="uses",
        dst="Fourier",
        dst_type="concept",
        reason="rule",
        source_doc=doc,
    )
    review.replay(con)
    replayed = [e for e in store.traverse(con, "P", hops=1) if e["dst"] == "Fourier"]
    assert replayed and replayed[0]["producer"] == "replay"
    assert page["created"]


def test_backfill_and_retire(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(
        con,
        "t",
        title="P",
        meta={"source": "zotero", "extraction": {"extractor": "claude-sonnet-5"}},
    )["doc_id"]
    ids = [
        store.link(
            con, E("P", "paper", "authored_by", "Ada", "author"), source_doc=doc
        ),
        store.link(
            con,
            E("P", "paper", "cites", "Q", "paper"),
            source_doc=doc,
            evidence="crossref doi:x references y",
        ),
        store.link(
            con,
            E("P", "paper", "about", "grains", "concept"),
            source_doc=doc,
            evidence="quoted",
            ontology_version="1",
        ),
        store.link(con, E("P", "paper", "about", "delay", "concept")),
    ]
    assert all(
        con.execute("SELECT producer FROM edges WHERE id = ?", (i,)).fetchone()[0]
        is None
        for i in ids
    )
    counts = store.backfill_provenance(con)
    assert counts["zotero"] == 1 and counts["crossref"] == 1
    assert counts["extraction"] == 1 and counts["manual"] == 1
    tagged = {
        tuple(r)
        for r in con.execute("SELECT rel, producer, run FROM edges ORDER BY id")
    }
    assert tagged == {
        ("authored_by", "zotero", "backfill"),
        ("cites", "crossref", "backfill"),
        ("about", "claude-sonnet-5", "ontology-v1"),
        ("about", "manual", "backfill"),
    }
    assert store.backfill_provenance(con) == {
        "crossref": 0,
        "openalex": 0,
        "page": 0,
        "zotero": 0,
        "extraction": 0,
        "manual": 0,
    }
    summary = {
        (r["producer"], r["run"]): r["live"] for r in store.provenance_summary(con)
    }
    assert summary[("claude-sonnet-5", "ontology-v1")] == 1
    # retire the model's run: its edges end, the others stay, history remains
    assert store.retire_run(con, producer="claude-sonnet-5", run="ontology-v1") == 1
    assert store.retire_run(con, producer="claude-sonnet-5", run="ontology-v1") == 0
    live = {e["dst"] for e in store.traverse(con, "P", hops=1)}
    assert live == {"Ada", "Q", "delay"}
    assert (
        con.execute("SELECT count(*) FROM edges WHERE valid_to IS NOT NULL").fetchone()[
            0
        ]
        == 1
    )
    with pytest.raises(ValueError):
        store.retire_run(con)
