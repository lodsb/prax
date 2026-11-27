"""A PDF's outline as its text's headings (``prax.parsers.outline``), the
``outline`` reading, and the door asking for it after a layout read."""

from __future__ import annotations

import sqlite3

import pytest

from prax import parsers, store
from prax.capture import pipeline
from prax.parsers import outline
from prax.text import markup

PAGES = [
    "**A Book of Tape**\n\nBy Anna Lind",
    "#### CHAPTER 1\nEchoes\n\nA tape loop repeats.\n\n#### A smaller point\n\nWords.",
    "###### 1.1 The loop\n\nIt fades.\n\n# Running head",
    "Body text with no heading at all.",
    "## Chapter 2 Delay\n\nA delay line waits.",
    "## Feedback Paths\n\nMore.",
]
TOC = [
    (1, "Chapter 1 Echoes", 2),
    (2, "1.1 The loop", 3),
    (2, "1.2 The splice", 4),  # printed as a picture: not on its page
    (1, "Chapter 2 Delay", 5),
    (2, "2.1 Feedback Paths: how they ring", 6),
]


def _text(pages: list[str]) -> str:
    return "".join(f"{p}\n{markup.page_break(n)}" for n, p in enumerate(pages, 1))


def _levels(text: str) -> list[tuple[int, str]]:
    return [(level, words) for _, level, words in markup.headings(text)]


def test_the_outline_sets_the_chapters_and_what_it_lists_not_goes_under_them() -> None:
    got = outline.apply(_text(PAGES), TOC)
    assert _levels(got) == [
        (1, "CHAPTER 1 Echoes"),  # a title over two lines, found as one
        (4, "A smaller point"),  # unlisted and deeper already: kept
        (2, "1.1 The loop"),
        (3, "Running head"),  # unlisted, under the section before it
        (2, "1.2 The splice"),  # not found on its page: written in at its top
        (1, "Chapter 2 Delay"),
        (2, "Feedback Paths"),  # a run of the outline's title
    ]
    assert "Body text with no heading at all." in got
    assert got.count("--- end of page") == len(PAGES)


def test_an_outline_of_another_text_or_of_a_few_bookmarks_changes_nothing() -> None:
    text = _text(PAGES)
    assert outline.apply(text, TOC[:3]) == text  # under MIN_ENTRIES
    elsewhere = [(1, f"Chapter {n} of a book not this one", 1) for n in range(9)]
    assert outline.apply(text, elsewhere) == text  # under MIN_FOUND
    _, found = outline.place(text, TOC)
    assert found == 4


def _pdf_with_outline() -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for words in ("Title page", "Echoes", "The loop", "Body", "Delay", "Feedback"):
        doc.new_page().insert_text((72, 72), words)
    doc.set_toc([[lvl, title, page] for lvl, title, page in TOC])
    data: bytes = doc.tobytes()
    return data


def test_the_reading_sets_the_headings_of_the_text_it_is_given() -> None:
    data = _pdf_with_outline()
    reader = parsers.by_name("outline")
    assert reader.explicit_only and reader.annotates
    got = reader(data, previous=_text(PAGES))
    assert (1, "Chapter 2 Delay") in _levels(got)
    with pytest.raises(parsers.ExtractionError, match="parse the document first"):
        reader(data, previous="")


def test_the_door_asks_for_it_after_a_layout_read_of_a_pdf_with_an_outline(
    con: sqlite3.Connection,
) -> None:
    data = _pdf_with_outline()
    doc = store.register(con, data, mime="application/pdf", title="Tape")["doc_id"]
    store.index_text(con, doc, _text(PAGES), text_source="pymupdf4llm/2")
    pipeline.follow_ups(con, doc, stamp="pymupdf4llm/2", action="created")
    assert "outline" in {r["extractor"] for r in store.pending_readings(con, doc)}
    # a LaTeX source sets its own headings: nothing asked
    other = store.register(con, data + b" ", mime="application/pdf", title="T")[
        "doc_id"
    ]
    store.index_text(con, other, _text(PAGES), text_source="latex-source/1")
    pipeline.follow_ups(con, other, stamp="latex-source/1", action="created")
    assert "outline" not in {r["extractor"] for r in store.pending_readings(con, other)}
