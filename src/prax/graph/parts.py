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
from prax.text import references

PART_CHARS = extraction.INPUT_CHARS  # the default window
MIN_PART_CHARS = 3_000  # the default fill before a heading starts a part
SKIPPED_KINDS = frozenset(extraction.SKIPPED_KINDS) | frozenset(store.ASIDE_KINDS)


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


def _matter(heading: list[str], words: frozenset[str]) -> bool:
    """Whether a chunk's heading path opens front or back matter."""
    return any(
        _plain_heading(h) in words or references.is_bibliography_heading(h, whole=True)
        for h in heading[:2]
    )


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
    matter = matter_headings()
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
        if c.get("kind") in SKIPPED_KINDS or not c["text"].strip():
            continue
        if "char_start" not in loc or _matter(heading, matter):
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
    matter = matter_headings()
    pieces: list[str] = []
    used = 0
    for c in chunks:
        loc = c.get("locator") or {}
        if c.get("kind") in SKIPPED_KINDS or "char_start" not in loc:
            continue
        if not start <= loc["char_start"] < end:
            continue
        if _matter(list(c.get("heading") or []), matter):
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
