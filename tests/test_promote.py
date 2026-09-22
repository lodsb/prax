"""Promotion: the flag, the candidates, the auto-flag from pages, the step,
and the doors."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from prax import extraction, models, store


def test_promote_and_unpromote(con: sqlite3.Connection) -> None:
    a = store.ingest_text(con, "alpha " * 50, title="Paper A")["doc_id"]
    flag = store.promote(con, a, reason="central to reverb")
    assert flag["by"] == "human" and flag["reason"] == "central to reverb"
    assert flag["at"].endswith("Z")
    # a second promote keeps the first flag
    again = store.promote(con, a, by="agent", reason="later")
    assert again == flag
    assert store.get_meta(con, a)["promote"] == flag
    with pytest.raises(KeyError):
        store.promote(con, 999)
    assert store.unpromote(con, a) and not store.unpromote(con, a)
    assert "promote" not in store.get_meta(con, a)


def test_promoted_documents_know_who_read_them(con: sqlite3.Connection) -> None:
    a = store.ingest_text(con, "alpha " * 50, title="Paper A")["doc_id"]
    b = store.ingest_text(con, "beta " * 50, title="Paper B")["doc_id"]
    store.promote(con, a)
    store.promote(con, b)
    extraction.apply(con, a, extraction.Extraction(summary="s"), extractor="claude-x")
    rows = store.promoted_documents(con, producer="claude-x")
    assert [(r["doc_id"], r["done"]) for r in rows] == [(a, True), (b, False)]
    # the history counts too: a later local pass does not erase the deep one
    extraction.apply(con, a, extraction.Extraction(summary="s2"), extractor="local:q")
    assert store.promoted_documents(con, producer="claude-x")[0]["done"]
    assert store.extracted_by(store.get_meta(con, a), "local:q")
    # a reading under an older ontology is not the pass being asked for
    meta = store.get_meta(con, b)
    meta["extraction"] = {"extractor": "claude-x", "ontology_version": "3"}
    store.set_meta(con, b, meta)
    assert store.extracted_by(meta, "claude-x")
    assert not store.extracted_by(meta, "claude-x", ontology_version="5")
    assert not store.promoted_documents(con, producer="claude-x")[1]["done"]


def test_candidates_are_scored(con: sqlite3.Connection) -> None:
    a = store.ingest_text(con, "alpha " * 50, title="Paper A")["doc_id"]
    b = store.ingest_text(con, "beta " * 50, title="Paper B")["doc_id"]
    c = store.ingest_text(con, "gamma " * 50, title="Paper C")["doc_id"]
    # A is cited by two library documents, B by one; C is a project member
    for src, dst in (
        ("Paper B", "Paper A"),
        ("Paper C", "Paper A"),
        ("Paper A", "Paper B"),
    ):
        store.link(
            con,
            store.Edge(src, "paper", "cites", dst, "paper"),
            source_doc={"Paper A": a, "Paper B": b, "Paper C": c}[src],
            producer="test",
        )
    store.write_page(con, "thread", "# Thread", kind="project")
    store.add_to_project(con, "thread", c)  # auto-promotes C
    ranked = store.promotion_candidates(con)
    assert [(r["doc_id"], r["score"]) for r in ranked] == [(a, 2), (b, 1)]
    assert ranked[0]["cited"] == 2
    promoted = store.promoted_documents(con)
    assert [p["doc_id"] for p in promoted] == [c]
    assert promoted[0]["promote"]["by"] == "page"
    assert promoted[0]["promote"]["reason"] == "member of project thread"
    # a synthesis source is promoted too, once
    store.write_page(con, "survey", "# Survey", kind="synthesis", annotates=[a, b])
    assert {p["doc_id"] for p in store.promoted_documents(con)} == {a, b, c}
    assert store.promotion_candidates(con) == []


def test_promote_step_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("PRAX_PROMOTE", "PRAX_PROMOTE_MODEL", "PRAX_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    models.reset()
    assert models.resolve("promote").model == "claude-sonnet-5"
    monkeypatch.setenv("PRAX_PROMOTE", "stub")
    assert isinstance(extraction.current("promote"), extraction.StubExtractor)
    monkeypatch.setenv("PRAX_PROMOTE", "none")
    with pytest.raises(RuntimeError, match="steps.promote"):
        extraction.current("promote")


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PRAX_PROMOTE", "stub")
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_api_promote_view(client: TestClient) -> None:
    a = client.post("/ingest", json={"text": "alpha " * 50, "title": "A"}).json()[
        "doc_id"
    ]
    b = client.post("/ingest", json={"text": "beta " * 50, "title": "B"}).json()[
        "doc_id"
    ]
    client.post(
        "/link",
        json={
            "src": "B",
            "src_type": "paper",
            "rel": "cites",
            "dst": "A",
            "dst_type": "paper",
            "source_doc": b,
        },
    )
    r = client.post(f"/doc/{a}/promote", json={"reason": "it matters"})
    assert r.status_code == 200 and r.json()["by"] == "human"
    assert client.post("/doc/999/promote", json={}).status_code == 404
    view = client.get("/promote").json()
    assert view["step"]["model"] == "stub"
    assert [p["doc_id"] for p in view["promoted"]] == [a]
    assert view["promoted"][0]["done"] is False
    assert view["candidates"] == []  # A is promoted already; B is cited by nobody
    assert client.delete(f"/doc/{a}/promote").json() == {"removed": True}
    assert client.get("/promote").json()["candidates"][0]["doc_id"] == a


def test_the_promote_view_says_who_would_do_the_work(client: TestClient) -> None:
    """A pending promotion explains itself: the step is named on the
    command line or it never runs, whatever the flag says."""
    a = client.post("/ingest", json={"text": "alpha " * 50, "title": "A"}).json()[
        "doc_id"
    ]
    client.post(f"/doc/{a}/promote", json={"reason": "it matters"})
    wait = client.get("/promote").json()["waiting"]
    assert wait["step"] == "promote" and wait["watched"] is False
    assert "unless the run names it" in wait["why"]
    assert wait["how"] == "prax work --steps promote"  # the stub costs nothing
    routes = client.get(f"/doc/{a}/routes").json()["routes"]
    promote_route = next(r for r in routes if r["action"]["kind"] == "promote")
    assert promote_route["pending"] is True
    assert "unless the run names it" in promote_route["why"]
    assert all(r["why"] == "" for r in routes if not r["pending"])
