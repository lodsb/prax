"""EPUB books (``prax.parsers.epub``): the spine's XHTML in reading
order, the chapters placed by the book's table of contents. Every book
here is made up in the test."""

from __future__ import annotations

import io
import zipfile

import pytest

from prax import parsers

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""


def _epub(opf: str, files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        for name, body in files.items():
            z.writestr(f"OEBPS/{name}", body)
    return buf.getvalue()


def _page(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title>'
        "<style>p { color: red }</style></head>"
        f"<body>{body}</body></html>"
    )


EPUB3_OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="c2" href="text/two.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"
      properties="nav"/>
    <item id="c1" href="text/one.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="nav"/><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""

NAV = _page(
    '<nav epub:type="landmarks"><ol><li><a href="text/one.xhtml">Start</a></li>'
    "</ol></nav>"
    '<nav epub:type="toc"><ol>'
    '<li><a href="text/one.xhtml">Chapter One: Tape</a><ol>'
    '<li><a href="text/one.xhtml#echo">Echo</a></li></ol></li>'
    '<li><a href="text/two.xhtml">Chapter Two</a></li>'
    "</ol></nav>"
)

ONE = _page(
    '<p class="chapter-title">Chapter One: Tape</p>'
    "<p>A tape loop repeats&nbsp;what it was given &amp; fades.</p>"
    "<script>var no = 1;</script>"
    "<h1>A heading the table leaves out</h1>"
    '<p>Before the <span id="echo">echo</span> comes the delay.</p>'
    "<ul><li><p>wow</p></li><li>flutter</li></ul>"
    "<table><tr><th>speed</th><th>ips</th></tr><tr><td>slow</td><td>7.5</td></tr>"
    "</table>"
    "<pre>loop:\n  play\n  rewind</pre>"
    "<p>A long word: re\xadcord.</p>"
)

TWO = _page("<h2>Chapter Two</h2><p>The second part.</p>")


def test_an_epub_reads_in_spine_order_with_its_chapters_from_the_table() -> None:
    data = _epub(
        EPUB3_OPF, {"nav.xhtml": NAV, "text/one.xhtml": ONE, "text/two.xhtml": TWO}
    )
    text = parsers.by_name("epub")(data)
    assert parsers.candidates("application/epub+zip")[0].name == "epub"
    lines = [line for line in text.splitlines() if line]
    # a chapter title styled as a paragraph becomes the entry's heading
    assert lines[0] == "# Chapter One: Tape"
    # a no-break space is a space in running text
    assert lines[1] == "A tape loop repeats what it was given & fades."
    # a heading the table does not list sits under the entry before it
    assert "## A heading the table leaves out" in lines
    # an entry pointing at plain text puts its title in before it
    i = lines.index("## Echo")
    assert lines[i + 1] == "Before the echo comes the delay."
    assert "- wow\n- flutter" in text
    assert "| speed | ips |\n|---|---|\n| slow | 7.5 |" in text
    assert "```\nloop:\n  play\n  rewind\n```" in text
    assert "record." in text and "var no" not in text and "color" not in text
    # the h2 the table lists at level 1 takes the table's level
    assert lines[-2:] == ["# Chapter Two", "The second part."]
    # the nav document is the table, not text; the manifest's order is not read
    assert "Start" not in text and text.index("Tape") < text.index("second part")


EPUB2_OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="a" href="a.html" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="a"/></spine>
</package>"""

NCX = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="p1"><navLabel><text>Part I</text></navLabel>
      <content src="a.html#part"/>
      <navPoint id="p2"><navLabel><text>Oscillators</text></navLabel>
        <content src="a.html#osc"/></navPoint>
    </navPoint>
  </navMap>
</ncx>"""


def test_an_epub_2_takes_its_chapters_from_the_ncx() -> None:
    page = _page(
        '<div id="part"><p>PART I</p></div>'
        '<p id="osc">An oscillator makes a periodic wave.</p>'
    )
    text = parsers.by_name("epub")(_epub(EPUB2_OPF, {"toc.ncx": NCX, "a.html": page}))
    assert text.splitlines()[0] == "# PART I"
    assert "## Oscillators\n\nAn oscillator makes a periodic wave." in text


def test_an_epub_of_page_images_has_next_to_no_text() -> None:
    pages = _page('<div><img src="p1.jpg" alt=""/></div>')
    data = _epub(EPUB2_OPF, {"a.html": pages})  # no toc.ncx in the zip
    assert parsers.by_name("epub")(data) == ""


def test_what_is_not_an_epub_says_so() -> None:
    with pytest.raises(ValueError, match="not an EPUB"):
        parsers.by_name("epub")(b"plain bytes")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "a zip, no package")
    with pytest.raises(ValueError, match="no package file"):
        parsers.by_name("epub")(buf.getvalue())


def test_a_book_is_refused_past_the_bound_on_its_whole_xhtml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each member is capped where it is inflated; a spine that names one
    member many times is capped as a whole (the review of 2026-11-26)."""
    from prax.parsers import epub

    many = EPUB2_OPF.replace('<itemref idref="a"/>', '<itemref idref="a"/>' * 50)
    data = _epub(many, {"a.html": _page("<p>" + "word " * 100 + "</p>")})
    assert parsers.by_name("epub")(data).count("word") == 50 * 100
    monkeypatch.setattr(epub, "ZIPPED_XML_MAX", 10_000)
    with pytest.raises(ValueError, match="inflates to"):
        parsers.by_name("epub")(data)
