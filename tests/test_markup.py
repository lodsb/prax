"""The format prax writes is the format prax reads.

Nothing checked that before: the page mark was written in four places and
matched by three separate copies of one pattern, so a change to either
side could pass every test in the tree.
"""

from __future__ import annotations

import re
from pathlib import Path

from prax import chunking, markup, titles
from prax.parsers import figures, formulas
from prax.store import pages


def test_the_page_mark_a_parser_writes_is_the_one_a_reader_matches() -> None:
    line = markup.page_mark(7)
    m = markup.PAGE_MARK.match(line)
    assert m and m.group(1) == "7"
    # and with the blank lines a parser puts around it
    inside = markup.page_break(12).strip()
    m2 = markup.PAGE_MARK.match(inside)
    assert m2 and m2.group(1) == "12"


def test_a_figure_line_round_trips() -> None:
    ref = "a" * 64
    line = markup.figure_ref("Figure 3: a spectrogram", ref)
    m = markup.FIGURE_REF.match(line)
    assert m and m.group("ref") == ref
    assert m.group("alt") == "Figure 3: a spectrogram"


def test_an_inlined_picture_round_trips() -> None:
    line = markup.data_image("Figure 1", "image/png", "AAAA")
    m = markup.DATA_IMAGE.match(line)
    assert m and m.group("alt") == "Figure 1"
    assert m.group("url").startswith("data:image/png;base64,")


def test_a_reading_round_trips_and_says_what_it_read() -> None:
    line = markup.read_by("Figure", "qwen", "two sine waves")
    m = markup.READ_BY.match(line)
    assert m and m.group("kind") == "Figure"
    assert m.group("model") == "qwen"
    assert m.group("text") == "two sine waves"
    # the head alone, which is how a parser writes it before the text
    head = markup.read_by("Formula", "qwen")
    m2 = markup.READ_BY.match(head)
    assert m2 and m2.group("kind") == "Formula" and m2.group("text") == ""


def test_a_reading_can_be_matched_for_one_kind_only() -> None:
    figure = markup.read_by("Figure", "qwen", "a plot")
    assert markup.read_by_pattern("Figure").match(figure)
    assert not markup.read_by_pattern("Formula").match(figure)


def test_a_heading_round_trips() -> None:
    m = markup.HEADING.match(markup.heading(3, "Results"))
    assert m and m.group("hashes") == "###" and m.group("text") == "Results"
    for section in (markup.FIGURES_HEADING, markup.COMMENTS_HEADING):
        assert markup.HEADING.match(section)


def test_a_formula_round_trips() -> None:
    m = markup.FORMULA.match(markup.formula(r"x = \frac{a}{b}, \quad (4)"))
    assert m
    assert markup.EQ_NUMBER.search(m.group("latex")).group(1) == "4"


def test_a_document_link_round_trips() -> None:
    m = markup.DOC_LINK.search(markup.doc_link("A Paper", 12))
    assert m and m.group(1) == "12"


# ------------------------------------ the copies the modules used to hold


def test_every_module_reads_the_same_page_mark() -> None:
    """Three identical copies lived in chunking and figures, one file
    holding two of them."""
    assert chunking._PAGE_MARK is markup.PAGE_MARK
    assert figures._PAGE_MARK is markup.PAGE_MARK
    assert titles.PAGE_MARK is markup.PAGE_MARK_ANY


def test_every_module_reads_the_same_figure_reference() -> None:
    assert chunking._FIGURE_REF is markup.FIGURE_REF
    assert figures.REF is markup.FIGURE_REF
    assert figures.DATA_IMAGE is markup.DATA_IMAGE


def test_the_readings_come_from_one_pattern() -> None:
    assert chunking._READ_BY is markup.READ_BY
    for line, kind in (
        (markup.read_by("Figure", "m", "x"), figures.READ_BY),
        (markup.read_by("Formula", "m", "x"), formulas.READ_BY),
    ):
        assert kind.match(line)


def test_the_section_headings_are_named_once() -> None:
    assert figures.FIGURES_HEADING is markup.FIGURES_HEADING
    assert pages._DOC_LINK is markup.DOC_LINK


def test_the_heading_and_table_patterns_are_shared() -> None:
    assert chunking._HEADING is markup.HEADING
    assert chunking._TABLE_SEP is markup.TABLE_SEP
    assert chunking._FORMULA is markup.FORMULA
    assert chunking._EQ_NUMBER is markup.EQ_NUMBER


def test_markup_imports_nothing_of_prax() -> None:
    """It is the bottom of the stack: a parser, the chunker, the store and
    the UI all read it, so it can depend on none of them."""
    text = Path(markup.__file__ or "").read_text(encoding="utf-8")
    assert not re.search(r"^from prax|^import prax|^from \.", text, re.MULTILINE)
