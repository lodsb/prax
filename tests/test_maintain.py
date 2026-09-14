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
