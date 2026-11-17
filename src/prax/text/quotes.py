"""Where a quote stands in a text: the character range of an edge's
evidence in the text artifact it was read from (migration 48).

A model's quote is the text's own words, but not always its own
characters. It rewraps a line, drops a hyphen at a line's end, or folds
two spaces into one. It wraps the quote in quotation marks, leaves out
the Markdown the text carries (``_italics_``, ``**bold**``), and joins
the parts it kept with an ellipsis. So the quote is looked for as written
first; then as a pattern over the text itself, a run of whitespace in the
quote matching any run in the text, regardless of case; then with
Markdown allowed between and around its words; then with a hyphen at a
line's end allowed between any two letters; and last, a quote with an
ellipsis, by its parts, each found after the one before. The text is
never copied or folded: a book is megabytes, and a document's quotes are
hundreds. A quote not found is None, never a guess.

``VERSION`` says which of these the matcher does. A quote an older one
did not find is looked for again by the ``places`` pass (migration 71).
"""

from __future__ import annotations

import re

# 1: as written, whitespace and case loose, a line-end hyphen in a word
# 2 (2026-11-17, AV): quotation marks around it, Markdown in the text, and
#   the parts of a quote with an ellipsis
VERSION = 2
MIN_QUOTE = 8  # a shorter quote is found anywhere and places nothing
MAX_QUOTE = 600  # a longer one is no quote, and its pattern would be slow
PART_GAP = 3000  # characters an ellipsis may stand for between two parts
_SPACE = r"(?:-[ \t]*\n\s*|\s+)"  # a run of whitespace, or a hyphen ending a line
_BROKEN = r"(?:-[ \t]*\n\s*)?"  # a hyphen that ends a line, inside a word
_MARK = r"[*_`~]*"  # Markdown the text may carry around a word
_WRAPPING = " \t\n\"'“”„‟«»‘’‚‹›"
_ELLIPSIS = re.compile(r"\s*(?:\[\s*(?:\.\.\.|…)\s*\]|\.\.\.+|…)\s*")


def _pattern(quote: str, *, inside_words: bool, marks: bool = False) -> re.Pattern[str]:
    words = quote.split()
    if marks:
        words = [w.strip("*_`~") for w in words]
        words = [w for w in words if w]
    if inside_words:
        words = [_BROKEN.join(re.escape(ch) for ch in w) for w in words]
    else:
        words = [re.escape(w) for w in words]
    space = f"{_MARK}{_SPACE}{_MARK}" if marks else _SPACE
    return re.compile(space.join(words), re.IGNORECASE)


def _whole(text: str, q: str, start: int = 0) -> tuple[int, int] | None:
    """One run of words, from ``start`` on, in the ways ``place`` says."""
    at = text.find(q, start)
    if at >= 0:
        return at, at + len(q)
    for inside, marks in ((False, False), (False, True), (True, False)):
        m = _pattern(q, inside_words=inside, marks=marks).search(text, start)
        if m:
            return m.start(), m.end()
    return None


def _parts(text: str, q: str) -> tuple[int, int] | None:
    """A quote with an ellipsis, by its parts in order, each within
    ``PART_GAP`` of the one before; parts shorter than ``MIN_QUOTE`` are
    left out, and at least one must be long enough."""
    parts = [p.strip(_WRAPPING) for p in _ELLIPSIS.split(q)]
    parts = [p for p in parts if len(p) >= MIN_QUOTE]
    if len(parts) < 2:
        return None
    first = _whole(text, parts[0])
    while first is not None:
        end = first[1]
        for part in parts[1:]:
            got = _whole(text, part, end)
            if got is None or got[0] - end > PART_GAP:
                break
            end = got[1]
        else:
            return first[0], end
        first = _whole(text, parts[0], first[0] + 1)
    return None


def place(text: str, quote: str | None) -> tuple[int, int] | None:
    """The ``(start, end)`` of ``quote`` in ``text``: the first place it
    stands, in the ways the module says; None when it is not there, or too
    short or too long to place."""
    q = (quote or "").strip().strip(_WRAPPING).strip()
    if not MIN_QUOTE <= len(q) <= MAX_QUOTE or not text:
        return None
    got = _whole(text, q)
    if got is None and _ELLIPSIS.search(q):
        got = _parts(text, q)
    return got
