"""A compound is split where both halves are words the library uses.

German builds nouns by gluing them together, and FTS5 makes one token of
the result: `Apfelkuchen` matches no document in a library that holds 26
with `Apfel` and 27 with `Kuchen`, so for a compound query the keyword
half of the hybrid contributes nothing at all
(`docs/eval/apfelkuchen-2026-09-26.md`). That is 1,988 documents of this
library written in a language whose nouns work that way.

The split needs no word list, for the reason the vocabulary pass needs no
dictionary (rationale R19, "ask the corpus, not the author"): **the
library is the word list.** A compound is split where both halves are
terms the index already holds, which means the parts are words this
library actually uses rather than words a dictionary knows. A German
library grows better at this as it grows; an English one asks and is told
no, at the cost of a lookup.

Nothing here writes, and nothing here is language-specific: the same rule
finds `wavetable` as `wave` + `table` where the library uses both.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

MIN_WORD = 8  # a compound shorter than this is not worth splitting
MIN_PART = 3  # neither half may be shorter than this
MIN_DOCS = 3  # …and each must be a word the library uses, not a typo
MAX_PARTS = 2  # two halves only: three is a guess, and rarely needed
# what German glues between the halves ("Arbeitszimmer", "Zwetschgenröster")
LINKS = ("", "s", "n", "es", "en", "er")

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def split(
    con: sqlite3.Connection, word: str, *, table: str = "chunks_fts"
) -> list[str]:
    """``word`` as the two words the library knows it by, or ``[]``.

    A word the index already holds is returned as nothing to split: it
    stands for itself and needs no help. Otherwise every split point is
    tried, longest first on the left, and the first whose halves are both
    terms of at least ``MIN_DOCS`` documents wins — longest-first because
    `Apfelkuchen` should be `Apfel` + `Kuchen` and not `A` + `pfelkuchen`,
    and `MIN_DOCS` because a library contains typos and a typo is not a
    word.
    """
    low = word.lower()
    if len(low) < MIN_WORD or not _WORD.fullmatch(low):
        return []
    if _docs(con, low, table):  # a term of its own; nothing to do
        return []
    best: list[str] = []
    for cut in range(len(low) - MIN_PART, MIN_PART - 1, -1):
        head = low[:cut]
        if _docs(con, head, table) < MIN_DOCS:
            continue
        for link in LINKS:
            if not low[cut:].startswith(link):
                continue
            tail = low[cut + len(link) :]
            if len(tail) < MIN_PART:
                continue
            if _docs(con, tail, table) >= MIN_DOCS:
                best = [head, tail]
                break
        if best:
            break
    return best


def _docs(con: sqlite3.Connection, term: str, table: str) -> int:
    """How many documents hold this term, through the index rather than a
    scan. 0 where the term is unknown, and 0 where the question cannot be
    asked — a library that will not answer is a library with no split."""
    try:
        row = con.execute(
            f"SELECT COUNT(*) FROM (SELECT rowid FROM {table}"
            f" WHERE {table} MATCH ? LIMIT ?)",
            (f'"{term}"', MIN_DOCS),
        ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def expand(
    con: sqlite3.Connection, tokens: list[str], **kw: Any
) -> dict[str, list[str]]:
    """The tokens of a query that are compounds, with their halves.

    Asked of the keyword side only: a vector already carries a compound's
    sense as well as it carries anything, and it is the FTS half that
    matches nothing. The caller adds the halves as alternatives rather
    than replacing the word, because a library that holds both the
    compound and its parts should rank the compound first.
    """
    out: dict[str, list[str]] = {}
    for t in dict.fromkeys(tokens):
        parts = split(con, t, **kw)
        if parts:
            out[t] = parts
    return out
