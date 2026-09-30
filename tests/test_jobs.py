"""Jobs bookkeeping, the change stamp, and the capture pipeline."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import store


def test_job_lifecycle_and_listing(con: sqlite3.Connection) -> None:
    with store.Job(con, "extract", total=3, note="stub") as job:
        job.update(done=1)
        listing = store.list_jobs(con)
        assert [j["name"] for j in listing["running"]] == ["extract"]
        assert listing["running"][0]["done"] == 1 and not listing["running"][0]["stale"]
        job.update(done=3, note="doc 3")
    listing = store.list_jobs(con)
    assert not listing["running"] and listing["recent"][0]["status"] == "done"
    assert listing["recent"][0]["note"] == "doc 3"
    with pytest.raises(RuntimeError), store.Job(con, "embed"):
        raise RuntimeError("index busy")
    failed = store.list_jobs(con)["recent"][0]
    assert failed.get("status") == "failed" and "index busy" in failed["note"]
    # a job without a heartbeat is stale
    jid = store.job_start(con, "titles")
    con.execute(
        "UPDATE jobs SET updated_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (jid,)
    )
    con.commit()
    assert store.list_jobs(con)["running"][0]["stale"]


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_changes_stamp_moves_on_writes(client: TestClient) -> None:
    a = client.get("/changes").json()
    assert a["jobs"] == 0 and "-" in a["stamp"]
    client.post("/ingest", json={"text": "alpha " * 20, "title": "A"})
    b = client.get("/changes").json()
    assert b["stamp"] != a["stamp"]
    assert client.get("/changes").json()["stamp"] == b["stamp"]  # reads move nothing
    j = client.get("/jobs").json()
    assert j["running"] == [] and j["recent"] == []
    assert j["host"]["name"] and "ram_free_mb" in j["host"]  # what the host has left
    assert client.post("/vectors/release").json()["released"] == 0


def test_reap_closes_jobs_without_heartbeat(con) -> None:
    jid = store.job_start(con, "worker", host="elsewhere", pid=0)
    assert store.job_reap(con) == 0  # fresh heartbeat: alive
    con.execute(
        "UPDATE jobs SET updated_at = datetime('now', '-1 hour') WHERE id = ?",
        (jid,),
    )
    con.commit()
    assert store.job_reap(con) == 1
    row = con.execute("SELECT status, note FROM jobs WHERE id = ?", (jid,)).fetchone()
    assert row["status"] == "failed" and "no heartbeat" in row["note"]
