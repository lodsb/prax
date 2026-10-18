"""What a text says is wrong with it (stage AO, ``prax.parsers.quality``):
each check on a text that has the fault and on one that does not."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from prax import store
from prax.parsers import quality

LAYER = "pymupdf4llm/1.28.2-r2"
PDF = "application/pdf"
TYPED = {"pages": 10, "scanned": 0.0, "ocr_layer": 0.0, "layer": True, "maths": 6}

GARBLED = """# Results

The error of the filter is bounded as follows.

e ( n ) = x ( n ) − y ( n )
∑ k = 0 N h ( k )
| H ( ω ) | ≤ 1 + ε

Further prose that explains what the bound means for the design of a filter.
"""


def _checks(found: list[quality.Finding]) -> dict[str, quality.Finding]:
    return {f.check: f for f in found}


def test_a_scans_ocr_layer_read_as_its_text_is_found() -> None:
    scan = {"pages": 8, "scanned": 1.0, "ocr_layer": 1.0, "layer": True, "maths": 0}
    got = _checks(quality.check("Words. " * 300, stamp=LAYER, mime=PDF, profile=scan))
    assert (
        got["ocr-layer"].fix == quality.Fix("marker", ocr=True)
        and got["ocr-layer"].count == 8
    )
    # marker's own reading of every page is what it should be
    again = quality.check(
        "Words. " * 300, stamp="marker/2.0.0+fast-ocr", mime=PDF, profile=scan
    )
    assert "ocr-layer" not in _checks(again)


def test_maths_as_loose_symbols_is_found_and_names_its_reader() -> None:
    got = _checks(quality.check(GARBLED, stamp=LAYER, mime=PDF, profile=TYPED))
    f = got["maths-as-text"]
    assert f.count == 3 and f.fix == quality.Fix("marker")
    assert f.evidence[0] == "e ( n ) = x ( n ) − y ( n )"
    on_arxiv = quality.check(
        GARBLED, stamp=LAYER, mime=PDF, profile={**TYPED, "arxiv": "2501.13484"}
    )
    assert _checks(on_arxiv)["maths-as-text"].fix == quality.Fix(
        "latex-source", "2501.13484"
    )
    # a reader that writes LaTeX, or a paper with no maths font, is not flagged
    assert "maths-as-text" not in _checks(
        quality.check(GARBLED, stamp="marker/2.0.0", mime=PDF, profile=TYPED)
    )
    assert "maths-as-text" not in _checks(
        quality.check(GARBLED, stamp=LAYER, mime=PDF, profile={**TYPED, "maths": 0})
    )
    # nor are a table's cells or ordinary prose, nor the library's false
    # alarms of 2026-10-18: a manual's code in backticks, a footnote's
    # address, a page counter, a bold label in a list
    prose = "| a | b |\n| 1 | 2 |\n" + "A sentence of ordinary words in a row. " * 40
    alarms = (
        # the most found once: code with its backticks split by emphasis
        "`\\xetex_if_engine:TF {` _(_ _`true code` )_ `} {` _(_ _`false code` )_ `}`\n"
        "`entity` _(_ _`entityId` )_ `is` [ _(_ _`generic declaration` )_ ] = [\n"
        "_(_ s _)_ ::= **`skip`** | _(_ s _)_ 1 _(_ s _)_ 2 = x\n"
        "- 2 `\\def\\raggedright{%`\n"
        "- 3 `\\let\\\\=\\@centercr`\n"
        "> 1 `http://gephi.org`\n"
        "> 2 `https://commoncrawl.org/the-data/`\n"
        "1 / 28\n2 / 28\n3 / 28\n"
        "- **Beispiel** :\n- **Beispiele** :\n- **Beispiel** :\n"
    )
    for text in (prose, alarms):
        assert "maths-as-text" not in _checks(
            quality.check(text, stamp=LAYER, mime=PDF, profile=TYPED)
        ), text[:40]
    # and garbled maths set in emphasis, as pymupdf4llm writes it, is found
    emphasised = (
        "Also: Amortisierte Kosten _≤ t_ + ∆ _bal ≤_ 2 _i −_ 1.\n"
        "_t ≤ i_ + _k_ ; _⇒ k ≥ t − i_\n"
        "Kante ( _x, y_ ) heißt leicht, falls 2 _· w_ ( _y_ ) _≤ w_ ( _x_ )\n"
    )
    found = _checks(quality.check(emphasised, stamp=LAYER, mime=PDF, profile=TYPED))
    assert found["maths-as-text"].count == 3


def test_a_caption_with_no_picture_asks_for_the_crops_once() -> None:
    text = "Prose.\n\nFigure 1: The rig.\n\nMore prose.\n"
    got = _checks(
        quality.check(text, stamp="latex-source/3.9-r2", mime=PDF, profile=TYPED)
    )
    assert (
        got["bare-figures"].fix == quality.Fix("figure-crops")
        and got["bare-figures"].count == 1
    )
    # the crops leave text_source as it was: the history says they ran
    history = [
        {"extractor": "pymupdf4llm/1.28.2-r2", "outcome": "created"},
        # on an earlier text: says nothing
        {"extractor": "figure-crops/1", "outcome": "upgraded"},
        {"extractor": "latex-source/3.9-r2", "outcome": "upgraded"},
        {"extractor": "formulas/1+q", "outcome": "upgraded"},
        {"extractor": "figure-crops/1", "outcome": "same"},
    ]
    since = quality.since_text(history, "latex-source/3.9-r2")
    assert since == {"formulas", "figure-crops"}
    assert quality.since_text(history[:3], "latex-source/3.9-r2") == set()
    # a re-read of the same text moves the stamp on and ends nothing; a
    # failed attempt ran on nothing; a history without the text, nothing
    again = [
        *history,
        {"extractor": "latex-source/3.9-r3", "outcome": "same"},
        {"extractor": "marker/1.10", "error": "the card is busy"},
    ]
    assert quality.since_text(again, "latex-source/3.9-r3") == {
        "formulas",
        "figure-crops",
        "latex-source",
    }
    assert quality.since_text(history[:2], "latex-source/3.9-r2") == set()
    after = _checks(
        quality.check(
            text, stamp="latex-source/3.9-r2", mime=PDF, profile=TYPED, since=since
        )
    )
    assert after["bare-figures"].fix is None  # the crops ran: only a flag
    pictured = "Prose.\n\n![Figure 1: The rig.](figure:" + "a" * 64 + ")\n"
    assert "bare-figures" not in _checks(
        quality.check(pictured, stamp=LAYER, mime=PDF, profile=TYPED)
    )


def test_a_text_that_lost_most_of_the_pdf_is_short() -> None:
    # the layer's characters, whitespace left out, over every page
    prof = {**TYPED, "layer_chars": 10 * 2000, "sampled": 10, "pages": 10}
    got = _checks(
        quality.check("A few words. " * 100, stamp=LAYER, mime=PDF, profile=prof)
    )
    assert got["short-text"].detail["share"] < 0.5
    whole = quality.check("A few words. " * 2000, stamp=LAYER, mime=PDF, profile=prof)
    assert "short-text" not in _checks(whole)
    # a book the profile read the first pages of says nothing of the rest
    book = {**prof, "pages": 114, "sampled": 12}
    assert "short-text" not in _checks(
        quality.check("A few words. " * 100, stamp=LAYER, mime=PDF, profile=book)
    )


def test_what_a_reader_left_that_is_not_text() -> None:
    text = "the map MATH ${\\cal A}$ ${\\cal A}$ at *f**s*=6&nbsp;kHz<br>"
    f = _checks(quality.check(text, stamp="latex2html/1", mime="text/html"))[
        "leftovers"
    ]
    assert f.count == 3 and f.fix is None
    assert set(f.detail["kinds"]) == {
        "a latex2html comment",
        "a subscript as emphasis",
        "an HTML entity",
    }
    clean = "the map ${\\cal A}$ at $f_{s}$=6 kHz"
    assert quality.check(clean, stamp="latex2html/2", mime="text/html") == []
    # the HTML pymupdf4llm writes on purpose is its format, not a leftover
    table = (
        "| a | b<br>c |\n|---|---|\n| 1 | 2<br>3 |\n"
        "the NUI<sup>1</sup> at 10<sup>-6</sup>\n"
    )
    assert quality.check(table, stamp=LAYER, mime="text/html") == []


def test_a_finding_names_its_fix_and_a_pdf_its_arxiv_id() -> None:
    import pymupdf

    found = quality.check(GARBLED, stamp=LAYER, mime=PDF, profile=TYPED)
    assert set(quality.fixes(found)) == {"marker"}
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "arXiv:1308.3432v2 [cs.LG] 9 Aug 2013")
    data = doc.tobytes()
    assert quality.profile_of(data, mime=PDF)["arxiv"] == "1308.3432"
    assert (
        quality.profile_of(data, mime=PDF, arxiv="2501.13484")["arxiv"] == "2501.13484"
    )
    assert quality.profile_of(data, mime=PDF, arxiv="no id")["arxiv"] == "1308.3432"
    assert quality.profile_of(data, mime="text/html") is None


def test_the_door_keeps_the_findings_with_the_text_they_read(
    con: sqlite3.Connection,
) -> None:
    doc = store.ingest_text(con, "Words of a page. " * 30, title="t")["doc_id"]
    found = quality.check(GARBLED, stamp=LAYER, mime=PDF, profile=TYPED)
    current = store.get_document(con, doc, max_chars=0)["text_hash"]
    held = store.set_quality(
        con,
        doc,
        [f.as_dict() for f in found],
        version=quality.VERSION,
        text_hash=current,
    )
    meta = store.get_meta(con, doc)
    assert held is not None and meta["quality"] == held
    assert held["text_hash"] == current
    assert held["findings"][0]["check"] == "maths-as-text"
    # the checks of a text the document no longer holds are not kept
    store.index_text(con, doc, "Another text of the page. " * 30)
    kept = store.set_quality(con, doc, [], version=quality.VERSION, text_hash=current)
    assert kept is None
    assert store.get_meta(con, doc)["quality"] == held


def test_the_quality_pass_checks_each_text_once(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(
        con, r"the map MATH ${\cal A}$ ${\cal A}$ here. " * 20, title="t"
    )["doc_id"]
    first = store.maintain(con, only=["quality"])["quality"]
    assert first["documents"] >= 1 and first["found"] == {"leftovers": 1}
    held = store.get_meta(con, doc)["quality"]
    assert held["version"] == quality.VERSION
    assert held["findings"][0]["check"] == "leftovers"
    # the next night reads only what changed
    assert store.maintain(con, only=["quality"])["quality"]["documents"] == 0
    store.index_text(con, doc, "Clean words of a page. " * 20, text_source="x/1")
    again = store.maintain(con, only=["quality"])["quality"]
    assert again["documents"] == 1 and again["found"] == {}
    assert store.get_meta(con, doc)["quality"]["findings"] == []


def test_the_admin_list_and_the_fix_for_its_documents(
    data_dir: Path,
) -> None:
    """``GET /documents/quality`` counts each check's documents and lists
    one check's, the most found first; ``POST /readings/bulk`` with
    ``finding`` asks for the fix over them, and only of the text a
    document holds now."""
    from fastapi.testclient import TestClient

    from prax.api import app

    data_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        con = client.app.state.con
        few = store.ingest_text(con, "a MATH $x$ $x$ b. " * 20, title="few")["doc_id"]
        many = store.ingest_text(con, "c &nbsp; MATH $y$ d. " * 30, title="many")[
            "doc_id"
        ]
        clean = store.ingest_text(con, "Clean words. " * 30, title="clean")["doc_id"]
        store.maintain(con, only=["quality"])
        got = client.get("/documents/quality").json()
        assert got["checks"] == {"leftovers": 2} and "items" not in got
        listed = client.get("/documents/quality?check=leftovers").json()
        assert [i["id"] for i in listed["items"]] == [many, few]
        assert listed["items"][0]["finding"]["count"] == 60
        asked = client.post(
            "/readings/bulk",
            json={"extractor": "pymupdf4llm", "finding": "leftovers", "dry_run": True},
        ).json()
        assert asked["selected"] == 2
        # a new text makes its old findings say nothing
        store.index_text(con, few, "Now clean. " * 30, text_source="x/1")
        assert client.get("/documents/quality").json()["checks"] == {"leftovers": 1}
        assert clean not in {i["id"] for i in listed["items"]}
