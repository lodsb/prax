"""The store's write lock says who held it when a write had to wait, and
which write held it long (``store.base``; AL step 6, the append that
took over 120 s)."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time

import pytest

from prax import store
from prax.store import base


def test_a_long_hold_and_a_long_wait_are_logged(
    con: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(base, "LOCK_WAIT_SLOW", 0.05)
    monkeypatch.setattr(base, "LOCK_HOLD_SLOW", 0.1)

    inside = threading.Event()

    @base._serialized
    def slow_pass(con: sqlite3.Connection) -> None:
        inside.set()  # the lock is held from here: the wait is certain
        time.sleep(0.3)

    holder = threading.Thread(target=slow_pass, args=(con,))
    with caplog.at_level(logging.WARNING, logger="prax.store"):
        holder.start()
        assert inside.wait(5)
        store.ingest_text(con, "a note that had to wait " * 10, title="waiting")
        holder.join()
    said = caplog.text
    assert "slow_pass held the write lock" in said
    assert "waited" in said and "(held by slow_pass)" in said


def test_the_fields_pass_lets_go_between_batches(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prax.store.documents import meta

    for i in range(5):
        store.ingest_text(con, f"document number {i} " * 20, title=f"D{i}")
    calls: list[int] = []
    real = meta._refresh_fields_batch.__wrapped__  # type: ignore[attr-defined]

    @base._serialized
    def counted(con: sqlite3.Connection, ids: list[int]) -> int:
        calls.append(len(ids))
        return real(con, ids)

    monkeypatch.setattr(meta, "FIELD_BATCH", 2)
    monkeypatch.setattr(meta, "_refresh_fields_batch", counted)
    meta.refresh_document_fields(con)
    assert calls == [2, 2, 1]  # three holds of the lock, not one
