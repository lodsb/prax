"""A journal cut into its entries (``prax.text.journal``, plan item AU
step 2)."""

from __future__ import annotations

from prax.text import journal

JOURNAL = """# Bring-up journal

What this file is for.

## 2026-11-08

First day.

### Scope probe

A sub-section of the first day, not an entry.

## Park state 2026-11-09

Still the first entry's: its date is not first.

### Session 2026-11-10b: sensor

The second entry, at another level.

```
## 2026-11-11 inside a fence is no heading
```

## 2026-09-16 (late)

The third.

## 2026-09-16 (late)

The same key again.
"""


def test_entries_are_dated_headings_whatever_their_level() -> None:
    got = journal.split(JOURNAL)
    assert got is not None
    assert [e.key for e in got.entries] == [
        "2026-11-08",
        "2026-11-10b",
        "2026-09-16 (late)",
        "2026-09-16 (late)~2",
    ]
    first = got.entries[0].text
    assert "Scope probe" in first and "Park state" in first
    assert "inside a fence" in got.entries[1].text
    assert got.head == "# Bring-up journal\n\nWhat this file is for.\n\n"
    # the head and the entries are the text, in order
    assert got.head + "".join(e.text for e in got.entries) == JOURNAL
    assert all(JOURNAL[e.start : e.end] == e.text for e in got.entries)


def test_a_key_is_the_date_letter_and_parenthesis_as_written() -> None:
    key = journal.entry_key
    assert key("2026-11-10b — bring-up, take two") == "2026-11-10b"
    assert key("Session 2026-11-12: sensor") == "2026-11-12"
    assert key("2026-09-16 (late) the rest") == "2026-09-16 (late)"
    assert key("WHERE THINGS STAND (2026-11-10 …)") is None
    assert key("Park state 2026-11-10") is None
    assert key("2026-11-1012") is None
    assert key("2026-11-10bis") is None


def test_a_note_with_few_dated_sections_is_no_journal() -> None:
    text = "# Notes\n\n## 2026-11-08\n\nOne.\n\n## 2026-11-09\n\nTwo.\n"
    assert journal.split(text) is None
    assert journal.split(text, least=2) is not None
