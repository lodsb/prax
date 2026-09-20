"""The maintenance pass: what the store does to itself, without a model
and without a decision — the acronyms table, the document fields, the
domain rules, the duplicate captures — as one job, on request and
nightly."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import config, inbox, store


def test_the_pass_rebuilds_what_is_derived(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # two documents define an acronym; a rule gives documents a domain
    for n in range(2):
        store.ingest_text(
            con,
            f"We use anti-derivative anti-aliasing (ADAA) here, part {n}. " * 20,
            title=f"paper {n}",
        )
    (config.data_dir() / "prax.yaml").write_text(
        "domains:\n  - domains: [research]\n", encoding="utf-8"
    )
    assert store.expand_query(con, "adaa")[0] == ["adaa"]  # not yet known
    report = store.maintain(con)
    assert set(report) == set(store.PASSES)
    assert report["acronyms"]["documents"] == 2
    assert report["acronyms"]["in_two_or_more"] == 1
    assert store.expand_query(con, "adaa")[0] == [
        "adaa",
        "anti derivative anti aliasing",
    ]
    assert report["domains"]["assigned"] == 2 and report["domains"]["rules"] == 1
    assert store.get_meta(con, 1).get("domains") == ["research"]
    assert report["dedupe"] == {"groups": 0, "retired": 0, "kept_apart": 0} | {
        "seconds": report["dedupe"]["seconds"]
    }
    # a subset, and an unknown pass
    assert set(store.maintain(con, only=["fields"])) == {"fields"}
    with pytest.raises(ValueError, match="no such pass"):
        store.maintain(con, only=["nope"])
    jobs = store.list_jobs(con)["recent"]
    assert jobs[0]["name"] == "maintain" and jobs[0]["status"] == "done"


def test_duplicate_captures_are_retired_by_the_pass(con: sqlite3.Connection) -> None:
    html = (
        b"<html><body><p>" + b"the same page, sent twice " * 40 + b"</p></body></html>"
    )
    first = inbox.ingest_upload(con, html, filename="p.html", mime="text/html")
    second = inbox.ingest_upload(
        con, html + b"<!-- -->", filename="p.html", mime="text/html"
    )
    for cap in (first, second):  # as the extension sends them: captures of one URL
        con.execute(
            "UPDATE documents SET source_url = 'https://example.org/p' WHERE id = ?",
            (cap.doc_id,),
        )
        store.set_meta(
            con, cap.doc_id, {**store.get_meta(con, cap.doc_id), "source": "capture"}
        )
        store.index_text(con, cap.doc_id, "the same page, sent twice " * 40)
    con.commit()
    report = store.maintain(con, only=["dedupe"])
    assert report["dedupe"]["groups"] == 1 and report["dedupe"]["retired"] == 1
    retired = [
        c for c in (first, second) if store.is_retired(store.get_meta(con, c.doc_id))
    ]
    assert len(retired) == 1


def test_the_door_runs_it_as_a_job(tmp_path: Path) -> None:
    from prax.api import app

    with TestClient(app) as door:
        door.post("/ingest", json={"text": "x " * 100, "title": "X"})
        assert door.post("/maintain", json={"only": ["nope"]}).status_code == 400
        started = door.post("/maintain", json={"only": ["fields", "dedupe"]}).json()
        assert started["passes"] == ["fields", "dedupe"]
        for _ in range(100):
            row = door.get(f"/jobs/{started['job']}").json()
            if row["status"] != "running":
                break
            time.sleep(0.05)
        assert row["status"] == "done", row
        assert row["note"].startswith("done: fields")


def test_a_reference_list_cites_the_library(con: sqlite3.Connection) -> None:
    """The references pass: a paper's bibliography names two library
    documents (one by title, one by DOI) and one the library lacks; the
    edges carry the score, the stamp keeps the pass from reading the same
    text twice, a changed text is read again with the old edges retired."""
    cited = store.ingest_text(
        con,
        "Antiderivative antialiasing reduces aliasing in stateful systems. " * 20,
        title="Antiderivative antialiasing for stateful systems",
        meta={"creators": [{"name": "Martin Holters"}], "date": "2018-09-01"},
    )["doc_id"]
    by_doi = store.ingest_text(
        con,
        "Wave digital filters and the diode clipper. " * 20,
        title="A wave digital filter model of the diode clipper",
        meta={"doi": "10.5555/wdf.2020"},
    )["doc_id"]
    body = "# Introduction\n\n" + "We build on earlier work. " * 40
    bib = (
        "\n\n# References\n\n"
        "- [1] M. Holters and J. Parker, “Antiderivative antialiasing for"
        " stateful systems,” in _Proc. DAFx_, 2018.\n\n"
        "- [2] K. Werner, “Virtual analog modeling of audio circuitry,”"
        " Ph.D. thesis, Stanford, 2016.\n\n"
        "- [3] A. Author, “Something else entirely,” 2020."
        " doi:10.5555/WDF.2020\n"
    )
    citing = store.ingest_text(
        con, body + bib, title="Aliasing reduction in clipped signals"
    )["doc_id"]
    bibs = store.bibliographies(con)
    assert list(bibs) == [citing]
    report = store.maintain(con, only=["references"])["references"]
    assert report["documents"] == 1 and report["linked"] == 2
    edges = [
        e
        for e in store.traverse(con, "Aliasing reduction in clipped signals", hops=1)
        if e["rel"] == "cites"
    ]
    by_dst = {e["dst"]: e for e in edges}
    assert set(by_dst) == {
        "Antiderivative antialiasing for stateful systems",
        "A wave digital filter model of the diode clipper",
    }
    inferred = by_dst["Antiderivative antialiasing for stateful systems"]
    assert inferred["confidence"] == "INFERRED" and inferred["source_doc"] == citing
    assert inferred["producer"] == "references" and "score 1.00" in inferred["evidence"]
    assert inferred["evidence"].startswith("references: [1] 'Antiderivative")
    exact = by_dst["A wave digital filter model of the diode clipper"]
    assert exact["confidence"] == "EXTRACTED" and "by doi" in exact["evidence"]
    stamp = store.get_meta(con, citing)["references"]
    assert stamp["entries"] == 3 and stamp["linked"] == 2 and stamp["ambiguous"] == 0
    # the same text again: nothing to do
    again = store.maintain(con, only=["references"])["references"]
    assert again.get("documents", 0) == 0 and again["unchanged"] == 1
    # the text read again (one entry gone): the old edges retired, new ones written
    store.index_text(
        con,
        citing,
        body + bib.replace("- [3] A. Author", "- [3] A. Nobody"),
        text_source="t/2",
    )
    third = store.maintain(con, only=["references"])["references"]
    assert third["documents"] == 1 and third["retired"] == 2 and third["linked"] == 2
    live = con.execute(
        "SELECT count(*) FROM edges WHERE source_doc = ? AND valid_to IS NULL",
        (citing,),
    ).fetchone()[0]
    kept = con.execute(
        "SELECT count(*) FROM edges WHERE source_doc = ?", (citing,)
    ).fetchone()[0]
    assert (live, kept) == (2, 4)
    assert by_doi and cited  # both named


def test_reference_chunks_carry_their_links_and_stay_out_of_search(
    con: sqlite3.Connection,
) -> None:
    """The chunker cuts the reference list into reference chunks; the pass
    writes what each cites into the chunk's data (and keeps it on the
    document, so a rechunk puts it back); a search leaves reference
    chunks out unless asked for them by kind; the embed step never
    vectorises them."""
    cited = store.ingest_text(
        con,
        "Antiderivative antialiasing reduces aliasing in stateful systems. " * 20,
        title="Antiderivative antialiasing for stateful systems",
    )["doc_id"]
    citing = store.ingest_text(
        con,
        "# Intro\n\n"
        + "We reduce aliasing with the method of [1]. " * 20
        + "\n\n# References\n\n"
        "- [1] M. Holters and J. Parker, “Antiderivative antialiasing for stateful"
        " systems,” in _Proc. DAFx_, 2018.\n\n"
        "- [2] K. Werner, “Virtual analog modeling of audio circuitry,” Ph.D."
        " thesis, Stanford, 2016.\n",
        title="Aliasing reduction in clipped signals",
    )["doc_id"]
    refs = store.reference_chunks(con, citing)
    assert [r["data"]["number"] for r in refs] == [1, 2]
    assert "cited" not in refs[0]["data"]
    # the reference entries name the cited paper's words, and are not hits
    hits = store.search(con, "antiderivative antialiasing stateful", mode="fts")
    assert {h["doc_id"] for h in hits} == {cited}
    aside = store.search(
        con, "antiderivative antialiasing stateful", mode="fts", kind="reference"
    )
    assert [h["doc_id"] for h in aside] == [citing] and aside[0]["kind"] == "reference"
    assert all(
        r["kind"] != "reference" for r in store.pending_embeddings(con, "hash-test")
    )
    store.maintain(con, only=["references"])
    refs = store.reference_chunks(con, citing)
    cited_by_1 = refs[0]["data"]["cited"]
    assert cited_by_1["doc_id"] == cited and cited_by_1["how"] == "sure"
    assert cited_by_1["title"] == "Antiderivative antialiasing for stateful systems"
    assert cited_by_1["score"] >= 0.9 and "cited" not in refs[1]["data"]
    links = store.get_meta(con, citing)["references"]["links"]
    assert [(k["number"], k["doc_id"]) for k in links] == [(1, cited)]
    # a rechunk rebuilds the chunks from the text and puts the links back
    store.rechunk(con, citing)
    refs = store.reference_chunks(con, citing)
    assert refs[0]["data"]["cited"]["doc_id"] == cited
    # the door's chunk list carries the data the UI links from
    chunks = store.list_chunks(con, citing)
    ref_rows = [c for c in chunks if c["kind"] == "reference"]
    assert ref_rows[0]["data"]["cited"]["doc_id"] == cited
