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
# a chunk a page number. `prax.parsers` writes it, `prax.text.chunking` and
# `prax.parsers.figures` read it
_PAGE_MARK = r"--- end of page\.page_number=(?P<page>\d+) ---"
# matched against one line at a time
PAGE_MARK = re.compile(rf"^{_PAGE_MARK}\s*$")
# unanchored, for a caller that clears the marks out of a text whose lines
# it has not kept: `titles.head` collapses the text before it reads it
PAGE_MARK_ANY = re.compile(_PAGE_MARK)


# the words pymupdf4llm finds inside a figure drawn with vector paths (axis
# ticks, labels, the boxes of a block diagram), between two comment lines
# and joined with <br>: the start marker alone on its line, the end one
# closing the last line of the block
PICTURE_START = "<!-- Start of picture text -->"
PICTURE_END = "<!-- End of picture text -->"
PICTURE = re.compile(
    re.escape(PICTURE_START) + ".*?" + re.escape(PICTURE_END), re.DOTALL
)
LINE_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)  # a break inside a cell or a block


def picture_words(block: str) -> str:
    """The words of a picture-text block: its markers out, its breaks as
    spaces."""
    inner = block.replace(PICTURE_START, " ").replace(PICTURE_END, " ")
    return " ".join(LINE_BREAK.sub(" ", inner).split())


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
# the separator row of a Markdown table, which is what makes it a table
TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def heading(level: int, text: str) -> str:
    return f"{'#' * max(1, min(6, level))} {text}"


def section_span(text: str, title: str) -> tuple[int, int, int] | None:
    """Where the section headed ``title`` lies in a Markdown ``text``:
    ``(body_start, body_end, level)``, the body running from the line
    after its heading to the next heading of the same or a higher level
    (or the end). The first heading whose words are ``title``, case and
    spacing aside; outside a fenced code block. None when there is none."""
    want = " ".join(title.split()).casefold()
    lines = text.splitlines(keepends=True)
    offset = 0
    found: tuple[int, int] | None = None  # (body start, level)
    fenced = False
    for line in lines:
        bare = line.rstrip("\r\n")
        if bare.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        m = None if fenced else HEADING.match(bare)
        if m:
            level = len(m.group("hashes"))
            if found is not None and level <= found[1]:
                return found[0], offset, found[1]
            if found is None and " ".join(m.group("text").split()).casefold() == want:
                found = (offset + len(line), level)
        offset += len(line)
    return (found[0], len(text), found[1]) if found else None


# The sections prax adds to a captured page. They are headings like any
# other, and named here so a writer and a reader cannot disagree on the
# words
FIGURES_HEADING = "## Figures"
COMMENTS_HEADING = "## Comments"


# -------------------------------------------------------------- formulas

# a display equation alone on its line, as a parser that reads maths
# writes it: `$$ x = \frac{a}{b}, \quad (4) $$`
FORMULA = re.compile(r"^\$\$(?P<latex>.+)\$\$$", re.DOTALL)
# the number the prose refers to it by, at the end: "\quad (4)", ", (4)",
# "~(4)", "  (4)", "\tag{4}". Bare parentheses count only after a
# separator: the (2) of \log(2) is an argument, and taking it for a number
# cut a link short (2026-10-02). The number is group 1, 2 or 3; take it
# with ``eq_number``.
EQ_NUMBER = re.compile(
    r"(?:(?:\\q?quad|\\hfill)\s*\{?\(?\s*(\d{1,3}[a-z]?)\s*\)?\}?"
    r"|(?:,|~|\s{2,})\s*\(\s*(\d{1,3}[a-z]?)\s*\)"
    r"|\\tag\*?\{\s*\(?\s*([^{}]*?)\s*\)?\s*\})\s*$"
)


def eq_number(latex: str) -> tuple[str, str | None]:
    """A display's LaTeX without its equation number, and the number (None
    when it has none)."""
    m = EQ_NUMBER.search(latex)
    if not m:
        return latex, None
    number = m.group(1) or m.group(2) or m.group(3) or None
    return latex[: m.start()].rstrip().rstrip(",.").rstrip(), number


def formula(latex: str) -> str:
    return f"$${latex}$$"


# ----------------------------------------------------------------- links

# a link to a library document in a page's prose, ``[title](#doc/12)``:
# the page annotates that document, an edge kept while the link stands
DOC_LINK = re.compile(r"\]\(#doc/(\d+)(?:[?#][^)]*)?\)")


def doc_link(title: str, doc_id: int) -> str:
    return f"[{title}](#doc/{doc_id})"
