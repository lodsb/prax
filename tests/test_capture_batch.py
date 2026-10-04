"""Several URLs in one call, a result each; a document's title set by
hand or by an agent (AL step 6)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.capture import inbox


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_EMBED", "hash")

    def fake_fetch(url: str, *, timeout: float = 0) -> tuple[bytes, str, str]:
        if "missing" in url:
            raise OSError("404")
        if "refused" in url:
            raise OSError("HTTP Error 403: Forbidden")
        return f"fetched from {url} ".encode() * 10, "text/plain", url

    monkeypatch.setattr(inbox, "fetch_url", fake_fetch)
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_a_batch_answers_each_url_in_order(client: TestClient) -> None:
    urls = [
        "https://example.org/a.txt",
        "https://example.org/missing",
        "ftp://example.org/x",
        "https://example.org/refused",
        "https://example.org/b.txt",
    ]
    out = client.post(
        "/ingest/urls",
        json={"items": [{"url": u, "domains": ["research"]} for u in urls]},
    ).json()
    assert [r["source"] for r in out["results"]] == urls
    assert (out["captured"], out["failed"]) == (2, 3)
    a, missing, ftp, refused, b = out["results"]
    assert a["created"] and a["domains"] == ["research"] and b["doc_id"] != a["doc_id"]
    assert missing["status"] == 502 and "404" in missing["error"]
    assert ftp["status"] == 400
    assert "your own session" in refused["error"]
    many = {"items": [{"url": f"https://example.org/{i}.txt"} for i in range(21)]}
    assert client.post("/ingest/urls", json=many).status_code == 400


def test_a_title_set_keeps_the_old_one(client: TestClient) -> None:
    con = client.app.state.con
    doc = store.ingest_text(con, "a paper about tape echo " * 20, title="2310.01234")[
        "doc_id"
    ]
    r = client.put(f"/doc/{doc}/title", json={"title": "Tape Echo, Revisited"})
    assert r.status_code == 200 and r.json()["changed"]
    meta = store.get_meta(con, doc)
    assert store.get_document(con, doc)["title"] == "Tape Echo, Revisited"
    assert meta["title_history"][-1]["title"] == "2310.01234"
    assert meta["title_source"] == "agent"
    assert client.put(f"/doc/{doc}/title", json={"title": "  "}).status_code == 400
    assert client.put("/doc/999999/title", json={"title": "x"}).status_code == 404
