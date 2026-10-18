"""The UI's files: the page names each file with its version, a versioned
file is cached for good, and the door's answers are compressed."""

from __future__ import annotations

import gzip
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prax.api import app
from prax.api.ui import UIFiles

INDEX = """<!doctype html><html><head>
<link rel="stylesheet" href="style.css">
<link rel="icon" href="favicon.svg">
<script src="a.js"></script>
<script src="missing.js"></script>
<a href="#search">search</a> <a href="https://example.org/x.js">out</a>
</head></html>"""
CSS = """@font-face { src: url("fonts/f.woff2") format("woff2"); }
.x { mask: url("data:image/svg+xml,%3Csvg%3E"); background: url(img/b.png); }"""


@pytest.fixture()
def ui(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    root = tmp_path / "ui"
    (root / "fonts").mkdir(parents=True)
    (root / "img").mkdir()
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "style.css").write_text(CSS, encoding="utf-8")
    (root / "a.js").write_bytes(b"var a = 1;\n")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / "fonts" / "f.woff2").write_bytes(b"wOF2")
    (root / "img" / "b.png").write_bytes(b"\x89PNG")
    small = FastAPI()
    small.mount("/ui", UIFiles(directory=root, html=True), name="ui")
    with TestClient(small) as c:
        yield c, root


def _version(text: str, name: str) -> str:
    m = re.search(rf"{re.escape(name)}\?v=([0-9a-f]+)", text)
    assert m, f"{name} carries no version in {text!r}"
    return m.group(1)


def test_the_page_names_each_file_with_its_version(ui: tuple[TestClient, Path]) -> None:
    c, root = ui
    page = c.get("/ui/")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"
    assert "content-security-policy" in page.headers
    v = _version(page.text, "a.js")
    _version(page.text, "style.css")
    _version(page.text, "favicon.svg")
    # what is not a file of the UI is left as written
    assert 'src="missing.js"' in page.text and 'href="#search"' in page.text
    assert 'href="https://example.org/x.js"' in page.text
    # a change to the file is a new version, and the page says so
    (root / "a.js").write_bytes(b"var a = 2;\n")
    assert _version(c.get("/ui/index.html").text, "a.js") != v


def test_a_versioned_file_is_cached_for_good_and_a_bare_one_revalidated(
    ui: tuple[TestClient, Path],
) -> None:
    c, _ = ui
    v = _version(c.get("/ui/").text, "a.js")
    kept = c.get(f"/ui/a.js?v={v}")
    assert kept.status_code == 200 and kept.text == "var a = 1;\n"
    assert "immutable" in kept.headers["cache-control"]
    assert "max-age=31536000" in kept.headers["cache-control"]
    assert c.get("/ui/a.js").headers["cache-control"] == "no-cache"


def test_a_stylesheet_names_its_fonts_and_pictures_with_their_versions(
    ui: tuple[TestClient, Path],
) -> None:
    c, _ = ui
    v = _version(c.get("/ui/").text, "style.css")
    css = c.get(f"/ui/style.css?v={v}")
    assert css.headers["content-type"].startswith("text/css")
    assert "immutable" in css.headers["cache-control"]
    _version(css.text, "fonts/f.woff2")
    _version(css.text, "img/b.png")
    assert 'url("data:image/svg+xml,%3Csvg%3E")' in css.text
    font = c.get(f"/ui/fonts/f.woff2?v={_version(css.text, 'fonts/f.woff2')}")
    assert font.content == b"wOF2" and "immutable" in font.headers["cache-control"]


def test_the_page_answers_not_modified_to_its_own_etag(
    ui: tuple[TestClient, Path],
) -> None:
    c, _ = ui
    first = c.get("/ui/")
    again = c.get("/ui/", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and not again.content


def test_the_real_page_versions_every_script_and_stylesheet() -> None:
    with TestClient(app) as c:
        page = c.get("/ui/").text
    names = re.findall(r'(?:src|href)="([^"#:]+)"', page)
    local = [n for n in names if n.split("?")[0].endswith((".js", ".css", ".svg"))]
    assert len(local) >= 20
    assert local and all("?v=" in n for n in local), local


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _raw(c: TestClient, path: str, **kw: object) -> tuple[dict[str, str], bytes]:
    """The headers and the bytes as sent, before the client decodes them."""
    with c.stream("GET", path, headers={"Accept-Encoding": "gzip"}, **kw) as r:  # type: ignore[arg-type]
        return dict(r.headers), b"".join(r.iter_raw())


def test_the_doors_answers_are_compressed(client: TestClient) -> None:
    for i in range(30):
        client.post(
            "/ingest", json={"text": f"granular clouds {i} " * 20, "title": f"note {i}"}
        )
    headers, body = _raw(client, "/documents", params={"limit": 30})
    assert headers.get("content-encoding") == "gzip"
    assert gzip.decompress(body).startswith(b"{")
    headers, _ = _raw(client, "/ui/core.js")
    assert headers.get("content-encoding") == "gzip"


def test_an_original_is_sent_as_it_is(client: TestClient) -> None:
    pdf = client.post(
        "/ingest/file",
        files={"file": ("p.pdf", b"%PDF-1.4 " + b"x" * 4000, "application/pdf")},
    ).json()
    headers, body = _raw(client, f"/doc/{pdf['doc_id']}/original")
    assert "content-encoding" not in headers and body.startswith(b"%PDF-1.4")
