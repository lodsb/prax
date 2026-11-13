"""A journal as entries (plan item AU, step 2).

A project's journal is one append-only Markdown file of dated entries, some
thousands of lines long. Synced whole, every new entry made the whole file a
new document (invariant 2), so a sync brought 3,400 or 7,600 lines back each
time and an edit to one old entry did the same. ``split`` cuts such a file
into its head (what stands before the first entry) and its entries, which the
sync keeps as documents of their own (``prax.capture.projects``).

An entry is a heading whose words, after an optional ``Session ``, begin
with a date ``YYYY-MM-DD``, an optional letter right after it and an optional
parenthesis (``2026-11-10b``, ``2026-09-16 (late)``, ``Session 2026-11-12:
bring-up``). The heading's level says nothing: a journal's sub-sections are
often ``###`` too. What follows an entry's heading is the entry's, up to the
next entry's heading, whatever headings stand between. A heading whose date
is not first (``Park state 2026-11-10``, ``WHERE THINGS STAND (2026-11-10
…)``) is not an entry and belongs to the one before it.

An entry's ``key`` is its date, letter and parenthesis as written; the words
after them may change without making it another entry. A key that comes
twice in one file is numbered from its second (``2026-11-10~2``).

A file is a journal when it holds ``JOURNAL_MIN`` entries or more: a note
with one dated section is a note. Nothing here imports prax.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from prax.text import markup

JOURNAL_MIN = 3  # entries that make a file a journal rather than a note

ENTRY_HEADING = re.compile(
    r"^(?:session\s+)?"
    r"(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?P<letter>[a-z])?"
    r"(?![\w-])"
    r"(?:\s*\((?P<paren>[^()]*)\))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Entry:
    key: str  # date, letter and parenthesis as written: "2026-09-16 (late)"
    heading: str  # the heading's words
    start: int  # offsets into the file's text; text == file[start:end]
    end: int
    text: str


@dataclass(frozen=True)
class Journal:
    head: str  # what stands before the first entry; "" when nothing does
    entries: list[Entry]


def entry_key(heading: str) -> str | None:
    """The key a heading's words give an entry, or None when they do not
    begin with a date."""
    m = ENTRY_HEADING.match(heading.strip())
    if m is None:
        return None
    key = m.group("date") + (m.group("letter") or "")
    if m.group("paren") is not None:
        key += f" ({m.group('paren').strip()})"
    return key


def split(text: str, *, least: int = JOURNAL_MIN) -> Journal | None:
    """A Markdown text cut into its head and its entries, or None when it
    holds fewer than ``least`` entries. ``head`` and the entries' texts
    together are the text, in order."""
    found: list[tuple[int, str, str]] = []
    for offset, _level, words in markup.headings(text):
        key = entry_key(words)
        if key is not None:
            found.append((offset, key, words))
    if len(found) < least:
        return None
    seen: dict[str, int] = {}
    entries = []
    for i, (start, key, words) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(text)
        seen[key] = seen.get(key, 0) + 1
        unique = key if seen[key] == 1 else f"{key}~{seen[key]}"
        entries.append(Entry(unique, words, start, end, text[start:end]))
    return Journal(text[: found[0][0]], entries)
