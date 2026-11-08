"""The store's errors reach a client through one pair of handlers in the
door: NotFound is a 404, a refusal (ValueError) a 400, and a KeyError that
is not a NotFound is a bug, a 500. Until 2026-11-08 each route mapped them
by hand, 125 times, and a store bug's KeyError came back as "not found"."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.errors import NotFound


def test_a_miss_reads_without_quotes() -> None:
    assert str(NotFound("no such document: 5")) == "no such document: 5"
    assert str(KeyError("no such document: 5")) == "'no such document: 5'"
    assert isinstance(NotFound("x"), KeyError)  # a caller that catches one


def test_the_door_answers_the_stores_errors_once(client: TestClient) -> None:
    missing = client.get("/doc/987654/domains")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "no such document: 987654"
    doc = client.post("/ingest", json={"text": "wavelets " * 40, "title": "W"})
    refused = client.put(
        f"/doc/{doc.json()['doc_id']}/domains", json={"domains": ["no-such-module"]}
    )
    assert refused.status_code == 400


def test_a_key_error_that_is_a_bug_stays_a_server_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object, **__: object) -> None:
        raise KeyError("summary")  # a missing dict key, not a missing thing

    monkeypatch.setattr(store, "document_domains", broken)
    raw = TestClient(client.app, raise_server_exceptions=False)
    assert raw.get("/doc/1/domains").status_code == 500
