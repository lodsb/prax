"""FastAPI door: every endpoint through TestClient against a tmp data dir."""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax.api import app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:  # runs the lifespan: opens the store
        yield c


def test_ingest_search_get(client: TestClient) -> None:
    r = client.post("/ingest", json={"text": "alpha beta gamma", "title": "abc"})
    assert r.status_code == 200 and r.json()["created"]
    doc_id = r.json()["doc_id"]

    hits = client.get("/search", params={"q": "beta"}).json()
    assert hits[0]["doc_id"] == doc_id and hits[0]["title"] == "abc"

    doc = client.get(f"/get/{doc_id}").json()
    assert doc["text"] == "alpha beta gamma" and doc["meta"] == {}

    window = client.get(f"/get/{doc_id}", params={"offset": 6, "max_chars": 4}).json()
    assert window["text"] == "beta" and window["truncated"]


def test_get_unknown_is_404(client: TestClient) -> None:
    assert client.get("/get/12345").status_code == 404


def test_ingest_file_text_and_binary(client: TestClient) -> None:
    r = client.post(
        "/ingest/file",
        files={"file": ("notes.md", b"granular synthesis notes", "text/markdown")},
        data={"title": "notes"},
    )
    assert r.status_code == 200 and r.json()["created"]
    assert client.get("/search", params={"q": "granular"}).json()[0]["title"] == "notes"

    r = client.post(
        "/ingest/file",
        files={"file": ("paper.pdf", b"%PDF-1.4 binary", "application/pdf")},
    )
    doc = client.get(f"/get/{r.json()['doc_id']}").json()
    assert doc["mime"] == "application/pdf" and doc["parsed_at"] is None
    assert doc["title"] == "paper.pdf"


def test_ingest_file_guesses_mime_from_name(client: TestClient) -> None:
    r = client.post(
        "/ingest/file",
        files={"file": ("x.txt", b"plain words", "application/octet-stream")},
    )
    doc = client.get(f"/get/{r.json()['doc_id']}").json()
    assert doc["mime"] == "text/plain" and doc["text"] == "plain words"


def test_search_with_punctuation_is_200(client: TestClient) -> None:
    client.post("/ingest", json={"text": "STFT-based analysis"})
    r = client.get("/search", params={"q": "STFT-based: what's (this)?"})
    assert r.status_code == 200 and r.json()


def test_link_and_traverse(client: TestClient) -> None:
    body = {"src": "A", "src_type": "concept", "rel": "extends",
            "dst": "B", "dst_type": "concept"}
    assert client.post("/link", json=body).json()["edge_id"] == 1
    body.update(src="B", dst="C")
    client.post("/link", json=body)
    body.update(src="C", dst="D")
    client.post("/link", json=body)

    one = client.get("/traverse", params={"entity": "A", "hops": 1}).json()
    assert {(e["src"], e["dst"]) for e in one} == {("A", "B")}
    capped = client.get("/traverse", params={"entity": "A", "hops": 9}).json()
    assert {(e["src"], e["dst"]) for e in capped} == {("A", "B"), ("B", "C")}
    assert capped[0]["hop"] == 1 and capped[-1]["hop"] == 2


def test_link_bad_confidence_is_400(client: TestClient) -> None:
    body = {"src": "A", "src_type": "x", "rel": "r", "dst": "B", "dst_type": "x",
            "confidence": "GUESS"}
    assert client.post("/link", json=body).status_code == 400
