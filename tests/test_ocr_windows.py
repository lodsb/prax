"""Stage Y, OCR in windows: a scan longer than ``parse.ocr_max_pages`` is
read a window of pages a pass, the pages read before kept, and the door
asks for the next window while pages wait. PDF and DjVu alike (DjVu's own
reading is in ``test_djvu.py``)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import parsers, store
from prax.text import markup, mimes


def test_pages_are_found_by_their_marks_and_joined_back() -> None:
    text = f"one{markup.page_break(1)}{markup.page_break(2)}three{markup.page_break(3)}"
    pages = parsers.pages_by_mark(text)
    assert pages == {1: "one", 2: "", 3: "three"}
    again = parsers.join_pages([pages[1], pages[2], pages[3]])
    assert parsers.pages_by_mark(again) == pages
    assert parsers.empty_pages(["a page of text long enough here", "", " x "]) == [1, 2]
    assert parsers.pages_by_mark("no marks at all") == {1: "no marks at all"}
    assert parsers.pages_by_mark("") == {}
    part = parsers.Partial("text", pages_left=3, pages=5)
    assert part == "text" and (part.pages_left, part.pages) == (3, 5)
    assert not hasattr(part.strip(), "pages_left")  # what the worker reads first


def _pdf(pages: list[str]) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    return doc.tobytes()


def test_a_scanned_pdf_is_read_in_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from prax.parsers import pdf

    monkeypatch.setenv("PRAX_OCR_MAX_PAGES", "2")
    monkeypatch.setattr(pdf, "_ocr_language", lambda: "arabic")  # the page-by-page path
    monkeypatch.setattr(pdf, "_ocr_engine", lambda: object())
    read: list[int] = []

    def page(p: Any, engine: Any, *, right_to_left: bool) -> str:
        read.append(p.number + 1)
        return f"the words the recognizer found on page {p.number + 1}"

    monkeypatch.setattr(pdf, "_ocr_page", page)
    data = _pdf(["A title page with a text layer of its own", "", "", ""])
    first = parsers.by_name("pymupdf4llm-ocr")(data)
    assert isinstance(first, parsers.Partial)
    assert (first.pages_left, first.pages) == (1, 4) and read == [2, 3]
    assert "text layer of its own" in first  # a page with text is not read
    last = parsers.by_name("pymupdf4llm-ocr")(data, previous=str(first))
    assert not isinstance(last, parsers.Partial) and read == [2, 3, 4]
    pages = parsers.pages_by_mark(last)
    assert pages[2] == "the words the recognizer found on page 2"  # kept
    assert pages[4] == "the words the recognizer found on page 4"


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_a_window_through_the_door_asks_for_the_next(client: TestClient) -> None:
    """A worker's window comes back with the pages still to go; the door
    keeps the text, says how far it is, and hands the document out again
    with the text so far."""
    con = client.app.state.con
    doc = int(
        store.register(con, b"AT&TFORM a scan", mime=mimes.DJVU, title="A scan")[
            "doc_id"
        ]
    )
    text = parsers.join_pages(
        ["the words OCR found on page one of five", "", "", "", ""]
    )
    got = client.post(
        "/work/parse",
        json={
            "results": [
                {
                    "doc_id": doc,
                    "extractor": "djvu/3.5.29",
                    "text": text,
                    "pages_left": 4,
                    "pages_total": 5,
                }
            ]
        },
    )
    assert got.status_code == 200 and got.json()["applied"] == 1
    assert store.get_meta(con, doc)["ocr"]["left"] == 4
    items = client.get("/work/parse").json()["items"]
    mine = [it for it in items if it["doc_id"] == doc]
    assert mine and mine[0]["extractor"] == "djvu"
    assert "page one of five" in (mine[0].get("previous") or "")
    # the last window: none left, and nothing asked for again
    client.post(
        "/work/parse",
        json={
            "results": [
                {
                    "doc_id": doc,
                    "extractor": "djvu/3.5.29",
                    "text": text,
                    "requested": "djvu",
                    "force": True,
                }
            ]
        },
    )
    assert store.get_meta(con, doc)["ocr"]["left"] == 0
    assert not [
        r for r in store.reading_requests(con, limit=None) if r["doc_id"] == doc
    ]
