"""The store's errors reach a client through one pair of handlers in the
door: NotFound is a 404, a refusal (ValueError) a 400, and a KeyError that
is not a NotFound is a bug, a 500. Until 2026-11-08 each route mapped them
by hand, 125 times, and a store bug's KeyError came back as "not found"."""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import config, store
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


@pytest.mark.parametrize(
    "bug",
    [
        json.JSONDecodeError("Expecting value", "{", 1),  # a corrupt meta row
        config.ConfigError("vectors.dtype: f12 is not one of f16, f32, i8"),
        UnicodeDecodeError("utf-8", bytes([0xFF]), 0, 1, "invalid start byte"),
    ],
)
def test_a_value_error_that_is_a_bug_is_a_logged_server_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    bug: ValueError,
) -> None:
    """A ValueError the caller did not cause (a corrupt row, a host's
    mistyped setting) was answered as the caller's 400 and logged nowhere
    (the quality review of 2026-11-11, findings 13 and 22)."""

    def broken(*_: object, **__: object) -> None:
        raise bug

    monkeypatch.setattr(store, "document_domains", broken)
    raw = TestClient(client.app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="prax.door"):
        answer = raw.get("/doc/1/domains")
    assert answer.status_code == 500
    assert any(r.exc_info and r.exc_info[1] is bug for r in caplog.records)


def test_a_refusal_is_logged_with_its_route(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    doc = client.post("/ingest", json={"text": "wavelets " * 40, "title": "W"})
    with caplog.at_level(logging.INFO, logger="prax.door"):
        refused = client.put(
            f"/doc/{doc.json()['doc_id']}/domains", json={"domains": ["no-such"]}
        )
    assert refused.status_code == 400
    assert any(
        "/domains" in r.getMessage() and "no-such" in r.getMessage()
        for r in caplog.records
    )


def test_no_route_catches_a_plain_key_error() -> None:
    """A miss reaches the door as NotFound, which its handler answers 404;
    a route that caught KeyError answered a bug's missing dict key as "not
    found" too (the quality review of 2026-11-11, findings 14, 24, 41)."""
    api = Path(__file__).parents[1] / "src" / "prax" / "api"
    found = []
    for path in sorted(api.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            names = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            if not any(isinstance(n, ast.Name) and n.id == "KeyError" for n in names):
                continue
            line = lines[node.lineno - 1]
            if "the caller's own data" not in line:  # a dict the request sent
                found.append(f"{path.name}:{node.lineno}")
    assert found == []
