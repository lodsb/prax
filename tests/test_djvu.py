"""DjVu documents: an image type that is a document (``prax.text.mimes``), read
by its own parser (``parsers._djvu``, DjVuLibre's ``djvutxt``)."""

from __future__ import annotations

import sqlite3
import subprocess
from typing import Any

import pytest

from prax import parsers, store
from prax.text import markup, mimes


def test_a_djvu_file_is_a_document_not_a_picture() -> None:
    assert parsers.guess_mime("Book.djvu") == mimes.DJVU
    assert parsers.guess_mime("Book.DJV") == mimes.DJVU
    assert not mimes.is_picture(mimes.DJVU)
    assert mimes.is_picture("image/png") and not mimes.is_picture("application/pdf")
    assert "NOT IN ('image/vnd.djvu')" in mimes.picture_sql("d.mime")


def _tools(monkeypatch: pytest.MonkeyPatch, *, ddjvu: bool = False) -> None:
    have = {"djvutxt": "djvutxt", "djvused": "djvused"}
    if ddjvu:
        have["ddjvu"] = "ddjvu"
    monkeypatch.setattr(parsers.djvu, "djvu_tool", lambda name: have.get(name))


def _djvutxt(monkeypatch: pytest.MonkeyPatch, pages: list[str]) -> list[list[str]]:
    """djvutxt answering with these pages, separated by form feeds (and
    ending with one, as it does), and djvused counting them."""
    calls: list[list[str]] = []

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(list(argv))
        if argv[0] == "djvused":
            return subprocess.CompletedProcess(argv, 0, f"{len(pages)}\n".encode(), b"")
        body = "\f".join(pages) + "\f"
        return subprocess.CompletedProcess(argv, 0, body.encode(), b"")

    monkeypatch.setattr(parsers.djvu.subprocess, "run", run)
    return calls


def test_the_text_layer_page_by_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _tools(monkeypatch)
    _djvutxt(monkeypatch, ["Chapter one begins here and runs on.", "Page two text.\n"])
    text = parsers._djvu(b"AT&TFORM")
    assert text.index("Chapter one") < text.index(markup.page_mark(1))
    assert text.index(markup.page_mark(1)) < text.index("Page two text.")
    assert text.rstrip().endswith(markup.page_mark(2))
    ext = parsers.for_mime(mimes.DJVU)
    assert ext is not None and ext.name == "djvu"


def test_without_djvulibre_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parsers.djvu, "djvu_tool", lambda name: None)
    with pytest.raises(parsers.ExtractionError, match="DjVuLibre is not installed"):
        parsers._djvu(b"AT&TFORM")
    assert parsers.for_mime(mimes.DJVU) is None  # not offered where it cannot run


def test_a_long_scan_is_read_in_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage Y: a scan past the OCR budget was refused, and four DjVu books
    of 190 to 746 pages waited without text (2026-09-28). Now each pass
    reads the next window of pages without text, keeps what the passes
    before read, and says how many pages wait."""
    _tools(monkeypatch, ddjvu=True)
    monkeypatch.setenv("PRAX_OCR_MAX_PAGES", "2")
    _djvutxt(monkeypatch, ["", "", "", "", ""])  # five pages, no text layer
    read: list[int] = []

    def ocr(src: Any, pages: list[str], window: list[int]) -> list[str]:
        out = list(pages)
        for i in window:
            read.append(i + 1)
            out[i] = f"the words OCR found on page {i + 1} of the scan"
        return out

    monkeypatch.setattr(parsers.djvu, "_djvu_ocr", ocr)
    first = parsers._djvu(b"AT&TFORM")
    assert isinstance(first, parsers.Partial)
    assert (first.pages_left, first.pages) == (3, 5) and read == [1, 2]
    assert "page 2 of the scan" in first and "page 3 of the scan" not in first
    second = parsers._djvu(b"AT&TFORM", previous=str(first))
    assert second.pages_left == 1 and read == [1, 2, 3, 4]
    assert "page 1 of the scan" in second  # kept, not read again
    last = parsers._djvu(b"AT&TFORM", previous=str(second))
    assert not isinstance(last, parsers.Partial) and read == [1, 2, 3, 4, 5]
    assert parsers.pages_by_mark(last)[5] == "the words OCR found on page 5 of the scan"


def test_the_door_asks_for_the_next_window_while_pages_wait(
    con: sqlite3.Connection,
) -> None:
    from prax.parsers import queue

    doc = int(
        store.register(con, b"AT&TFORM a scan", mime=mimes.DJVU, title="A scan")[
            "doc_id"
        ]
    )
    store.index_text(con, doc, "the words OCR found on the first pages " * 20)
    got = queue.continue_windows(con, doc, stamp="djvu/3.5.29", pages_left=3, pages=5)
    assert got is not None and got["left"] == 3 and got["extractor"] == "djvu"
    waiting = [r for r in store.reading_requests(con, limit=None) if r["doc_id"] == doc]
    assert [r["extractor"] for r in waiting] == ["djvu"]
    assert waiting[0]["by"] == "ocr-window"
    # the window is done: its reading finishes, and the last one leaves none
    store.finish_reading(con, doc, outcome="created", stamp="djvu/3.5.29")
    done = queue.continue_windows(
        con, doc, stamp="djvu/3.5.29", pages_left=None, pages=None
    )
    assert done is not None and done["left"] == 0 and done["pages"] == 5
    assert not [
        r for r in store.reading_requests(con, limit=None) if r["doc_id"] == doc
    ]
    # a text read whole, of a document never read in windows, says nothing
    other = int(store.register(con, b"AT&TFORM other", mime=mimes.DJVU)["doc_id"])
    assert (
        queue.continue_windows(
            con, other, stamp="djvu/3.5.29", pages_left=None, pages=None
        )
        is None
    )


def test_a_djvu_book_is_extracted_like_any_document(con: sqlite3.Connection) -> None:
    doc = int(
        store.register(con, b"AT&TFORM djvu", mime=mimes.DJVU, title="A book")["doc_id"]
    )
    store.index_text(con, doc, "Wave digital filters, chapter one. " * 40)
    photo = int(
        store.register(con, b"\x89PNG photo", mime="image/png", title="A photo")[
            "doc_id"
        ]
    )
    store.index_text(con, photo, "A caption that is long enough to count. " * 40)
    due = store.select_for_extraction(
        con, ontology_version="x", skip_mime_prefix="image/"
    )
    assert doc in due and photo not in due
    assert "scanned document" in (store.document_field(con, doc) or "")


def test_a_djvu_taken_in_as_unknown_bytes_is_given_its_type(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six DjVu books were registered as application/octet-stream before
    prax named the type; the heal pass gives them their type, and the parse
    queue then offers them to the DjVu parser."""
    _tools(monkeypatch)
    doc = int(
        store.register(
            con,
            b"AT&TFORM a book",
            mime="application/octet-stream",
            title="A book",
            original_path="old/Book.djvu",
        )["doc_id"]
    )
    store.register(
        con, b"\x00zip", mime="application/octet-stream", original_path="x.zip"
    )
    found = store.health(con, only=["untyped-documents"])["ailments"][0]
    assert found["count"] == 1 and found["examples"][0]["mime"] == mimes.DJVU
    store.heal(con, only=["untyped-documents"])
    assert store.get_document(con, doc, max_chars=0)["mime"] == mimes.DJVU
    assert store.health(con, only=["untyped-documents"])["ailments"][0]["count"] == 0
