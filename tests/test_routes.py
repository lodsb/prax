"""The routes from a document (``prax.routes``): what has been done to
it and what can be asked for, the extraction request, and the door's
endpoints behind the "process…" dialog."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import extraction, models, ontology, routes, store

FIG_A = "a" * 64
FIG_B = "b" * 64
PDF_TEXT = (
    "# A paper\n\nProse that runs on for a while about diodes.\n\n"
    f"![Figure 1. The circuit](figure:{FIG_A})\n"
    "*Figure, as read by qwen@host:* A diode across a resistor.\n\n"
    "More prose between the figures.\n\n"
    f"![Figure 2. The response](figure:{FIG_B})\n\n"
    f"![Figure on page 3](figure:{'c' * 64})\n\n"
    "The diode law is\n\n"
    "$$i = I_s (e^{v/V_T} - 1) \\quad (1)$$\n\n"
    "and the rest of the paper follows.\n"
)


def _pdf(con: sqlite3.Connection, text: str = PDF_TEXT, **meta: object) -> int:
    doc = store.register(
        con,
        b"%PDF-1.4 fake",
        mime="application/pdf",
        title="Paper",
        meta={"pages": 12, **meta},
    )["doc_id"]
    store.index_text(con, doc, text, text_source="pymupdf4llm/3")
    return doc


def _by_id(view: dict) -> dict[str, dict]:
    return {r["id"]: r for r in view["routes"]}


def test_routes_of_a_pdf(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_VISION", "stub")
    monkeypatch.setenv("PRAX_EXTRACT", "stub")
    monkeypatch.setenv("PRAX_FORMULAS", "none")
    models.reset()
    doc = _pdf(con)
    view = routes.routes_for(con, doc)
    state = view["state"]
    assert state["mime"] == "application/pdf"
    assert state["text_source"] == "pymupdf4llm/3"
    assert (state["figures"], state["figures_read"]) == (3, 1)
    assert state["figures_uncaptioned"] == 1
    assert state["figures_uncaptioned_read"] == 0
    assert (state["formulas"], state["formulas_read"]) == (1, 0)
    assert state["extraction"] is None and state["promote"] is None
    assert state["models"]["vision"] == {"name": "stub", "paid": False}
    assert state["models"]["formulas"] is None
    by = _by_id(view)
    groups = {r["group"] for r in view["routes"]}
    assert groups == {"text", "figures", "formulas", "graph"}
    # the text routes a PDF has
    assert by["ocr"]["action"] == {
        "kind": "reading",
        "extractor": "pymupdf4llm-ocr",
        "mode": None,
    }
    assert by["vision-pages"]["action"]["mode"] == "scans"
    assert by["marker"]["action"]["mode"] == "fast"
    assert {"reread", "docling", "vision-pages-all"} <= set(by)
    # the figures: nobody-has-read, every-one-again, uncaptioned too
    assert "1 of 2 captioned figures" in by["figures"]["detail"]
    assert by["figures"]["available"] is True
    assert by["figures-again"]["action"] == {
        "kind": "reading",
        "extractor": "figures",
        "mode": "again",
    }
    assert by["figures-all"]["action"]["mode"] == "all"
    assert by["figures-all"]["available"] is True
    assert by["figures-all"]["detail"].startswith("1 of 1 unread")
    assert "the 2 captioned figures" in by["figures-again"]["detail"]
    assert by["figures"]["model"] == "stub" and by["figures"]["paid"] is False
    # the formulas step is off on this host: the route says so
    assert by["formulas"]["available"] is False
    assert "no model for the formulas step" in by["formulas"]["note"]
    assert by["formulas-again"]["available"] is False
    # the graph
    assert by["extract"]["label"] == "Extract the graph"
    assert by["extract"]["action"] == {"kind": "extract"}
    assert by["promote"]["action"] == {"kind": "promote"}
    assert by["promote"]["paid"] is True  # the default promote model is Claude
    assert not any(r["pending"] for r in view["routes"])


def test_a_scan_is_named_and_a_request_shows_as_pending(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_VISION", "stub")
    models.reset()
    # a scan read as its cover: twelve pages, a few words
    doc = _pdf(con, "cover words only\n")
    view = routes.routes_for(con, doc)
    assert view["state"]["thin"] is True
    by = _by_id(view)
    assert "looks like a scan" in by["ocr"]["detail"]
    assert by["figures"]["available"] is False  # no figure references yet
    assert "read the document again first" in by["figures"]["detail"]
    assert by["figures-all"]["available"] is False
    assert "formulas-again" not in by
    store.request_reading(con, doc, "vision-pages", mode="scans")
    by = _by_id(routes.routes_for(con, doc))
    assert by["vision-pages"]["pending"] is True
    assert by["vision-pages-all"]["pending"] is False  # another mode
    assert by["ocr"]["pending"] is False
    store.request_reading(con, doc, "pymupdf4llm-ocr", mode="latin")
    by = _by_id(routes.routes_for(con, doc))
    assert by["ocr"]["pending"] is True  # any language is that route
    assert by["vision-pages"]["pending"] is False  # a request replaces the last
    store.promote(con, doc)
    by = _by_id(routes.routes_for(con, doc))
    assert by["promote"]["pending"] is True


def test_routes_of_other_kinds(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_VISION", "stub")
    monkeypatch.setenv("PRAX_POLISH", "stub")
    models.reset()
    page = store.write_page(con, "notes", "# Notes\n\nSome notes.\n")["doc_id"]
    assert routes.routes_for(con, page)["routes"] == []
    html = store.register(
        con, b"<html><body>hi</body></html>", mime="text/html", title="Page"
    )["doc_id"]
    store.index_text(con, html, "hi " * 50, text_source="trafilatura/2")
    by = _by_id(routes.routes_for(con, html))
    assert by["reread"]["action"]["extractor"] == "trafilatura"
    assert "polish" not in by and "ocr" not in by and "figures-all" not in by
    assert {"figures", "figures-again", "formulas", "extract", "promote"} <= set(by)
    store.set_meta(con, html, {"video": {"id": "x"}})
    assert "polish" in _by_id(routes.routes_for(con, html))
    img = store.register(con, b"\x89PNG fake", mime="image/png", title="Pic")["doc_id"]
    store.index_text(con, img, "a picture " * 20, text_source="vision/1")
    by = _by_id(routes.routes_for(con, img))
    assert by["vision"]["action"]["extractor"] == "vision"
    assert "figures" not in by and "formulas" not in by
    with pytest.raises(KeyError):
        routes.routes_for(con, 999)


def test_request_extraction_goes_first_whatever_the_scope(
    con: sqlite3.Connection,
) -> None:
    """A person asks for the graph to be read again: the stamp goes to
    the history, the document is first in the queue and in the captures
    scope too, the next reading retires this producer's old edges and
    clears the request."""
    onto = ontology.current()
    later = store.ingest_text(
        con, "later " * 300, title="Later", meta={"source": "capture"}
    )["doc_id"]
    doc = store.ingest_text(con, "f " * 300, title="F", meta={"source": "zotero"})[
        "doc_id"
    ]
    triple = extraction.Triple(
        "F", "paper", "about", "Diodes", "concept", "EXTRACTED", "F is about diodes"
    )
    extraction.apply(
        con,
        doc,
        extraction.Extraction(summary="s", triples=[triple]),
        extractor="stub",
        run="r1",
    )
    assert store.select_for_extraction(
        con, ontology_version=onto.version, onto=onto, sources=("capture",)
    ) == [later]
    stale = store.request_extraction(con, doc, by="tester")
    assert stale["extractor"] == "stub" and stale["requested"]["by"] == "tester"
    meta = store.get_meta(con, doc)
    assert "extraction" not in meta
    assert meta["extraction_history"][-1]["superseded_by"] == "request"
    assert store.select_for_extraction(
        con, ontology_version=onto.version, onto=onto
    ) == [doc, later]
    assert store.select_for_extraction(
        con, ontology_version=onto.version, onto=onto, sources=("capture",)
    ) == [doc, later]
    assert _by_id(routes.routes_for(con, doc))["extract"]["pending"] is True
    extraction.apply(
        con, doc, extraction.Extraction(summary="t"), extractor="stub", run="r2"
    )
    meta = store.get_meta(con, doc)
    assert "extraction_stale" not in meta
    live = con.execute(
        "SELECT count(*) FROM edges WHERE source_doc = ? AND valid_to IS NULL", (doc,)
    ).fetchone()[0]
    kept = con.execute(
        "SELECT count(*) FROM edges WHERE source_doc = ?", (doc,)
    ).fetchone()[0]
    assert (live, kept) == (0, 1)
    assert store.select_for_extraction(
        con, ontology_version=onto.version, onto=onto, sources=("capture",)
    ) == [later]
    # a document never extracted can be asked for too
    stale = store.request_extraction(con, later)
    assert "extractor" not in stale and stale["requested"]["by"] == "human"
    with pytest.raises(KeyError):
        store.request_extraction(con, 999)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_VISION", "stub")
    monkeypatch.setenv("PRAX_EXTRACT", "stub")
    models.reset()
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_api_routes_and_extract(client: TestClient) -> None:
    con = client.app.state.con
    doc = _pdf(con)
    r = client.get(f"/doc/{doc}/routes")
    assert r.status_code == 200
    view = r.json()
    assert view["doc_id"] == doc and view["state"]["figures"] == 3
    by = _by_id(view)
    assert by["extract"]["pending"] is False
    assert client.get("/doc/999/routes").status_code == 404
    r = client.post(f"/doc/{doc}/extract", json={})
    assert r.status_code == 200 and r.json()["requested"]["by"] == "human"
    assert _by_id(client.get(f"/doc/{doc}/routes").json())["extract"]["pending"]
    assert client.post("/doc/999/extract", json={}).status_code == 404
    bare = store.register(con, b"%PDF-1.4 bare", mime="application/pdf", title="B")[
        "doc_id"
    ]
    assert client.post(f"/doc/{bare}/extract", json={}).status_code == 400
    # a reading request through the door shows in the routes
    client.post(f"/doc/{doc}/reading", json={"extractor": "figures", "mode": "again"})
    by = _by_id(client.get(f"/doc/{doc}/routes").json())
    assert by["figures-again"]["pending"] is True
    assert by["figures"]["pending"] is False
    assert view["state"]["models"]["extract"] == {"name": "stub", "paid": False}
