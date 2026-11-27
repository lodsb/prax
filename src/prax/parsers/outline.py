"""A PDF's outline (its bookmarks) as the headings of its text.

The layout reader ranks a heading by its font size among the heading sizes
of its window of pages, so a book's chapters and sections come out at
levels that drift from window to window, and a chapter title set as a
picture or in body type is no heading at all. The outline is the author's
own table: a level, a title and a page per entry. Of the library's 71 book
PDFs, 35 carried one of five entries or more (2026-11-26).

``apply`` gives each entry's line on its page the entry's level (a title
broken over two lines is found as one), inserts an entry it cannot find at
the top of its page, and puts every heading the outline does not list under
the entry before it. It reads nothing but the text and the entries, so the
``outline`` reading annotates the current text without reading the pages
again (``prax.parsers.readings``).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from prax.text import markup

MIN_ENTRIES = 5  # an outline of fewer is a document's few bookmarks, not its table
# the share of its entries an outline must have found on their pages to be
# this text's (measured 2026-11-27 on the library's book PDFs, below)
MIN_FOUND = 0.5
MIN_PART = 0.4  # the share of an outline title a line printing part of it covers
MAX_TITLE_LINE = 200  # a line longer than this is prose, never a title
# the numbering in front of a title, which the outline and the page may
# spell differently ("3 Voicings", "Chapter 3. Voicings", "III. Voicings")
_NUMBERING = re.compile(
    r"^(?:(?:chapter|part|section|appendix|kapitel|teil|chapitre|partie)\s+)?"
    r"(?:(?:\d+|[ivxlc]+|[a-z])(?:[.:)]|\s)\s*)+"
)


def entries(doc: Any) -> list[tuple[int, str, int]]:
    """A MuPDF document's outline as (level, title, page), the page
    counted from 1; an entry that points nowhere is left out."""
    out = []
    for item in doc.get_toc(simple=True):
        level, title, page = int(item[0]), str(item[1]).strip(), int(item[2])
        if title and page >= 1:
            out.append((level, title, page))
    return out


def _key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"^#{1,6}\s+", "", s.strip())
    s = re.sub(r"[*_`]+", "", s).casefold()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _bare(key: str) -> str:
    return _NUMBERING.sub("", key + " ").strip()


def _same(line: str, title: str) -> bool:
    a, b = _key(line), _key(title)
    if not a or not b:
        return False
    if a == b:
        return True
    ba, bb = _bare(a), _bare(b)
    if ba and ba == bb:
        return True
    # the outline joins a label, a title and a subtitle ("Lesson 2 Sound
    # Level Changes: Tones and speech") that the page prints apart: the
    # line is a run of the title's words, two or more and 40% of it
    words = ba.split()
    return len(words) >= 2 and len(ba) >= MIN_PART * len(bb) and f" {ba} " in f" {bb} "


class _Lines:
    """The text's lines with the page each stands on (the page mark ends
    its page), the fenced ones and the marks themselves not candidates."""

    def __init__(self, text: str) -> None:
        self.lines = text.split("\n")
        self.page: list[int] = []
        self.open: list[bool] = []
        # a line's page: the number of the next mark at or after it
        marks = [int(m.group("page")) for m in markup.PAGE_MARK_ANY.finditer(text)]
        last = marks[-1] + 1 if marks else 1
        k, fenced = 0, False
        for line in self.lines:
            bare = line.strip()
            is_mark = bool(markup.PAGE_MARK_ANY.search(line))
            self.page.append(marks[k] if k < len(marks) else last)
            if bare.startswith("```"):
                fenced = not fenced
            self.open.append(
                not fenced
                and not is_mark
                and bool(bare)
                and len(bare) <= MAX_TITLE_LINE
                and not bare.startswith(("![", "|", "$$", "<!--"))
            )
            if is_mark:
                k += 1

    def on(self, page: int) -> list[int]:
        return [i for i, p in enumerate(self.page) if p == page]


def apply(text: str, toc: list[tuple[int, str, int]]) -> str:
    """The text with the outline's entries as its headings (see the
    module). An outline of fewer than ``MIN_ENTRIES`` changes nothing, nor
    one whose titles are found on their pages for fewer than ``MIN_FOUND``
    of its entries: it describes another text (an index's page labels, a
    print the scan is not of)."""
    new, found = place(text, toc)
    return new if found >= MIN_FOUND * len(toc) else text


def place(text: str, toc: list[tuple[int, str, int]]) -> tuple[str, int]:
    """``apply``'s work without its last test: the text with the outline
    placed, and how many of its entries were found on their page."""
    if len(toc) < MIN_ENTRIES:
        return text, 0
    top = min(level for level, _, _ in toc)
    doc = _Lines(text)
    lines = doc.lines
    level_at: dict[int, int] = {}  # line index -> outline level
    inserts: dict[int, list[str]] = {}  # line index -> headings before it
    taken: set[int] = set()
    for level, title, page in toc:
        depth = min(6, level - top + 1)
        found: tuple[int, int | None] | None = None
        for p in (page, page + 1, page - 1):
            for i in doc.on(p):
                if i in taken or not doc.open[i]:
                    continue
                if _same(lines[i], title):
                    found = (i, None)
                    break
                # a title broken over two lines: "Chapter 3" / "Voicings"
                j = next(
                    (j for j in range(i + 1, len(lines)) if lines[j].strip()), None
                )
                if (
                    j is not None
                    and doc.open[j]
                    and doc.page[j] == p
                    and _same(
                        lines[i].strip("# *_") + " " + lines[j].strip("# *_"), title
                    )
                ):
                    found = (i, j)
                    break
            if found:
                break
        if found is None:
            first = next((i for i in doc.on(page) if lines[i].strip()), None)
            if first is not None:
                inserts.setdefault(first, []).append(markup.heading(depth, title))
            continue
        i, j = found
        words = (
            _plain(lines[i]) if j is None else f"{_plain(lines[i])} {_plain(lines[j])}"
        )
        lines[i] = markup.heading(depth, words)
        level_at[i] = depth
        taken.add(i)
        if j is not None:
            lines[j] = ""
            taken.add(j)
    out: list[str] = []
    current = 0  # the level of the outline's entry the text is in
    for i, line in enumerate(lines):
        for h in inserts.get(i, []):
            out += [h, ""]
            current = len(h) - len(h.lstrip("#"))
        if i in level_at:
            current = level_at[i]
        elif current and doc.open[i]:
            m = markup.HEADING.match(line.strip())
            if m and len(m.group("hashes")) <= current:
                # a heading the outline does not list: under its entry
                line = markup.heading(min(6, current + 1), m.group("text"))
        out.append(line)
    return "\n".join(out), len(level_at)


def _plain(line: str) -> str:
    """A line's words without the Markdown a layout reader put on them."""
    s = re.sub(r"^#{1,6}\s+", "", line.strip())
    s = re.sub(r"^(\*\*|__)(.*)\1$", r"\2", s)
    return s.strip(" *_")
