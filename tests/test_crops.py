"""figure-crops: the picture a caption claims, rendered off the page.

A plot drawn with vector paths is not an image object, so no extractor
pulls it out and the caption is left with nothing behind it. More than
half the live store's figure chunks were those.
"""

from __future__ import annotations

import base64

import pytest

from prax.parsers import figures

pymupdf = pytest.importorskip("pymupdf")


def _paper() -> bytes:
    """A two-page PDF: page 1 a vector plot under its caption, page 2 a
    caption whose figure is not drawn anywhere."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(80, 90, 300, 260), color=(0, 0, 1), width=1.5)
    page.draw_line(pymupdf.Point(90, 240), pymupdf.Point(290, 110))
    page.insert_text(pymupdf.Point(80, 300), "Figure 1: A line and a box.")
    page.insert_text(pymupdf.Point(80, 400), "Prose about the figure above.")
    second = doc.new_page()
    second.insert_text(pymupdf.Point(80, 120), "Figure 2: Nothing is drawn for this.")
    out = doc.tobytes()
    doc.close()
    return out


TEXT = """# A paper

Prose about the figure above.

Figure 1: A line and a box.

--- end of page.page_number=1 ---

Figure 2: Nothing is drawn for this.

--- end of page.page_number=2 ---
"""


def test_a_bare_caption_gets_the_picture_above_it() -> None:
    out = figures.add_crops(_paper(), TEXT)
    pictures = figures.DATA_IMAGE.findall(out)
    assert len(pictures) == 1  # figure 2 has nothing to render
    alt, url = pictures[0]
    assert alt == "Figure 1: A line and a box."
    png = base64.b64decode(url.split(",", 1)[1])
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000
    # the caption line became the picture; nothing else moved
    assert "Figure 2: Nothing is drawn for this." in out
    assert out.count("Prose about the figure above.") == 1


def test_a_caption_whose_figure_is_already_there_is_left_alone() -> None:
    """pymupdf4llm places the picture *and* leaves the caption in the
    prose, so the same figure appears twice; cropping the second would
    make a duplicate."""
    with_picture = TEXT.replace(
        "Figure 1: A line and a box.",
        "![Figure 1: A line and a box.](figure:" + "ab" * 32 + ")\n\n"
        "Figure 1: A line and a box.",
    )
    assert figures.DATA_IMAGE.findall(figures.add_crops(_paper(), with_picture)) == []


def test_prose_about_a_figure_is_not_a_caption() -> None:
    """ "Figure 2 shows a recorded performance" is a sentence, not a
    caption: cropping from it sweeps in the real caption below."""
    assert figures._CAPTION_LINE.match("Figure 1: A line and a box.")
    assert figures._CAPTION_LINE.match("Fig 1 -- Magnitude and phase response")
    assert figures._CAPTION_LINE.match("Figure 8.1: The Sierpinski gasket")
    assert figures._CAPTION_LINE.match("Abbildung 3: Aufbau der Schaltung")
    assert not figures._CAPTION_LINE.match("Figure 2 shows a recorded performance")
    assert not figures._CAPTION_LINE.match("Figure 7.10 shows an abstract path")
    prose = TEXT.replace(
        "Figure 1: A line and a box.", "Figure 1 shows a line and a box."
    )
    assert figures.DATA_IMAGE.findall(figures.add_crops(_paper(), prose)) == []


def test_the_region_stops_at_white_space() -> None:
    """The paragraph above a figure is not part of it: the climb stops at
    a gap, and a region too small to be a picture is no region."""
    doc = pymupdf.open(stream=_paper(), filetype="pdf")
    page = doc[0]
    caption = figures.find_caption(page, "Figure 1: A line and a box.")
    assert caption is not None
    rect = figures.crop_region(page, caption)
    assert rect is not None
    assert rect.y1 <= caption.y0  # never over the caption itself
    assert rect.y0 >= 80  # not up into the page's top margin
    # a caption with nothing above it gets nothing
    second = doc[1]
    where = figures.find_caption(second, "Figure 2: Nothing is drawn for this.")
    assert where is not None and figures.crop_region(second, where) is None
    doc.close()


def test_the_extractor_is_registered_and_wants_the_text() -> None:
    from prax import parsers, store

    ext = parsers.by_name("figure-crops")
    assert ext.previous and ext.annotates and ext.explicit_only
    assert "figure-crops" in store.READINGS
    with pytest.raises(parsers.ExtractionError, match="parse the document first"):
        ext(_paper(), filename="p.pdf", previous=None)
