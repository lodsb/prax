"""Papers with their LaTeX source (``prax import latex``): the source read
into the Markdown prax indexes, and the door taking a PDF with that text."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.importers import latex

MAIN = r"""\documentclass{article}
\newcommand{\R}{\mathbb R}
\begin{document}
\section{Introduction}
We prove the bound of Smith~\cite{smith} and Jones~\cite[Thm.~2]{jones}. % a comment
\input{sections/body}
\bibliographystyle{plain}
\bibliography{references}
\end{document}
"""
BODY = r"""\section{The bound}
For every $x\in\R$,
\begin{equation}\label{eq:main}
f(x) \le 2x + 1.
\end{equation}
By \eqref{eq:main} the claim follows. 50\% of it is easy.
\begin{align}
a &= b \\
c &= d \nonumber
\end{align}
"""
BIB = r"""@article{smith, author = {Smith, Ann}, title = {{A} bound},
  journal = {J. Math.}, year = {2001}, doi = {10.1/abc}}
@book{jones, author = {Jones, Bo and Lee, Cy}, title = "Bounds",
  publisher = {Press}, year = 1999}
"""


def _manuscript(root: Path) -> Path:
    folder = root / "A-bound-September-25-2026"
    (folder / "build" / "sections").mkdir(parents=True)
    (folder / "README.md").write_text(
        "# [A Bound for Everything](paper.pdf)\n\n**Author:** OpenAI\n"
        "September 25, 2026\n",
        encoding="utf-8",
    )
    (folder / "paper.pdf").write_bytes(b"%PDF-1.4 a stand-in")
    (folder / "build" / "main.tex").write_text(MAIN, encoding="utf-8")
    (folder / "build" / "sections" / "body.tex").write_text(BODY, encoding="utf-8")
    (folder / "build" / "references.bib").write_text(BIB, encoding="utf-8")
    # a figure built on its own inside the manuscript is part of it
    (folder / "build" / "figures").mkdir()
    (folder / "build" / "figures" / "fig.pdf").write_bytes(b"%PDF-1.4 a figure")
    (folder / "build" / "figures" / "fig.tex").write_text(
        r"\documentclass{standalone}\begin{document}x\end{document}", encoding="utf-8"
    )
    return folder


def test_a_folder_is_a_manuscript_with_its_title_authors_and_date(
    tmp_path: Path,
) -> None:
    folder = _manuscript(tmp_path)
    found = latex.manuscripts(tmp_path)
    assert len(found) == 1
    ms = found[0]
    assert ms.folder == folder and ms.pdf.name == "paper.pdf"
    assert ms.main.name == "main.tex" and ms.title == "A Bound for Everything"
    assert ms.authors == ("OpenAI",) and ms.date == "2026-09-25"
    assert latex.folder_date("Tangent-flow-2026-09-24") == "2026-09-24"
    assert latex.author_line("OpenAI · September 26, 2026") == ("OpenAI",)
    assert latex.author_line("A. Writer and B. Writer") == ("A. Writer", "B. Writer")


def test_the_source_is_flattened_cited_and_numbered(tmp_path: Path) -> None:
    ms = latex.manuscripts(_manuscript(tmp_path).parent)[0]
    tex = latex.flatten(ms.main)
    assert "\\section{The bound}" in tex and "% a comment" not in tex
    assert "50\\% of it" in tex  # an escaped percent is not a comment
    tex, entries = latex.bibliography(tex, ms.main.parent)
    # plain: by the authors' surnames
    assert [k for k, _ in entries] == ["jones", "smith"]
    assert "Bo Jones, Cy Lee" in entries[0][1] and "doi:10.1/abc" in entries[1][1]
    assert "\\bibliography{" not in tex
    tex = latex.number_citations(tex, [k for k, _ in entries])
    assert "Smith~{[2]}" in tex and "Jones~{[1, Thm.~2]}" in tex
    tex = latex.number_displays(tex)
    assert "\\tag{1}" in tex and "By (1) the claim" in tex
    assert "\\tag{2}" in tex and tex.count("\\tag{") == 2  # \nonumber row unnumbered
    section = latex.references_section(entries)
    assert section.startswith("\\section*{References}") and "[2] Ann Smith" in section


def test_a_thebibliography_keeps_its_own_order(tmp_path: Path) -> None:
    tex = (
        "See \\cite{b}.\\begin{thebibliography}{9}"
        "\\bibitem{b} B. Second.\\bibitem[X]{a} A. First.\\end{thebibliography}"
    )
    rest, entries = latex.bibliography(tex, tmp_path)
    assert [k for k, _ in entries] == ["b", "a"] and "thebibliography" not in rest
    assert latex.number_citations(rest, ["b", "a"]) == "See {[1]}."


def test_pandoc_markdown_is_tidied_as_prax_reads_it() -> None:
    md = (
        "Text \\[3, 7\\] and [Theorem 1](#thm:main).\n\n$$\\begin{aligned} a &= b"
        ' \\tag{4}\\\\\n c &= d \\tag{5} \\end{aligned}$$\n\n<div class="x">'
        "$$\\begin{equation*}x = 1\\label{e}\\end{equation*}$$</div>"
    )
    out = latex.tidy(md)
    assert "Text [3, 7] and Theorem 1." in out
    assert "$$\\begin{aligned} a &= b\\\\ c &= d \\end{aligned} \\tag{4–5}$$" in out
    assert "$$x = 1$$" in out and "<div" not in out


@pytest.mark.skipif(latex.pandoc() is None, reason="pandoc is not installed")
def test_a_manuscript_converts_end_to_end(tmp_path: Path) -> None:
    ms = latex.manuscripts(_manuscript(tmp_path).parent)[0]
    text = latex.convert(ms, str(latex.pandoc()))
    assert "# Introduction" in text and "# References" in text
    lines = text.splitlines()
    assert any(x.startswith("$$") and x.endswith("\\tag{1}$$") for x in lines)
    assert "[2] Ann Smith" in text and "By (1) the claim" in text


@pytest.fixture()
def client() -> TestClient:
    from prax.api import app

    with TestClient(app) as c:
        yield c  # type: ignore[misc]


TEXT = (
    "# The bound\n\nFor every real $x$ the bound holds:\n\n"
    "$$f(x) \\le 2x + 1 \\tag{1}$$\n\nBy (1) the claim follows. " + "More. " * 60
)


def test_the_door_takes_a_pdf_with_its_text(client: TestClient) -> None:
    pdf = b"%PDF-1.4 a stand-in for a paper"
    form = {"title": "A bound", "text": TEXT, "text_source": "latex-source/3.9"}
    got = client.post(
        "/ingest/file", data=form, files={"file": ("p.pdf", pdf, "application/pdf")}
    )
    assert got.status_code == 200, got.text
    doc = int(got.json()["doc_id"])
    con = client.app.state.con
    meta = store.get_meta(con, doc)
    assert meta["text_source"] == "latex-source/3.9"
    assert meta["parse_history"][-1]["extractor"] == "latex-source/3.9"
    kinds = {c["kind"] for c in store.list_chunks(con, doc)}
    assert "formula" in kinds
    # a parser's name is the parse queue's to stamp; text and source together
    for bad in (
        {"text": TEXT, "text_source": "pymupdf4llm/1.0"},
        {"text": TEXT},
        {"text_source": "latex-source/3.9"},
    ):
        r = client.post(
            "/ingest/file", data=bad, files={"file": ("q.pdf", b"%PDF other", "a/b")}
        )
        assert r.status_code == 400, bad


def test_an_unasked_parse_keeps_a_senders_text(client: TestClient) -> None:
    """A parse handed out while the PDF had no text yet comes back after
    the sender's LaTeX text: the sender's stays; a reading someone asks
    for still replaces it."""
    from prax.parsers import queue

    got = client.post(
        "/ingest/file",
        data={"text": TEXT, "text_source": "latex-source/3.9"},
        files={"file": ("p.pdf", b"%PDF-1.4 raced", "application/pdf")},
    )
    doc = int(got.json()["doc_id"])
    con = client.app.state.con
    parsed = "A PDF's text layer. " * 200
    stamp = "pymupdf4llm/1.28.2-r2"
    assert queue.apply_parse(con, doc, stamp=stamp, text=parsed, unasked=True) == "kept"
    assert store.get_meta(con, doc)["text_source"] == "latex-source/3.9"
    assert queue.apply_parse(con, doc, stamp=stamp, text=parsed) == "upgraded"
    assert queue.senders_text("latex-source/3.9")
    assert not queue.senders_text("zotero-ft-cache")
    assert not queue.senders_text(stamp)


FIGURES = (
    r"\section{One}\begin{figure}\centering\begin{tikzpicture}\draw (0,0)--(1,1);"
    r"\end{tikzpicture}\caption[short]{A line, $x$.}\label{fig:line}\end{figure}"
    r" As Figure~\ref{fig:line} shows.\section{Two}\begin{figure*}"
    r"\caption{Second.}\end{figure*}"
)


def test_a_figure_is_its_caption_line_with_the_number_the_pdf_prints() -> None:
    out = latex.figure_captions(FIGURES)
    assert "Figure 1: A line, $x$." in out and "Figure~1 shows" in out
    assert "Figure 2: Second." in out and "tikzpicture" not in out
    by_section = latex.figure_captions(r"\numberwithin{figure}{section}" + FIGURES)
    assert "Figure 1.1: A line" in by_section and "Figure 2.1: Second." in by_section
    assert latex.stamp("3.9") == f"latex-source/3.9-r{latex.REVISION}"


def test_a_text_past_a_form_field_comes_as_a_file_part(client: TestClient) -> None:
    """A page with its pictures inlined passes the 1 MB a form field may
    hold: the text comes as the file part ``text_file``."""
    big = TEXT + "\n\n" + ("A long paragraph of prose. " * 50_000)
    got = client.post(
        "/ingest/file",
        data={"text_source": "latex2html/1"},
        files={
            "file": ("p.html", b"<html>a page</html>", "text/html"),
            "text_file": ("text.md", big.encode("utf-8"), "text/markdown"),
        },
    )
    assert got.status_code == 200, got.text
    doc = int(got.json()["doc_id"])
    meta = store.get_meta(client.app.state.con, doc)
    assert meta["text_source"] == "latex2html/1"
    assert store.get_document(client.app.state.con, doc)["text_len"] > 1_000_000
