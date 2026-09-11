"""Captures: the drop folder, uploads, sent pages, fetched URLs, the
recent list, and the door's endpoints."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import config, inbox, ontology, parsers, store

needs_trafilatura = pytest.mark.skipif(
    not parsers.by_name("trafilatura").available(), reason="trafilatura not installed"
)

PAGE = """<!doctype html><html><head><title>A page about reverb</title></head>
<body><nav>Home · About</nav><main><article><h1>A page about reverb</h1>
<p>Feedback delay networks make a reverb from a few delay lines and a mixing
matrix. The matrix keeps the energy while spreading the echoes.</p>
<p>Schroeder used comb and allpass filters for the same purpose in 1962; the
network form generalizes both into one structure that is easy to tune.</p>
</article></main><footer>© nobody</footer></body></html>"""

FAMILY = """
module: family
version: 1
requires: [core]
entity_types:
  relative: {parent: person, description: a family member}
relation_types:
  depicts: {domain: [document], range: [person], description: shown in the picture}
"""


@pytest.fixture()
def modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = tmp_path / "onto"
    d.mkdir()
    for f in Path(config.ONTOLOGY_PATH).glob("*.yaml"):
        (d / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    (d / "family.yaml").write_text(FAMILY, encoding="utf-8")
    monkeypatch.setenv("PRAX_ONTOLOGY", str(d))
    assert "family" in ontology.current().modules


def _old(path: Path) -> None:
    t = time.time() - 10
    os.utime(path, (t, t))


def test_canonical_url() -> None:
    assert (
        inbox.canonical_url("HTTPS://Example.org/a/b?utm_source=x&id=3&fbclid=9#frag")
        == "https://example.org/a/b?id=3"
    )
    assert inbox.canonical_url("http://x.org") == "http://x.org/"


def test_scan_registers_removes_and_dedupes(
    con: sqlite3.Connection, tmp_path: Path, modules: None
) -> None:
    root = tmp_path / "inbox"
    (root / "family").mkdir(parents=True)
    note = root / "note.txt"
    note.write_text("a plain note about reverb design " * 5, encoding="utf-8")
    photo = root / "family" / "grandma.txt"
    photo.write_text("grandma at the lake, summer 1962 " * 5, encoding="utf-8")
    side = root / "family" / "grandma.txt.json"
    side.write_text(
        json.dumps(
            {
                "title": "Grandma 1962",
                "tags": ["photo"],
                "domains": ["family", "research"],
            }
        ),
        encoding="utf-8",
    )
    pdf = root / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really a pdf")
    fresh = root / "still-writing.txt"
    fresh.write_text("x", encoding="utf-8")
    (root / "download.pdf.part").write_bytes(b"...")
    for p in (note, photo, side, pdf):
        _old(p)
    rep = inbox.scan(con, root)
    assert len(rep.registered) == 3 and rep.waiting == 1 and not rep.failed
    assert (
        not note.exists()
        and not photo.exists()
        and not side.exists()
        and not pdf.exists()
    )
    assert fresh.exists() and (root / "download.pdf.part").exists()
    assert not (root / "family").exists()  # emptied folders go
    by_title = {
        store.get_document(con, i, max_chars=0)["title"]: i for i in rep.registered
    }
    assert set(by_title) == {"note", "Grandma 1962", "paper"}
    g = store.get_document(con, by_title["Grandma 1962"], max_chars=0)
    assert g["meta"]["domains"] == ["family", "research"]
    assert g["meta"]["tags"] == ["photo"] and g["meta"]["source"] == "inbox"
    assert g["meta"]["capture"]["session"].startswith("inbox-")
    assert g["original_path"] == "family/grandma.txt" and g["text_len"] > 0
    paper = store.get_document(con, by_title["paper"], max_chars=0)
    assert paper["mime"] == "application/pdf" and paper["text_len"] == 0  # queue's
    # the folder alone names the domain; a re-drop is a duplicate
    again = root / "family" / "grandma.txt"
    again.parent.mkdir()
    again.write_text("grandma at the lake, summer 1962 " * 5, encoding="utf-8")
    _old(again)
    rep2 = inbox.scan(con, root)
    assert rep2.duplicates == [by_title["Grandma 1962"]] and not rep2.registered
    assert not again.exists()
    other = root / "family" / "uncle.txt"
    other.parent.mkdir()
    other.write_text("uncle at the lake " * 5, encoding="utf-8")
    _old(other)
    rep3 = inbox.scan(con, root)
    assert store.document_domains(con, rep3.registered[0]) == ["family"]


def test_scan_from_somebodys_folder_leaves_files(
    con: sqlite3.Connection, tmp_path: Path, modules: None
) -> None:
    folder = tmp_path / "papers"
    (folder / "sub").mkdir(parents=True)
    a = folder / "a.txt"
    a.write_text("paper a about reverb " * 5, encoding="utf-8")
    b = folder / "sub" / "b.pdf"
    b.write_bytes(b"%PDF-1.4 fake b")
    for p in (a, b):
        _old(p)
    rep = inbox.scan(con, folder, consume=False, domains=["family"])
    assert len(rep.registered) == 2 and a.exists() and b.exists()
    assert (folder / "sub").exists()
    docs = {store.get_document(con, i, max_chars=0)["title"]: i for i in rep.registered}
    assert store.document_domains(con, docs["a"]) == ["family"]
    assert (
        store.get_document(con, docs["b"], max_chars=0)["original_path"] == "sub/b.pdf"
    )
    again = inbox.scan(con, folder, consume=False)
    assert not again.registered and sorted(again.duplicates) == sorted(rep.registered)
    assert a.exists() and b.exists()


def test_scan_moves_refused_files_to_failed(
    con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    bad = root / "bad.txt"
    bad.write_text("x " * 20, encoding="utf-8")
    _old(bad)

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("refused")

    monkeypatch.setattr(inbox, "ingest_bytes", boom)
    rep = inbox.scan(con, root)
    assert rep.failed and (root / "failed" / "bad.txt").exists() and not bad.exists()


@needs_trafilatura
def test_ingest_html_indexes_and_links_recaptures(
    con: sqlite3.Connection, modules: None
) -> None:
    cap = inbox.ingest_html(
        con,
        PAGE,
        url="https://example.org/reverb?utm_source=mail#top",
        title="A page about reverb",
        domains=["research"],
        session="s1",
    )
    assert cap.created and cap.indexed and cap.domains == ["research"]
    doc = store.get_document(con, cap.doc_id)
    assert doc["source_url"] == "https://example.org/reverb"
    assert "Feedback delay networks" in doc["text"] and "© nobody" not in doc["text"]
    assert (
        doc["meta"]["source"] == "capture" and doc["meta"]["capture"]["session"] == "s1"
    )
    assert doc["meta"]["text_source"].startswith("trafilatura")
    hits = store.search(con, "feedback delay networks", 5)
    assert hits and hits[0]["doc_id"] == cap.doc_id
    # the same bytes again: the same document
    same = inbox.ingest_html(con, PAGE, url="https://example.org/reverb")
    assert same.doc_id == cap.doc_id and not same.created
    # a changed page: a new document that knows its predecessor
    changed = inbox.ingest_html(
        con, PAGE.replace("1962", "1961"), url="https://example.org/reverb?fbclid=1"
    )
    assert changed.created and changed.previous == cap.doc_id
    assert store.get_meta(con, changed.doc_id)["previous_capture"] == cap.doc_id
    recent = inbox.recent(con)
    assert [r["doc_id"] for r in recent] == [changed.doc_id, cap.doc_id]
    assert recent[0]["indexed"] and not recent[0]["extracted"]
    assert recent[0]["previous_capture"] == cap.doc_id


def test_html_title() -> None:
    assert inbox.html_title(b"<html><head><TITLE>\n A &amp; B </TITLE>") == "A & B"
    assert inbox.html_title(b"<html><body>no title") is None


def test_ingest_url_fetches(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_fetch(url: str, *, timeout: float = 0) -> tuple[bytes, str, str]:
        if url.endswith(".pdf"):
            return b"%PDF-1.4 fake", "application/pdf", url
        return PAGE.encode(), "text/html", "https://example.org/final"

    monkeypatch.setattr(inbox, "fetch_url", fake_fetch)
    cap = inbox.ingest_url(con, "https://example.org/start")
    doc = store.get_document(con, cap.doc_id, max_chars=0)
    assert doc["title"] == "A page about reverb"  # from <title>
    assert doc["source_url"] == "https://example.org/final"
    assert doc["meta"]["requested_url"] == "https://example.org/start"
    assert doc["mime"] == "text/html"
    pdf = inbox.ingest_url(con, "https://example.org/files/paper.pdf", title=None)
    d2 = store.get_document(con, pdf.doc_id, max_chars=0)
    assert d2["mime"] == "application/pdf" and d2["title"] == "paper.pdf"
    assert d2["original_path"] == "paper.pdf" and not pdf.indexed
    with pytest.raises(ValueError):
        inbox.check_url("file:///etc/passwd")


def test_upload_uses_rules_when_no_domain_is_given(
    con: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    modules: None,
) -> None:
    cfg = tmp_path / "prax.yaml"
    cfg.write_text(
        "domains:\n  - match: {source: upload}\n    domains: [family]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRAX_CONFIG", str(cfg))
    cap = inbox.ingest_upload(con, b"hello there " * 10, filename="C:\\tmp\\hello.txt")
    assert cap.domains == ["family"] and cap.indexed
    doc = store.get_document(con, cap.doc_id, max_chars=0)
    assert doc["title"] == "hello.txt" and doc["original_path"] == "hello.txt"
    assert doc["mime"] == "text/plain" and doc["meta"]["domains_by"] == "rule"
    # an explicit domain is added to what the document has
    again = inbox.ingest_upload(
        con, b"hello there " * 10, filename="hello.txt", domains=["research"]
    )
    assert again.doc_id == cap.doc_id and again.domains == ["family", "research"]
    with pytest.raises(ValueError, match="unknown domain"):
        inbox.ingest_upload(con, b"other " * 10, filename="o.txt", domains=["cooking"])


@pytest.fixture()
def client(modules: None) -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_api_captures(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    r = client.post(
        "/ingest/file",
        files={"file": ("notes.txt", b"an uploaded note " * 10, "text/plain")},
        data={"domains": "family, research", "tags": "todo", "session": "u1"},
    ).json()
    assert r["created"] and r["indexed"] and r["domains"] == ["family", "research"]
    doc = client.get(f"/get/{r['doc_id']}").json()
    assert doc["meta"]["tags"] == ["todo"] and doc["meta"]["capture"]["session"] == "u1"
    bad = client.post(
        "/ingest/file",
        files={"file": ("x.txt", b"x " * 10, "text/plain")},
        data={"domains": "cooking"},
    )
    assert bad.status_code == 400
    if parsers.by_name("trafilatura").available():
        h = client.post(
            "/ingest/html",
            json={
                "url": "https://example.org/p",
                "html": PAGE,
                "domains": ["research"],
            },
        ).json()
        assert h["indexed"] and h["domains"] == ["research"]

    def fake_fetch(url: str, *, timeout: float = 0) -> tuple[bytes, str, str]:
        if "missing" in url:
            raise OSError("404")
        return b"fetched text " * 10, "text/plain", url

    monkeypatch.setattr(inbox, "fetch_url", fake_fetch)
    u = client.post("/ingest/url", json={"url": "https://example.org/t.txt"}).json()
    assert u["created"] and u["indexed"]
    assert (
        client.post(
            "/ingest/url", json={"url": "https://example.org/missing"}
        ).status_code
        == 502
    )
    assert (
        client.post("/ingest/url", json={"url": "ftp://example.org/x"}).status_code
        == 400
    )
    t = client.post(
        "/ingest",
        json={"text": "typed text " * 10, "title": "t", "domains": ["family"]},
    ).json()
    assert client.get(f"/doc/{t['doc_id']}/domains").json()["domains"] == ["family"]
    # an HTML original is served sandboxed
    h = client.post("/ingest/html", json={"url": "https://example.org/s", "html": PAGE})
    orig = client.get(f"/doc/{h.json()['doc_id']}/original")
    assert orig.status_code == 200
    assert orig.headers["content-security-policy"].startswith("sandbox")
    view = client.get("/inbox").json()
    assert view["modules"] == ["family", "research", "studio"]
    assert [x["source"] for x in view["recent"]][:2] == ["capture", "upload"] or len(
        view["recent"]
    ) >= 2
    assert view["inbox_dir"].endswith("inbox")
