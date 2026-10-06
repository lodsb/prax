"""Where a quote stands in a text: the character range of an edge's
evidence in the text artifact it was read from (migration 48).

A model's quote is the text's own words, but not always its own
characters: it rewraps a line, drops a hyphen at a line's end, or folds
two spaces into one. So the quote is looked for as written first; then
as a pattern over the text itself, a run of whitespace in the quote
matching any run in the text, regardless of case; and last with a hyphen
at a line's end allowed between any two letters. The text is never
copied or folded: a book is megabytes, and a document's quotes are
hundreds. A quote not found is None, never a guess.
"""

from __future__ import annotations

import re

MIN_QUOTE = 8  # a shorter quote is found anywhere and places nothing
MAX_QUOTE = 600  # a longer one is no quote, and its pattern would be slow
_SPACE = r"(?:-[ \t]*\n\s*|\s+)"  # a run of whitespace, or a hyphen ending a line
_BROKEN = r"(?:-[ \t]*\n\s*)?"  # a hyphen that ends a line, inside a word


def _pattern(quote: str, *, inside_words: bool) -> re.Pattern[str]:
    words = quote.split()
    if inside_words:
        words = [_BROKEN.join(re.escape(ch) for ch in w) for w in words]
    else:
        words = [re.escape(w) for w in words]
    return re.compile(_SPACE.join(words), re.IGNORECASE)


def place(text: str, quote: str | None) -> tuple[int, int] | None:
    """The ``(start, end)`` of ``quote`` in ``text``: the first place it
    stands, as written, with its whitespace and case loose, or with a
    line-end hyphen inside a word; None when it is not there, or too short
    or too long to place."""
    q = (quote or "").strip()
    if not MIN_QUOTE <= len(q) <= MAX_QUOTE or not text:
        return None
    at = text.find(q)
    if at >= 0:
        return at, at + len(q)
    for inside in (False, True):
        m = _pattern(q, inside_words=inside).search(text)
        if m:
            return m.start(), m.end()
    return None
