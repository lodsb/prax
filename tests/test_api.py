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
    body = {
        "src": "A",
        "src_type": "concept",
        "rel": "extends",
        "dst": "B",
        "dst_type": "concept",
    }
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
    body = {
        "src": "A",
        "src_type": "x",
        "rel": "r",
        "dst": "B",
        "dst_type": "x",
        "confidence": "GUESS",
    }
    assert client.post("/link", json=body).status_code == 400


def test_search_kind_filter_and_chunk_route(client: TestClient) -> None:
    table = "Table 1: sizes\n\n| part | mm |\n|---|---|\n| bolt | 12 |\n"
    r = client.post("/ingest", json={"text": table, "title": "t"})
    hits = client.get("/search", params={"q": "bolt", "kind": "table"}).json()
    assert hits and hits[0]["kind"] == "table"
    assert hits[0]["doc_id"] == r.json()["doc_id"]
    chunk = client.get(f"/chunk/{hits[0]['chunk_id']}").json()
    assert chunk["data"]["rows"] == [["bolt", "12"]]
    assert chunk["locator"]["char_start"] == 0
    assert client.get("/chunk/999999").status_code == 404
    bad = client.get("/search", params={"q": "bolt", "kind": "audio"})
    assert bad.status_code == 400


def test_browsing_endpoints_and_ui(client: TestClient) -> None:
    pdf = client.post(
        "/ingest/file",
        files={"file": ("paper.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"title": "A fake paper"},
    ).json()
    note = client.post(
        "/ingest",
        json={
            "text": "# Intro\n\nGranular clouds.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
            "title": "note",
            "meta": {"source": "zotero"},
        },
    ).json()
    listing = client.get("/documents").json()
    assert listing["total"] == 2
    assert [d["id"] for d in listing["items"]] == [note["doc_id"], pdf["doc_id"]]
    assert listing["items"][0]["n_chunks"] >= 1 and listing["items"][0]["meta"] == {
        "source": "zotero"
    }
    assert client.get("/documents", params={"title": "FAKE"}).json()["total"] == 1
    assert client.get("/documents", params={"source": "zotero"}).json()["total"] == 1
    assert (
        client.get("/documents", params={"mime": "application/pdf"}).json()["total"]
        == 1
    )

    orig = client.get(f"/doc/{pdf['doc_id']}/original")
    assert orig.status_code == 200 and orig.content == b"%PDF-1.4 fake"
    assert orig.headers["content-type"].startswith("application/pdf")
    assert "inline" in orig.headers["content-disposition"]
    assert client.get("/doc/999/original").status_code == 404

    text = client.get(f"/doc/{note['doc_id']}/text")
    assert text.status_code == 200 and text.text.startswith("# Intro")
    assert text.headers["content-type"].startswith("text/markdown")

    chunks = client.get(f"/doc/{note['doc_id']}/chunks").json()
    assert [c["kind"] for c in chunks] == ["text", "table"]
    assert chunks[1]["data"]["rows"] == [["1", "2"]] and chunks[1]["heading"] == [
        "Intro"
    ]
    assert client.get("/doc/999/chunks").status_code == 404

    client.post(
        "/link",
        json={
            "src": "A fake paper",
            "src_type": "paper",
            "rel": "authored_by",
            "dst": "Ada Lovelace",
            "dst_type": "author",
        },
    )
    ents = client.get("/entities", params={"q": "lovelace"}).json()
    assert ents == [
        {"id": ents[0]["id"], "name": "Ada Lovelace", "type": "author", "degree": 1}
    ]

    assert client.get("/", follow_redirects=False).status_code == 307
    page = client.get("/ui/")
    assert page.status_code == 200 and "<title>prax</title>" in page.text
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/ui/vendor/marked.min.js").status_code == 200
