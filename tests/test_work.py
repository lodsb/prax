"""The work protocol end to end: the door hands out, a worker that only
speaks HTTP does the work, the door takes the results in and stays the
only writer."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import embeddings, inbox, store, work, worker

needs_usearch = pytest.mark.skipif(
    not store.vectors_available(), reason="usearch not installed"
)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_EXTRACT", "stub")
    monkeypatch.setenv("PRAX_TITLES", "none")
    monkeypatch.setenv("PRAX_EMBED", "hash")
    work._leases.clear()
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _door(client: TestClient) -> worker.Door:
    return worker.Door("http://testserver", client=client, name="test-worker")


def test_hand_out_and_take_in_extract(client: TestClient) -> None:
    con = client.app.state.con
    a = client.post(
        "/ingest",
        json={"text": "reverb design by feedback delay networks " * 30, "title": "A"},
    ).json()["doc_id"]
    inbox.ingest_upload(
        con, ("a note on granular synthesis " * 30).encode(), filename="b.txt"
    )
    # captures only: the plain ingest is not a capture
    batch = client.get("/work/extract", params={"limit": 5}).json()
    ids = [i["doc_id"] for i in batch["items"]]
    assert a not in ids and len(ids) == 1
    item = batch["items"][0]
    assert item["header"].startswith("Title:") and item["ontology_version"]
    # leased: a second worker gets nothing
    assert client.get("/work/extract", params={"limit": 5}).json()["items"] == []
    assert (
        client.get("/work/extract", params={"limit": 5, "scope": "all"}).json()[
            "items"
        ][0]["doc_id"]
        == a
    )
    # the worker does it with the stub extractor and posts
    d = _door(client)
    work._leases.clear()  # the hand-outs above went to this test, not the worker
    out = worker.run_once(d, steps=("extract",), scope="all", log_=lambda t: None)
    assert "extract" in out
    for doc_id in ids + [a]:
        assert store.get_meta(con, doc_id)["extraction"]["extractor"] == "stub"
    assert (
        client.get("/work/extract", params={"limit": 5, "scope": "all"}).json()["items"]
        == []
    )
    assert client.get("/work/nope").status_code == 400


def test_parse_and_titles_through_the_door(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    # a pdf the door only registered: the worker fetches the original and parses
    pdf = Path("tests/fixtures/zotero/storage").rglob("*.pdf")
    src = next(iter(pdf), None)
    if src is None:
        pytest.skip("no fixture PDF")
    cap = inbox.ingest_upload(
        con, src.read_bytes(), filename="paper.pdf", mime="application/pdf"
    )
    assert not cap.indexed
    batch = client.get("/work/parse").json()
    assert [i["doc_id"] for i in batch["items"]] == [cap.doc_id]
    assert batch["items"][0]["original"] == f"/doc/{cap.doc_id}/original"
    d = _door(client)
    work._leases.clear()
    out = worker.run_once(d, steps=("parse",), log_=lambda t: None)
    assert out["parse"].startswith("1 parsed")
    assert store.get_document(con, cap.doc_id, max_chars=0)["text_len"] > 0
    assert client.get("/work/parse").json()["items"] == []
    # titles: the file name is not a title; with no titles model the worker
    # says so and the door remembers the try
    batch = client.get("/work/titles").json()
    assert [i["doc_id"] for i in batch["items"]] == [cap.doc_id] and batch["items"][0][
        "why"
    ] == "filename"
    work._leases.clear()
    out = worker.run_once(d, steps=("titles",), log_=lambda t: None)
    assert out["titles"].endswith("1 left")
    assert store.get_meta(con, cap.doc_id)["titles_tried"]["why"] == "no titles model"
    assert client.get("/work/titles").json()["items"] == []
    # a retitle through the door
    rep = client.post(
        "/work/titles",
        json={
            "results": [
                {
                    "doc_id": cap.doc_id,
                    "title": "A Real Title",
                    "source": "test",
                    "confidence": "printed",
                }
            ]
        },
    ).json()
    assert rep["applied"] == 1
    assert store.get_document(con, cap.doc_id, max_chars=0)["title"] == "A Real Title"


@needs_usearch
def test_embed_through_the_door(client: TestClient) -> None:
    con = client.app.state.con
    emb = embeddings.current()
    assert emb is not None
    doc = client.post(
        "/ingest",
        json={"text": "wave digital filters for diode clippers " * 30, "title": "W"},
    ).json()["doc_id"]
    batch = client.get("/work/embed", params={"limit": 50}).json()
    assert batch["model"] == emb.name and batch["chunks"] and batch["fields"]
    assert (
        client.get("/work/embed", params={"limit": 50}).json()["chunks"] == []
    )  # leased
    d = _door(client)
    work._leases.clear()
    out = worker.run_once(d, steps=("embed",), log_=lambda t: None)
    assert out["embed"].endswith("vectors")
    assert store.count_pending_embeddings(con, emb.name) == 0
    assert store.count_pending_document_embeddings(con, emb.name) == 0
    hits = client.get(
        "/search", params={"q": "diode clipper wave digital", "mode": "vec"}
    ).json()
    assert hits and hits[0]["doc_id"] == doc
    merged = client.post("/vectors/merge").json()
    assert merged["chunks"]["delta"] == 0
    # a wrong model is refused
    assert (
        client.post(
            "/work/embed", json={"model": "other", "chunks": [], "fields": []}
        ).status_code
        == 400
    )


def test_session_job_and_watch_once(client: TestClient, tmp_path: Path) -> None:
    con = client.app.state.con
    drop = tmp_path / "prax-inbox"
    drop.mkdir()
    f = drop / "note.txt"
    f.write_text("a dropped note about reverb " * 20, encoding="utf-8")
    (drop / "note.txt.json").write_text(
        json.dumps({"title": "Dropped", "tags": ["t"], "domains": ["research"]}),
        encoding="utf-8",
    )
    import os
    import time

    for p in drop.iterdir():
        os.utime(p, (time.time() - 10, time.time() - 10))
    d = _door(client)
    worker.watch(d, folders=[drop], once=True, log_=lambda t: None)
    assert not f.exists()
    recent = inbox.recent(con)
    assert recent and recent[0]["title"] == "Dropped" and recent[0]["tags"] == ["t"]
    jobs = store.list_jobs(con)
    assert (
        jobs["recent"]
        and jobs["recent"][0]["name"] == "worker"
        and jobs["recent"][0]["host"] == "test-worker"
    )
    assert jobs["recent"][0]["status"] == "done"


def test_leases_expire(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    con = client.app.state.con
    inbox.ingest_upload(con, ("a note " * 60).encode(), filename="n.txt")
    assert len(client.get("/work/extract").json()["items"]) == 1
    assert client.get("/work/extract").json()["items"] == []
    monkeypatch.setattr(work, "LEASE_SECONDS", 0)
    # a fresh hand-out after the lease ran out
    work._leases.clear()
    assert len(client.get("/work/extract").json()["items"]) == 1
