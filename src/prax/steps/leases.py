"""Who holds which item of a step's work, and until when: the door's lease
table. A batch handed out is leased to the worker that asked, so the next
worker gets other work; the lease ends when the result comes back, or
after ``LEASE_SECONDS`` if it never does. A worker's "not yet" (its server
is loading) keeps the items ``DEFER_SECONDS`` instead.

Here rather than in ``prax.work`` because the steps use it and ``work``
uses the steps: the table below both, so neither imports the other back.
The door's process holds it in memory; a restart forgets every lease,
which hands the items out again, as it should.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from typing import Any

from prax import store

LEASE_SECONDS = 900
# a worker's "not yet" (the server it needs is loading or down) keeps the
# item leased this long, so the next batches hold other work instead of
# the same ten items again — the follow-ups of the first papers marker
# read once starved the 228 behind them
DEFER_SECONDS = 600

# (step, item) -> (worker, until), in time.monotonic()
_leases: dict[tuple[str, int], tuple[str, float]] = {}
# (extractor, document) -> until: a reading whose server was not there.
# Only that reading waits, not the document: a marker reading deferred by
# leasing the whole document kept the document's two vision-pages
# readings out of every batch for two days (2026-09-30 to 10-01)
_readings: dict[tuple[str, int], float] = {}


def defer_reading(extractor: str, doc_id: int, seconds: float = DEFER_SECONDS) -> None:
    _readings[(extractor, int(doc_id))] = time.monotonic() + seconds


def reading_deferred(extractor: str, doc_id: int, now: float) -> bool:
    until = _readings.get((extractor, int(doc_id)))
    return until is not None and until > now


def free(step: str, item: int, now: float) -> bool:
    held = _leases.get((step, item))
    return held is None or held[1] < now


def lease(
    step: str, items: list[int], worker: str, seconds: float = LEASE_SECONDS
) -> None:
    until = time.monotonic() + seconds
    for i in items:
        _leases[(step, i)] = (worker, until)


def release(step: str, items: list[int]) -> None:
    for i in items:
        _leases.pop((step, i), None)


def leased(step: str) -> set[int]:
    """The items of ``step`` a worker holds now."""
    now = time.monotonic()
    return {
        i for (s, i), (_w, until) in list(_leases.items()) if s == step and until >= now
    }


def renew(step: str, items: list[int], worker: str) -> int:
    """The worker holding those items keeps them another ``LEASE_SECONDS``:
    a book takes marker longer than one lease, and the worker beats while
    it reads. An item leased to another worker, or free, is left alone.
    Returns how many were renewed."""
    n = 0
    until = time.monotonic() + LEASE_SECONDS
    for i in items:
        held = _leases.get((step, i))
        if held is not None and held[0] == worker:
            _leases[(step, i)] = (worker, until)
            n += 1
    return n


def leases() -> dict[str, int]:
    """How many items are out per step (for the status view)."""
    now = time.monotonic()
    out: dict[str, int] = {}
    for (step, _), (_, until) in _leases.items():
        if until >= now:
            out[step] = out.get(step, 0) + 1
    return out


def release_deferred(step: str, extractors: tuple[str, ...] = ()) -> int:
    """Forget the "not yet" deferrals of a step, so the next hand-out
    offers those items again: what the door does when the server they
    waited for has just been given the card. Returns how many were
    released."""
    now = time.monotonic()
    gone = [
        key
        for key, (_worker, until) in list(_leases.items())
        if key[0] == step and until > now
    ]
    for key in gone:
        _leases.pop(key, None)
    if step == "parse":
        held = [
            k
            for k, until in list(_readings.items())
            if until > now and (not extractors or k[0] in extractors)
        ]
        for k in held:
            _readings.pop(k, None)
        gone += held
    return len(gone)


def in_scope(con: sqlite3.Connection, doc_id: int, scope: str) -> bool:
    """Whether a document is in a hand-out's scope: every document, or only
    the captures."""
    if scope == "all":
        return True
    return store.document_source(con, doc_id) in store.CAPTURE_SOURCES


def note_spend(
    con: sqlite3.Connection,
    step: str,
    usage: dict[str, Any] | None,
    *,
    doc_id: int | None = None,
    run: str | None = None,
    model: str | None = None,
) -> None:
    """What a worker's paid call cost, into the ledger. The worker never
    writes to the store; the door records what comes back through it.

    ``model`` is what the worker says it ran, which is not always what
    this host's config resolves the step to — a worker started against
    another config is exactly how an unrecorded bill happens — so the
    reported name is what the row carries."""
    from prax.ml import budget

    with contextlib.suppress(Exception):  # a ledger row is never worth an error
        budget.note(con, step, usage, doc_id=doc_id, run=run, model=model)
