"""The reader a captured PDF gets by itself (``pipeline.choose_reader``):
its LaTeX source when it is on arXiv, marker reading every page for a
scan, marker for a born-digital paper set in a maths font, nothing for
the rest; and the ``latex-source`` reading the worker does for it."""

from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path
from typing import ClassVar, Self

import pytest

from prax import parsers, store
from prax.capture import pipeline
from prax.importers import latex
from prax.parsers import latexsrc
from prax.parsers import pdf as pdfparts

pymupdf = pytest.importorskip("pymupdf")

LAYER = "pymupdf4llm/1.28.2-r2"


def _scan(pages: int = 2) -> bytes:
    """Pages that are a picture each, with an OCR's invisible text over them."""
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
        pix.clear_with(200 + n)
        page.insert_image(page.rect, pixmap=pix)
        page.insert_text((72, 100), f"err o r signal on page {n}", render_mode=3)
    out = doc.tobytes()
    doc.close()
    return out


def _typed(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    out = doc.tobytes()
    doc.close()
    return out


def _captured(con: store.sqlite3.Connection, data: bytes, **meta: object) -> int:
    doc = store.register(
        con,
        data,
        mime="application/pdf",
        meta={"source": "capture", **meta},
    )["doc_id"]
    store.index_text(con, doc, "# A paper\n\nIts words. " * 20, text_source=LAYER)
    return int(doc)


def _waiting(con: store.sqlite3.Connection, doc: int) -> list[tuple[str, str | None]]:
    return [(r["extractor"], r.get("mode")) for r in store.pending_readings(con, doc)]


def test_a_profile_tells_a_scan_from_a_typed_page() -> None:
    scan = pdfparts.profile(_scan())
    assert scan["scanned"] == 1.0 and scan["ocr_layer"] == 1.0 and scan["layer"]
    typed = pdfparts.profile(_typed("Plain words."))
    assert typed["scanned"] == 0.0 and typed["ocr_layer"] == 0.0 and typed["maths"] == 0
    assert pdfparts.profile(b"not a pdf")["pages"] == 0
    # what a formula is set in, and a word processor's bullets are not
    for name in ("ABCDEF+CMMI10", "CMSY7", "CambriaMath", "STIXTwoMath", "MT-Extra"):
        assert pdfparts._MATH_FONT.search(name), name
    assert not pdfparts._MATH_FONT.search("Symbol")
    assert not pdfparts._MATH_FONT.search("TimesNewRomanPSMT")


def test_a_broken_image_does_not_break_the_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MuPDF raises on an image it cannot load (one of width zero, in the
    library on 2026-10-18), and the door profiles every captured PDF when
    its text lands: the image covers nothing, the page is still read."""

    def broken(self: object, xref: int) -> list[object]:
        raise RuntimeError("code=8: image width is zero (or less)")

    monkeypatch.setattr(pymupdf.Page, "get_image_rects", broken)
    got = pdfparts.profile(_scan())
    assert got["pages"] == 2 and got["sampled"] == 2 and got["scanned"] == 0.0
    assert got["layer"] and got["layer_chars"] > 0


def test_a_captured_pdf_is_given_the_reader_that_suits_it(
    con: store.sqlite3.Connection,
) -> None:
    # on arXiv: its source, named by meta.arxiv
    paper = _captured(con, _typed("A paper on arXiv."), arxiv="2501.13484")
    assert (
        pipeline.follow_ups(con, paper, stamp=LAYER, action="created") == "latex-source"
    )
    assert _waiting(con, paper) == [("latex-source", "2501.13484")]
    # or by the stamp arXiv prints on the PDF's first page
    stamped = _captured(con, _typed("arXiv:1308.3432v2 [cs.LG] 9 Aug 2013"))
    pipeline.follow_ups(con, stamped, stamp=LAYER, action="created")
    assert _waiting(con, stamped) == [("latex-source", "1308.3432")]
    # a scan: marker reading every page itself
    scan = _captured(con, _scan())
    assert pipeline.follow_ups(con, scan, stamp=LAYER, action="created") == "marker"
    assert _waiting(con, scan) == [("marker", "fast-ocr")]
    # through what the checks found, which the door keeps with the text
    held = store.get_meta(con, scan)["quality"]
    assert [f["check"] for f in held["findings"]] == ["ocr-layer"]
    assert held["text_hash"] == store.get_document(con, scan, max_chars=0)["text_hash"]
    # a typed page without a formula: its text layer is the text
    plain = _captured(con, _typed("Just words."))
    assert pipeline.follow_ups(con, plain, stamp=LAYER, action="created") is None
    assert _waiting(con, plain) == []
    # what an import brought is not a capture: nothing is asked
    imported = store.register(
        con, _scan(3), mime="application/pdf", meta={"source": "zotero"}
    )
    store.index_text(con, imported["doc_id"], "Words. " * 40, text_source=LAYER)
    assert (
        pipeline.follow_ups(con, imported["doc_id"], stamp=LAYER, action="created")
        is None
    )


def test_a_source_that_fails_hands_the_paper_to_marker(
    con: store.sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper = _captured(con, _typed("A paper on arXiv."), arxiv="2406.06293")
    pipeline.follow_ups(con, paper, stamp=LAYER, action="created")
    store.finish_reading(con, paper, outcome="error", stamp="latex-source/3.9-r2")
    meta = store.get_meta(con, paper)
    meta.setdefault("parse_history", []).append(
        {"extractor": "latex-source/3.9-r2", "error": "pandoc could not convert"}
    )
    store.set_meta(con, paper, meta)
    assert (
        pipeline.follow_ups(con, paper, stamp="latex-source/3.9-r2", action="error")
        == "marker"
    )
    assert ("marker", None) in _waiting(con, paper)
    # and the host can turn the choosing off
    monkeypatch.setenv("PRAX_CHOOSE_READER", "off")
    other = _captured(con, _scan())
    assert pipeline.follow_ups(con, other, stamp=LAYER, action="created") is None


def _eprint(tmp_path: Path, tex: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = tex.encode("utf-8")
        info = tarfile.TarInfo("paper.tex")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return gzip.compress(buf.getvalue())


TEX = (
    "\\documentclass{article}\\begin{document}\\section{Introduction}"
    "The bound holds for every real $x$:\\begin{equation}f(x)\\le 2x+1"
    "\\label{e}\\end{equation}By \\eqref{e} the claim follows. "
    + "More words of the paper. " * 30
    + "\\end{document}"
)


@pytest.mark.skipif(latex.pandoc() is None, reason="pandoc is not installed")
def test_the_reading_turns_the_e_print_into_the_papers_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def fetch(arxiv: str) -> bytes:
        asked.append(arxiv)
        return _eprint(tmp_path, TEX)

    monkeypatch.setattr(latexsrc, "_fetch", fetch)
    monkeypatch.setenv("PRAX_ARXIV_ID", "2501.13484")
    ext = parsers.by_name("latex-source")
    assert ext.stamp.startswith("latex-source/") and ext.explicit_only
    text = ext(_typed("A paper."), previous="The PDF's text. " * 10)
    assert asked == ["2501.13484"]
    assert "# Introduction" in text
    assert any(x.startswith("$$") and "\\tag{1}" in x for x in text.splitlines())
    # a conversion that lost the paper is refused, not filed
    with pytest.raises(parsers.ExtractionError):
        ext(_typed("A paper."), previous="The PDF's text. " * 2000)


def test_an_e_print_without_source_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Answer(io.BytesIO):
        headers: ClassVar[dict[str, str]] = {"Content-Type": "application/pdf"}

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(
        latexsrc.urllib.request, "urlopen", lambda req, timeout: Answer(b"%PDF-1.5 ...")
    )
    with pytest.raises(parsers.ExtractionError, match="only a PDF"):
        latexsrc._fetch("2501.13484")
    store.check_mode("latex-source", "math.GT/0309136")
    with pytest.raises(ValueError):
        store.check_mode("latex-source", "not an id")
