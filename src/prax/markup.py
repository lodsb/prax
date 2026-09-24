"""The marks prax puts in a text, written and read in one place.

A parsed document is Markdown with prax's own additions: a line between
pages, a figure as a reference to its bytes, a reading under a figure or a
formula, a heading that sets aside a section the document did not write.
prax is both the author and the reader of that format, which is the one
case where a pattern has an obvious owner — and it had none.

So the page mark was *written* in four places in `prax.parsers` and
*matched* by three identical regular expressions in two other modules, one
file holding two copies; a Markdown heading had five spellings, three of
which captured different things; and every new reader of the format —
advertising, a recipe's ingredients, an ask block — copied the patterns
next to the one it needed, because there was nothing to import
(`docs/stratification.md`).

Nothing here is new behaviour. Every pattern below is one that already
existed somewhere, and the module's own test asserts the thing the old
arrangement could not: that what the writer emits is what the matcher
matches.

A caller that needs a mark in a pattern of its own imports the source
(``PAGE_MARK.pattern``) rather than writing the mark out again.
"""

from __future__ import annotations

import re

# ----------------------------------------------------------------- pages

# the line a parser puts after each page, 1-based, so the chunker can give
# a chunk a page number. `prax.parsers` writes it, `prax.chunking` and
# `prax.parsers.figures` read it
_PAGE_MARK = r"--- end of page\.page_number=(?P<page>\d+) ---"
# matched against one line at a time
PAGE_MARK = re.compile(rf"^{_PAGE_MARK}\s*$")
# and against a whole text, for a caller taking every mark out of one
PAGE_MARK_LINE = re.compile(rf"^{_PAGE_MARK}\s*$", re.MULTILINE)
# unanchored, for a caller that clears the marks out of a text whose lines
# it has not kept: `titles.head` collapses the text before it reads it
PAGE_MARK_ANY = re.compile(_PAGE_MARK)


def page_mark(page: int) -> str:
    """The mark that closes page ``page`` (1-based)."""
    return f"--- end of page.page_number={page} ---"


def page_break(page: int) -> str:
    """The mark with the blank lines around it, as a parser emits it."""
    return f"\n\n{page_mark(page)}\n\n"


# --------------------------------------------------------------- figures

# a figure in the text: its caption, and the hash of the bytes the archive
# holds. The bytes are served out of the original and never stored again
FIGURE_REF = re.compile(
    r"^!\[(?P<alt>[^\]\n]*)\]\(figure:(?P<ref>[0-9a-f]{16,64})\)[ \t]*$",
    re.MULTILINE,
)
# a picture a parser inlines for the door to file before indexing
DATA_IMAGE = re.compile(
    r"^!\[(?P<alt>[^\]\n]*)\]"
    r"\((?P<url>data:image/[a-z+.-]+;base64,[A-Za-z0-9+/=\s]+)\)[ \t]*$",
    re.MULTILINE,
)


def figure_ref(alt: str, ref: str) -> str:
    """A figure line: the caption and the hash of its bytes."""
    return f"![{alt}](figure:{ref})"


def data_image(alt: str, media: str, b64: str) -> str:
    """A picture inlined for the door to file (``figures.file_inline``)."""
    return f"![{alt}](data:{media};base64,{b64})"


# --------------------------------------------------------------- readings

# what a model said about a figure or a formula, under it. The kind is in
# the line so a reader can tell a figure's reading from a formula's
READ_BY = re.compile(
    r"^\*(?P<kind>Figure|Formula), as read by (?P<model>.+?):\* ?(?P<text>.*)$",
    re.MULTILINE,
)


def read_by(kind: str, model: str, text: str = "") -> str:
    """The line that introduces a reading. ``kind`` is Figure or Formula."""
    head = f"*{kind}, as read by {model}:*"
    return f"{head} {text}" if text else head


def read_by_pattern(kind: str) -> re.Pattern[str]:
    """``READ_BY`` narrowed to one kind, for a caller that wants only the
    figures' readings or only the formulas'."""
    return re.compile(
        rf"^\*{kind}, as read by (?P<model>.+?):\* ?(?P<text>.*)$", re.MULTILINE
    )


# --------------------------------------------------------------- headings

# a Markdown heading: the hashes and what follows them
HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.*?)\s*$")
HEADING_MULTILINE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.*?)\s*$", re.MULTILINE)
# the separator row of a Markdown table, which is what makes it a table
TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def heading(level: int, text: str) -> str:
    return f"{'#' * max(1, min(6, level))} {text}"


# The sections prax adds to a captured page. They are headings like any
# other, and named here so a writer and a reader cannot disagree on the
# words
FIGURES_HEADING = "## Figures"
COMMENTS_HEADING = "## Comments"


# -------------------------------------------------------------- formulas

# a display equation alone on its line, as a parser that reads maths
# writes it: `$$ x = \frac{a}{b}, \quad (4) $$`
FORMULA = re.compile(r"^\$\$(?P<latex>.+)\$\$$", re.DOTALL)
# the number the prose refers to it by, at the end: "\quad (4)"
EQ_NUMBER = re.compile(r"\\(?:quad|qquad|hfill|tag)\s*\{?\(?(\d{1,3}[a-z]?)\)?\}?\s*$")


def formula(latex: str) -> str:
    return f"$${latex}$$"


# ----------------------------------------------------------------- links

# a link to a library document in a page's prose, ``[title](#doc/12)``:
# the page annotates that document, an edge kept while the link stands
DOC_LINK = re.compile(r"\]\(#doc/(\d+)(?:[?#][^)]*)?\)")


def doc_link(title: str, doc_id: int) -> str:
    return f"[{title}](#doc/{doc_id})"
