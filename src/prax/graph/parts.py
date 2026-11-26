"""A document read in full: its text in parts, one extraction call each.

The extract step reads a document's head and its closing sections
(``extraction.build_input``): 12,000 characters and 4,000 more, which is
most of a short paper and 2% of a book. A document a person asks to be
read in full (``meta.parts.asked``, ``store.ask_parts``) is read again
part by part after that first reading, by the same model and prompt, each
part with the document's header and a line saying which part it is.

A part is a run of the document's chunks in reading order, at most
``part_chars()`` long (``extraction.part_chars`` in prax.yaml, 12,000 by
default, and as many bytes: a script that costs more tokens a character
gets less of it). A new top-level heading starts a new part once the
one being filled holds ``min_part_chars()``
(``extraction.min_part_chars``, 3,000), so a chapter is a part or
several, and short chapters share one. What a reading leaves out of the
head pass is left out here too, and with it the reference list and what
else is set aside (``store.ASIDE_KINDS``), and the front and back matter
by its heading: a reference list's (``references.is_bibliography_heading``,
the whole heading) and the lexicon's ``matter`` section (contents,
index, acknowledgements…, in each language it lists).

The plan is kept with the document (``meta.parts.plan``): the parts'
character ranges in the text, the text's hash and the ontology version
it is read under. A new text or another version makes a new plan and
every part due again; a part is done when its extraction is taken in.
A plan keeps the window it was made with: a changed window plans only
the documents planned after it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from prax import config, models, store
from prax.graph import extraction, ontology
from prax.text import evidence, references

PART_CHARS = extraction.INPUT_CHARS  # the default window
MIN_PART_CHARS = 3_000  # the default fill before a heading starts a part
SKIPPED_KINDS = frozenset(extraction.SKIPPED_KINDS) | frozenset(store.ASIDE_KINDS)
# a book's front and back stretches, where a contents page and an index
# are known by their page numbers too
OUTER = 0.15
# the share of a chunk's words that are page numbers from which it is an
# index or a contents page: the index chunks of the pilot's two books held
# 0.24 to 0.58 (the one under an INDEX heading from 0.24), their prose
# under 0.1 and a page of bar numbers 0.23. In a sample of 1,500 open
# documents (114,314 chunks), 1,060 chunks at 0.3 or more stood in the
# last 15% and 172 before it, 34 of those in the first 15% (contents and
# copyright pages) and the rest in the body, among them pitch-class sets
# and worked examples: hence the outer stretches only (2026-11-26)
INDEX_SHARE = 0.3
# an index runs on over its neighbours at this share, counted in a chunk of
# any length: the jazz counterpoint book's index lay in chunks of 0.24 to
# 0.40, and those under 0.3 and the short ones were read as a part with
# the back cover; the prose beside an index held 0.0 to 0.06 (2026-11-26)
INDEX_RUN_SHARE = 0.15


def part_chars() -> int:
    """The most characters of a part (``extraction.part_chars``): what
    fits the host's model beside its prompt. A longer context reads a
    book in fewer parts."""
    chars = int(config.number("extraction.part_chars", "PRAX_PART_CHARS", PART_CHARS))
    if chars < 1_000:
        raise config.ConfigError(f"extraction.part_chars: {chars} is under 1,000")
    return chars


def min_part_chars() -> int:
    """How much a part holds before a new top-level heading starts the
    next one (``extraction.min_part_chars``); not measured yet."""
    return int(
        config.number(
            "extraction.min_part_chars", "PRAX_MIN_PART_CHARS", MIN_PART_CHARS
        )
    )


def matter_headings() -> frozenset[str]:
    """The headings of front and back matter (the lexicon's ``matter``),
    lower case: read once a plan or a part, not once a chunk."""
    section = ontology.lexicon().section("matter") or {}
    return frozenset(_plain_heading(str(w)) for w in section.get("headings") or ())


def _plain_heading(h: str) -> str:
    return " ".join(h.strip(" \t#*_.:0123456789").lower().split())


@dataclass(frozen=True)
class Part:
    start: int  # character range in the text artifact
    end: int
    label: str  # the heading path where it begins


def _matter(heading: list[str], words: frozenset[str], *, outer: bool = False) -> bool:
    """Whether a chunk's heading path opens front or back matter: its top
    two levels anywhere, and any level in the book's outer stretches,
    where a PDF's index heading sits under the last chapter's ("…
    Ostinato Groove / … / INDEX", 2026-11-26) and where a section called
    "Index" in a database book's body does not."""
    return any(
        _plain_heading(h) in words or references.is_bibliography_heading(h, whole=True)
        for h in (heading if outer else heading[:2])
    )


class _Matter:
    """What a document's reading in full leaves out, chunk by chunk: the
    left-out kinds, empty text, matter by its heading, and in the book's
    outer stretches (``OUTER`` of its length at either end) a contents
    page or an index by its page numbers (``evidence.contents_share`` at
    ``INDEX_SHARE`` or more, and the run of chunks on either side at
    ``INDEX_RUN_SHARE``). Not in the body: a music paper's pitch-class
    sets and a worked example are as dense in numbers there."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.words = matter_headings()
        ends = [int((c.get("locator") or {}).get("char_end") or 0) for c in chunks]
        self.length = max(ends, default=0) or 1
        self.index = self._index_runs(chunks)

    def _outer(self, c: dict[str, Any]) -> bool:
        at = int(c["locator"]["char_start"]) / self.length
        return at < OUTER or at >= 1 - OUTER

    def _index_runs(self, chunks: list[dict[str, Any]]) -> frozenset[int]:
        """The starts of the chunks of an index or a contents page: one at
        ``INDEX_SHARE`` in an outer stretch, and its neighbours there at
        ``INDEX_RUN_SHARE`` or more, short ones too. A left-out kind is
        passed over, prose ends the run."""
        seq = sorted(
            (
                c
                for c in chunks
                if c.get("kind") not in SKIPPED_KINDS
                and c["text"].strip()
                and "char_start" in (c.get("locator") or {})
            ),
            key=lambda c: int(c["locator"]["char_start"]),
        )
        outer = [self._outer(c) for c in seq]
        seeds = [
            i
            for i, c in enumerate(seq)
            if outer[i] and evidence.contents_share(c["text"]) >= INDEX_SHARE
        ]
        marked = set(seeds)
        for i in seeds:
            for step in (-1, 1):
                j = i + step
                while (
                    0 <= j < len(seq)
                    and j not in marked
                    and outer[j]
                    and evidence.contents_share(seq[j]["text"], min_words=1)
                    >= INDEX_RUN_SHARE
                ):
                    marked.add(j)
                    j += step
        return frozenset(int(seq[i]["locator"]["char_start"]) for i in marked)

    def left_out(self, c: dict[str, Any]) -> bool:
        loc = c.get("locator") or {}
        if c.get("kind") in SKIPPED_KINDS or not c["text"].strip():
            return True
        if "char_start" not in loc:
            return True
        if _matter(list(c.get("heading") or []), self.words, outer=self._outer(c)):
            return True
        return int(loc["char_start"]) in self.index


def _size(text: str) -> int:
    return max(len(text), len(text.encode("utf-8")))


def plan(
    chunks: list[dict[str, Any]],
    *,
    window: int | None = None,
    min_chars: int | None = None,
) -> list[Part]:
    """The parts of a document from its chunks (``store.list_chunks``),
    by the host's window and fill unless they are given."""
    window = window or part_chars()
    min_chars = min_part_chars() if min_chars is None else min_chars
    matter = _Matter(chunks)
    parts: list[Part] = []
    start = end = -1
    label = ""
    top: str | None = None
    used = 0

    def close() -> None:
        if start >= 0 and end > start:
            parts.append(Part(start, end, label))

    for c in chunks:
        loc = c.get("locator") or {}
        heading = list(c.get("heading") or [])
        if matter.left_out(c):
            continue
        size = _size(c["text"]) + 2
        head = heading[0] if heading else None
        new_chapter = head != top and used >= min_chars
        if start < 0 or used + size > window or new_chapter:
            close()
            start, used = int(loc["char_start"]), 0
            label = " > ".join(heading[:2])
        top = head
        end = int(loc["char_end"])
        used += size
    close()
    return parts


def held_plan(record: dict[str, Any], text_hash: str | None, version: str) -> bool:
    """Whether the kept plan is the one for this text and version."""
    held = record.get("plan") or {}
    return bool(held) and (
        held.get("text_hash") == text_hash and held.get("version") == version
    )


def next_part(record: dict[str, Any]) -> int | None:
    """The first part not yet read, or None when every one is."""
    done = set(record.get("done") or [])
    for i in range(len((record.get("plan") or {}).get("parts") or [])):
        if i not in done:
            return i
    return None


def part_text(
    chunks: list[dict[str, Any]], start: int, end: int, *, window: int | None = None
) -> str:
    """The text of a part: the chunks that begin in ``[start, end)``, the
    left-out kinds and matter skipped, within the window and as many
    bytes. A chunk belongs to the part its start falls in, and a caller
    passes the next part's start as ``end`` (``span``), so after a
    rechunk (the same text, other boundaries) no chunk falls between two
    parts."""
    window = window or part_chars()
    matter = _Matter(chunks)
    pieces: list[str] = []
    used = 0
    for c in chunks:
        loc = c.get("locator") or {}
        if "char_start" not in loc or not start <= loc["char_start"] < end:
            continue
        if matter.left_out(c):
            continue
        piece = c["text"].strip()
        room = max(0, window - used)
        if _size(piece) > room:
            piece = models.trim_guessed(piece, chars=room, byts=room)
        if piece:
            pieces.append(piece)
            used += _size(piece) + 2
        if used >= window:
            break
    return "\n\n".join(pieces)


def span(plan: dict[str, Any], index: int) -> tuple[int, int]:
    """Where a part's chunks may begin: from its start to the next part's
    start (the last part to its own end, past which is only matter)."""
    ranges = plan["parts"]
    start, end = int(ranges[index][0]), int(ranges[index][1])
    if index + 1 < len(ranges):
        end = max(end, int(ranges[index + 1][0]))
    return start, end


def part_header(header: str, index: int, count: int, label: str) -> str:
    """The document's header and the line that says which part this is,
    and what the model is to take from it."""
    where = f" ({label})" if label else ""
    return (
        f"{header}\n"
        f"Part: {index + 1} of {count}{where}. The text below is this part of"
        " the document only; its title, authors and venue are known already."
        " Extract what this part states: its concepts, methods, techniques,"
        " tools, works and the relations between them."
    )


def prepared(
    con: sqlite3.Connection, doc_id: int
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]] | None:
    """The document (``store.get_document``, no text), its parts record
    with a plan for its current text and version, written when it was
    missing or stale (every part due again), and its chunks; None when
    it has no text or no part left."""
    doc = store.get_document(con, doc_id, max_chars=0)
    if doc is None or not doc.get("text_hash"):
        return None
    meta = doc["meta"] or {}
    record = dict(meta.get("parts") or {})
    version = store.expected_version(meta)
    chunks = store.list_chunks(con, doc_id)
    if not held_plan(record, doc["text_hash"], version):
        chars = part_chars()
        parts = plan(chunks, window=chars)
        record = store.set_parts_plan(
            con,
            doc_id,
            {
                "text_hash": doc["text_hash"],
                "version": version,
                "window": chars,
                "parts": [[p.start, p.end, p.label] for p in parts],
            },
        )
    if next_part(record) is None:
        return None
    return dict(doc), record, chunks
