"""What is current (stage AL, step 5): a stale document ranks lower and
names its replacement; never a filter."""

from __future__ import annotations

import sqlite3

from prax import store
from prax.answering import ask


def _two(con: sqlite3.Connection) -> tuple[int, int]:
    old = store.ingest_text(
        con,
        "oscillator tuning table oscillator tuning numbers oscillator " * 8,
        title="Tuning v1",
    )["doc_id"]
    new = store.ingest_text(
        con, "the oscillator tuning, corrected " * 6, title="Tuning v2"
    )["doc_id"]
    return old, new


def _order(con: sqlite3.Connection, **kw: object) -> list[int]:
    return [
        h["doc_id"] for h in store.search(con, "oscillator tuning", 5, mode="fts", **kw)
    ]


def test_a_superseded_document_ranks_lower_and_names_its_replacement(
    con: sqlite3.Connection,
) -> None:
    old, new = _two(con)
    assert _order(con) == [old, new]  # the old one says it more often
    store.link(
        con,
        store.Edge("Tuning v2", "document", "supersedes", "Tuning v1", "document"),
        source_doc=new,
        evidence="replaces the table of v1",
        producer="test",
        world_from="2026-10-02",
    )
    hits = store.search(con, "oscillator tuning", 5, mode="fts")
    assert [h["doc_id"] for h in hits] == [new, old]  # lower, still there
    stale = hits[1]["stale"]
    assert (stale["state"], stale["since"], stale["by"]) == (
        "superseded",
        "2026-10-02",
        "graph",
    )
    assert stale["replaced_by"] == [{"doc_id": new, "title": "Tuning v2"}]
    assert "stale" not in hits[0]
    # the plain order on request, the mark kept
    plain = store.search(con, "oscillator tuning", 5, mode="fts", include_stale=True)
    assert [h["doc_id"] for h in plain] == [old, new] and plain[0]["stale"]


def test_its_own_status_is_enough(con: sqlite3.Connection) -> None:
    old, new = _two(con)
    meta = store.get_meta(con, old)
    meta["status"] = {
        "state": "retired",
        "since": "2026-09",
        "by": "sync",
        "words": "Retired",
    }
    store.set_meta(con, old, meta)
    hits = store.search(con, "oscillator tuning", 5, mode="fts")
    assert [h["doc_id"] for h in hits] == [new, old]
    assert (
        hits[1]["stale"]["state"] == "retired" and hits[1]["stale"]["replaced_by"] == []
    )
    # a current status ranks as any document
    meta["status"] = {"state": "current", "by": "sync"}
    store.set_meta(con, old, meta)
    assert _order(con) == [old, new]


def test_the_answering_model_is_told() -> None:
    p = ask.Passage(
        n=1,
        doc_id=3,
        chunk_id=None,
        title="Tuning v1",
        heading=[],
        page=None,
        kind="text",
        text="…",
        stale={
            "state": "superseded",
            "since": "2026-10-02",
            "replaced_by": [{"doc_id": 4, "title": "Tuning v2"}],
        },
    )
    assert "[no longer current: superseded 2026-10-02 by Tuning v2]" in p.label()
    assert p.to_dict()["stale"]["state"] == "superseded"
