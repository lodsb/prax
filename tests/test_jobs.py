"""Jobs bookkeeping, the change stamp, and the capture pipeline."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import embeddings, extraction, pipeline, store


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
    assert client.get("/jobs").json() == {"running": [], "recent": []}
    assert client.post("/vectors/release").json()["released"] == 0


def test_extract_documents_with_the_stub(con: sqlite3.Connection) -> None:
    ids = [
        store.ingest_text(con, f"paper {i} about reverb " * 30, title=f"P{i}")["doc_id"]
        for i in range(3)
    ]
    ext = extraction.StubExtractor()
    with store.Job(con, "extract", total=3) as job:
        rep = pipeline.extract_documents(con, ids, ext, job=job, log=lambda t: None)
    assert rep.n == 3 and not rep.errors
    assert store.get_meta(con, ids[0])["extraction"]["extractor"] == ext.name
    assert store.list_jobs(con)["recent"][0]["done"] == 3


def test_process_captures_is_careful(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prax import inbox

    monkeypatch.setenv("PRAX_EXTRACT", "stub")
    monkeypatch.setenv("PRAX_TITLES", "none")
    monkeypatch.setenv("PRAX_EMBED", "hash")
    long = inbox.ingest_upload(
        con, ("a long note about reverb " * 40).encode(), filename="long.txt"
    )
    short = inbox.ingest_upload(con, b"too short", filename="short.txt")
    image = inbox.ingest_upload(
        con, b"\x89PNG fake", filename="pic.png", mime="image/png"
    )
    zotero = store.ingest_text(
        con, "a zotero paper " * 40, title="Z", meta={"source": "zotero"}
    )["doc_id"]
    out = pipeline.process_captures(con, log=lambda t: None)
    assert "extract" in out and "3 documents" not in out["extract"]
    assert store.get_meta(con, long.doc_id).get(
        "extraction"
    )  # the long capture was read
    assert not store.get_meta(con, short.doc_id).get("extraction")  # too short
    assert not store.get_meta(con, image.doc_id).get("extraction")  # an image
    assert not store.get_meta(con, zotero).get("extraction")  # not a capture
    names = [j["name"] for j in store.list_jobs(con)["recent"]]
    assert "extract" in names and "embed" in names
    emb = embeddings.current()
    assert emb is not None and store.count_pending_embeddings(con, emb.name) == 0
    # a paid model is never run unasked
    monkeypatch.setenv("PRAX_EXTRACT", "claude-sonnet-5")
    more = inbox.ingest_upload(
        con, ("another long note " * 40).encode(), filename="more.txt"
    )
    out = pipeline.process_captures(con, embed=False, log=lambda t: None)
    assert out["extract"].startswith("skipped") and "paid" in out["extract"]
    assert not store.get_meta(con, more.doc_id).get("extraction")
    # nothing to do says nothing
    monkeypatch.setenv("PRAX_EXTRACT", "stub")
    pipeline.process_captures(con, embed=False, log=lambda t: None)
    assert pipeline.process_captures(con, embed=False, log=lambda t: None) == {}


def test_index_busy_is_detected(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_EMBED", "hash")
    emb = embeddings.current()
    assert emb is not None
    store.ingest_text(con, "vectors please " * 30, title="V")
    rep = pipeline.embed_pending(con, emb, log=lambda t: None)
    assert rep["chunks"] >= 1 and rep["fields"] >= 1
    assert pipeline.index_writable(emb.name)  # our own views do not count
    # a save that keeps failing (the file mapped elsewhere) ends as IndexBusy,
    # after asking the door to let go each time
    asked = []

    def refuse(model: str) -> dict:
        raise OSError(32, "mapped elsewhere")

    monkeypatch.setattr(store, "save_vectors", refuse)
    monkeypatch.setattr(
        pipeline,
        "time",
        type(
            "T",
            (),
            {
                "sleep": staticmethod(lambda s: None),
                "monotonic": staticmethod(lambda: 0.0),
            },
        ),
    )
    store.ingest_text(con, "more vectors please " * 30, title="W")
    with pytest.raises(pipeline.IndexBusy):
        pipeline.embed_pending(con, emb, release=lambda: asked.append(1))
    assert (
        len(asked) == pipeline.LOCK_RETRIES
        if hasattr(pipeline, "LOCK_RETRIES")
        else len(asked) >= 1
    )
