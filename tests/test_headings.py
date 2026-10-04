"""A heading path as a hit carries it: capped, so one bad parse does not
swell every answer of its document (invariant 6)."""

from __future__ import annotations

import sqlite3

from prax import store


def test_a_long_level_is_cut_at_a_word_and_marked() -> None:
    long = "Numquam ponenda est pluralitas sine necessitate " * 40
    [got] = store.short_heading([long])
    assert len(got) <= store.HEADING_PART_CHARS + 1 and got.endswith("…")
    assert not got[:-1].endswith(" ")
    assert store.short_heading(["Methods", "Filters"]) == ["Methods", "Filters"]


def test_a_deep_path_keeps_its_nearest_levels() -> None:
    path = ["Book", "Part I", "Chapter 3", "Section 3.2", "Filters"]
    assert store.short_heading(path) == [
        "… Part I",
        "Chapter 3",
        "Section 3.2",
        "Filters",
    ]


def test_a_hit_carries_the_short_heading_and_the_text_stays_whole(
    con: sqlite3.Connection,
) -> None:
    epigraph = "Her sister was called Tatiana and the tender pages of a novel " * 30
    text = f"# {epigraph}\n\nThe zebrafinch sings its song at dawn. " * 3
    doc = store.ingest_text(con, text, title="A parse gone wrong")["doc_id"]
    hits = store.search(con, "zebrafinch", mode="fts")
    hit = next(h for h in hits if h["doc_id"] == doc)
    assert hit["heading"] and len(hit["heading"][0]) <= store.HEADING_PART_CHARS + 1
    chunk = store.get_chunk(con, hit["chunk_id"])
    assert chunk is not None and "zebrafinch" in chunk["text"]
