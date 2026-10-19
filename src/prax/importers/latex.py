"""Papers with their LaTeX source: the PDF is the original, the text comes
from the source (``prax import latex``).

Each manuscript is a folder with its PDF and the LaTeX that built it;
``manuscripts`` finds them and what their README and folder name say
(title, authors, date). The conversion is ``prax.parsers.latex``, the one
the worker's ``latex-source`` reading runs too. The door keeps the PDF
under its hash and the text as its text artifact, stamped
``latex-source/<pandoc version>`` (``POST /ingest/file`` with ``text``), so
the PDF's pages and figures are served as for any paper and the parse
queue leaves the text alone. Nothing here opens the store: this runs
where the ``prax`` command runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from prax.parsers import latex as conversion

SOURCE = "latex"

_TITLE = re.compile(r"^#\s*\[(?P<title>[^\]]+)\]\((?P<pdf>[^)]+\.pdf)\)", re.MULTILINE)
_DATE = re.compile(
    r"-(January|February|March|April|May|June|July|August|September|October"
    r"|November|December)-(\d{1,2})-(\d{4})$"
)
_DATE_LINE = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$")
_AUTHORS = re.compile(r",\s*|\s+and\s+")
_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


@dataclass(frozen=True)
class Manuscript:
    folder: Path
    pdf: Path
    main: Path
    title: str
    date: str | None  # YYYY-MM-DD, from the folder's name
    authors: tuple[str, ...] = ()  # the README's line under the title

    @property
    def key(self) -> str:
        """The folder's name: what a later run knows the manuscript by."""
        return self.folder.name


def manuscripts(root: Path) -> list[Manuscript]:
    """Every folder under ``root`` with one PDF and a LaTeX file that
    holds ``\\begin{document}``, the shallowest first; a folder inside a
    manuscript found already (a figure built on its own) is part of it. A
    folder's README names its PDF, title and authors where it has one
    (``# [Title](paper.pdf)``, the authors on the next line)."""
    out: list[Manuscript] = []
    for pdf in sorted(root.rglob("*.pdf"), key=lambda p: len(p.parts)):
        folder = pdf.parent
        # a figure built on its own inside a manuscript is part of it
        if any(m.folder == folder or m.folder in folder.parents for m in out):
            continue
        found = _manuscript(folder)
        if found is not None:
            out.append(found)
    return out


def _manuscript(folder: Path) -> Manuscript | None:
    title = None
    pdf = None
    authors: tuple[str, ...] = ()
    readme = folder / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8", errors="replace")
        m = _TITLE.search(text)
        if m and (folder / m.group("pdf")).is_file():
            title, pdf = m.group("title").strip(), folder / m.group("pdf")
            after = [x.strip() for x in text[m.end() :].splitlines() if x.strip()]
            if after and not after[0].startswith("#"):
                authors = author_line(after[0])
    pdfs = sorted(folder.glob("*.pdf"))
    if pdf is None:
        if len(pdfs) != 1:
            return None
        pdf = pdfs[0]
    main = conversion.main_tex(folder)
    if main is None:
        return None
    return Manuscript(
        folder, pdf, main, title or folder.name, folder_date(folder.name), authors
    )


def author_line(line: str) -> tuple[str, ...]:
    """The authors a README's line names: "**Author:** OpenAI", "OpenAI ·
    September 26, 2026", "A. Writer and B. Writer"; a date is not one."""
    line = re.sub(r"[*_]", "", line)
    line = re.sub(r"^\s*authors?\s*:\s*", "", line, flags=re.IGNORECASE)
    line = re.split(r"\s+[·|–—]\s+", line)[0]
    return tuple(
        a.strip()
        for a in _AUTHORS.split(line)
        if a.strip() and not _DATE_LINE.match(a.strip()) and not a.strip().isdigit()
    )


def folder_date(name: str) -> str | None:
    """``…-September-25-2026`` or ``…-2026-09-25`` as ``2026-09-25``."""
    iso = re.search(r"-(\d{4})-(\d{2})-(\d{2})$", name)
    if iso:
        return "-".join(iso.groups())
    m = _DATE.search(name)
    if not m:
        return None
    month = _MONTHS.index(m.group(1)) + 1
    return f"{m.group(3)}-{month:02d}-{int(m.group(2)):02d}"
