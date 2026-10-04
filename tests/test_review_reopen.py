"""A review item dropped by mistake is opened again (the references pass
dropped other passes' items until 2026-10-04)."""

from __future__ import annotations

import sqlite3

from prax import store


def _item(con: sqlite3.Connection, dst: str) -> int:
    return store.queue_review(
        con,
        src="A",
        src_type="paper",
        rel="about",
        dst=dst,
        dst_type="nothing",
        reason="unknown type",
    )


def test_a_dropped_item_opens_again_once(con: sqlite3.Connection) -> None:
    a, b, c = _item(con, "B"), _item(con, "C"), _item(con, "D")
    for i in (a, b):
        store.resolve_review(con, i, "dropped")
    store.resolve_review(con, c, "linked")
    _item(con, "C")  # the same triple queued again since
    assert store.reopen_reviews(con, [a, b, c]) == 1  # c was linked, b is open again
    assert store.get_review(con, a)["resolution"] is None
    assert store.get_review(con, b)["resolution"] == "dropped"
    assert store.get_review(con, c)["resolution"] == "linked"
