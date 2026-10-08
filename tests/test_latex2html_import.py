"""Books written with latex2html and MathJax (``prax import latex2html``):
a page's content as Markdown with its mathematics as LaTeX."""

from __future__ import annotations

from pathlib import Path

from prax.importers import latex2html as l2h

PAGE = (Path(__file__).parent / "fixtures" / "latex2html" / "page.html").read_text(
    encoding="utf-8"
)

CONFIG = r"""window.MathJax = {
  tex: {
    inlineMath: [['\\(', '\\)']],
    macros: {
      abs: ['\\left|#1\\right|', 1],
      Ab: '\\mathbf{A}',
      Abt: '\\tilde{\\Ab}',
    }
  }
};"""


def test_a_page_reads_as_markdown_with_its_latex() -> None:
    defined = l2h.macros(CONFIG)
    assert defined["abs"] == ("\\left|#1\\right|", 1)
    seen: list[str] = []

    def picture(url: str) -> str:
        seen.append(url)
        return "data:image/png;base64,AAAA"

    url = "https://example.org/~jos/mdft/Euler_s_Identity.html"
    md = l2h.to_markdown(PAGE, url, picture, defined)
    lines = md.splitlines()
    assert lines[0] == "## Euler's Identity"
    assert "Since $z = x + jy$ we have *polar* form" in md
    assert "$$\\boxed{e^{j\\theta}=\\cos(\\theta)+j\\sin(\\theta)}$$" in lines
    assert "$\\left|z\\right|^2 = z\\overline{z}$" in md  # the macro written out
    assert seen == ["https://example.org/~jos/mdft/img2_2x.png"]  # the 2x picture
    figure = [x for x in lines if x.startswith("![")]
    caption = "Figure 1.1: The unit circle $\\left|z\\right|=1$."
    assert figure == [f"![{caption}](data:image/png;base64,AAAA)"]
    assert md.count("The unit circle") == 1  # the caption is the picture's
    assert "- one point" in lines and "- two" in lines
    # navigation, the subsection list and the address are not the page
    assert "A subsection" not in md and "Next" not in md and "cite" not in md
    assert l2h.page_title(PAGE) == "Euler's Identity"
    assert l2h.book_title(PAGE).startswith("Mathematics of the DFT, with Audio")
    assert l2h.short_title(l2h.book_title(PAGE)) == "Mathematics of the DFT"


def test_macros_expand_nested_and_with_arguments() -> None:
    defined = l2h.macros(CONFIG)
    assert l2h.expand("\\Abt x", defined) == "\\tilde{\\mathbf{A}} x"
    assert l2h.expand("\\abs{\\abs{x}}", defined) == "\\left|\\left|x\\right|\\right|"
    assert l2h.expand("\\abs x", defined) == "\\left|x\\right|"  # a token argument
    assert l2h.expand("\\Abig", defined) == "\\Abig"  # not a defined name


def test_a_book_lists_its_pages_in_order() -> None:
    index = (
        '<A HREF="Preface.html">P</A><A HREF="Index_this_Document.html">I</A>'
        '<A HREF="Ch1.html">1</A><A HREF="Preface.html">again</A>'
        '<A HREF="https://example.org/~jos/other/x.html">other book</A>'
        '<A HREF="mdft-citation.html">cite</A><A HREF="sub/deeper.html">deep</A>'
    )
    pages = l2h.book_pages("https://example.org/~jos/mdft/", index)
    assert pages == (
        "https://example.org/~jos/mdft/Preface.html",
        "https://example.org/~jos/mdft/Ch1.html",
    )
    assert l2h.srcset("a.png 1x, a_2x.png 2x") == {1.0: "a.png", 2.0: "a_2x.png"}


def test_the_fetcher_keeps_what_it_fetched(tmp_path: Path) -> None:
    fetcher = l2h.Fetcher(tmp_path, delay=0)
    cached = tmp_path / "example.org" / "~jos" / "mdft" / "index.html"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"<html>held</html>")
    # read from disk: no request, no robots.txt asked
    assert fetcher.get("https://example.org/~jos/mdft/") == b"<html>held</html>"
    assert fetcher.fetched == 0


def test_a_paper_on_one_page_is_its_own_only_page(tmp_path: Path) -> None:
    fetcher = l2h.Fetcher(tmp_path, delay=0)
    page = tmp_path / "example.org" / "~jos" / "tonehole" / "index.html"
    page.parent.mkdir(parents=True)
    page.write_bytes(b"<HTML><TITLE>Tone Holes</TITLE><BODY><P>All of it.</BODY>")
    got = l2h.book(fetcher, "https://example.org/~jos/tonehole/")
    assert got is not None and got.pages == ("https://example.org/~jos/tonehole/",)


INDEX = """<HTML><TITLE>A BOOK</TITLE><BODY><UL>
<LI><A HREF="Preface.html">Preface</A>
<UL><LI><A HREF="Outline.html">Chapter Outline</A></UL>
<LI><A HREF="Waveguides.html">Introduction to Digital Waveguides</A>
<UL><LI><A HREF="Strings.html">Ideal Strings</A>
<LI><A HREF="Index_this_Document.html">Index</A></UL>
</UL></BODY></HTML>"""


def test_a_book_gets_a_page_of_its_contents() -> None:
    url = "https://example.org/~jos/pasp/"
    entries = l2h.contents(url, INDEX)
    assert [(d, t) for d, t, _ in entries] == [
        (1, "Preface"),
        (2, "Chapter Outline"),
        (1, "Introduction to Digital Waveguides"),
        (2, "Ideal Strings"),
    ]
    book = l2h.Book(url, "Physical Audio Signal Processing", "PASP", ())
    documents = {url + "Preface.html": 7, url + "Strings.html": 9}
    text = l2h.contents_page(book, entries, documents, ["Julius O. Smith III"])
    lines = text.splitlines()
    assert lines[0] == "# Physical Audio Signal Processing"
    assert "- [Preface](#doc/7)" in lines and "  - Chapter Outline" in lines
    # a chapter that is only its sections is its title; its section links
    assert "- Introduction to Digital Waveguides" in lines
    assert "  - [Ideal Strings](#doc/9)" in lines
