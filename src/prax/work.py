"""The work protocol: the door hands model work out and takes the results
in, so the batch passes can run on another machine, or on this one,
without opening the database. The door stays the only writer.

Four steps, each a selection the door already knows how to make:

    parse    a capture the door only registered; the worker fetches the
             original (``GET /doc/{id}/original``), runs the extractors,
             posts the text
    titles   a document whose title is a file name; the worker guesses
             with its titles model, posts the title and its confidence
    extract  a document the current ontology has not read; the door
             sends the prepared prompt input, the worker posts the triples
    embed    chunks and document fields without a vector; the worker
             posts the vectors, the door puts them in its delta index

In scope ``all`` the parse step also hands out the documents whose text
an extractor prax has since revised would read differently
(``parsers.behind``), so a backlog pass, run nightly, brings the library
up to date with the code a few documents at a time; a re-read that comes
out the same costs the parse and nothing else.

The readings a person asks for on a document (``meta.reading``) go out
with the parse step, first. The door asks for two kinds itself, for
captures, when the vision model is a local server (nothing spent): an
image to describe, and the figures of a freshly parsed document to read.

A handed-out item carries a lease (``LEASE_SECONDS``): until it expires
no other worker gets it. Leases live in this process; a door restart
forgets them, which costs nothing because every result is idempotent
(a stamp, a bookkeeping row, a replaced vector). ``scope`` is
``captures`` (uploads, dropped files, pages sent from the browser: what
arrives on its own) or ``all`` (the whole library, a backlog pass).
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import asdict
from typing import Any

from prax import (
    extraction,
    models,
    pipeline,
    steps,
    store,
)
from prax.steps import STEPS, WATCHED_STEPS

LEASE_SECONDS = 900
# a worker's "not yet" (the server it needs is loading or down) keeps the
# item leased this long, so the next batches hold other work instead of
# the same ten items again — the follow-ups of the first papers marker
# read once starved the 228 behind them
DEFER_SECONDS = 600
# which role of ``prax up`` a reading waits for: what the door reports as
# demand (``GET /work/demand``) so the supervisor can give it the card
ROLE_WORK = {
    "marker": ("marker",),
    "llama-server": ("figures", "vision", "vision-pages", "formulas", "polish"),
}


SCOPES = ("captures", "all")
MAX_LIMIT = 200

_leases: dict[tuple[str, int], tuple[str, float]] = {}
# when a worker last asked for each step's work, since this door started.
# A queue nobody asks about is the whole answer to "why is this pending"
_asked: dict[str, str] = {}


def asked_at(step: str) -> str | None:
    """When a worker last asked this door for the step's work."""
    return _asked.get(step)


def who_runs(con: Any, step: str) -> dict[str, Any]:
    """Why work for ``step`` may be sitting there untouched, and whether
    it would go badly if it ran.

    A queue is only as awake as the workers asking about it, and three
    things keep one asleep: the step is off on this host, no run names it
    (the paid passes are never in the default set), or the budget is
    spent. To those is added what the model itself says (``models.ready``,
    ``embeddings.ready``), because the resource differs by kind and a
    megabyte figure is the wrong interface for it: Claude wants a key and
    money, a server wants to be loaded and to have a free slot, a GGUF in
    the door's process wants the card.

    ``warn`` is the one worth reading. A model that refuses is visible; a
    model that runs badly is not, and on 2026-09-25 the embedder gave way
    to the CPU without a word and ran at a thirtieth of the speed for
    most of a day.

    The Promote view and the process dialog say all this, rather than
    leaving a document at "pending" with no account of itself."""
    from prax import budget
    from prax.config import ConfigError

    out: dict[str, Any] = {
        "step": step,
        "model": None,
        "paid": False,
        "watched": step in WATCHED_STEPS,
        "asked": _asked.get(step),
        "why": "",
        "warn": "",
        "how": f"prax work --steps {step}",
    }
    if step in models.STEPS:
        try:
            spec = models.resolve(step)
        except ConfigError:  # a config error is the host's, not the page's
            spec = None
        if spec is None:
            out["why"] = f"the {step} step is off on this host (prax.yaml)"
            out["how"] = ""
            return out
        out["model"], out["paid"] = spec.name, spec.paid
        verdict = models.ready(spec)
        out["warn"] = verdict["warn"]
        if not verdict["ok"]:
            out["why"] = verdict["why"]
            out["how"] = verdict["how"]
            return out
    if step == "embed":
        from prax import embeddings

        verdict = embeddings.ready()
        out["model"] = verdict["model"]
        out["warn"] = verdict["warn"]
        if not verdict["ok"]:
            out["why"], out["how"] = verdict["why"], verdict["how"]
            return out
    why = []
    if not out["watched"]:
        why.append(f"no worker asks for {step} work unless the run names it")
    elif out["asked"] is None:
        why.append(f"no worker has asked this door for {step} work")
    if out["paid"]:
        out["how"] += " --spend"
        may, no = budget.allows(con, step)
        why.append(
            no if not may else f"{out['model']} costs money, so the run wants --spend"
        )
    out["why"] = "; ".join(why)
    return out


RATE_HOURS = 3  # the window a queue's rate is measured over


def demand(con: Any) -> dict[str, Any]:
    """What waits, per extractor and per role of ``prax up``: the reading
    requests nobody has taken. A role with a count has work it cannot do
    unless it is running, which is what a swap of the card is for. The
    extraction backlog is left out on purpose: it is never empty, so it
    would say "work waiting" for ever.

    ``rate`` is how many readings finished per hour over the last few,
    and ``hours_left`` what is waiting at that rate. A long queue and a
    stopped one look the same in a count alone, and the figures backlog
    spent a day looking stopped while it was moving at 55 an hour.
    """
    from datetime import UTC, datetime, timedelta

    from prax import store

    readings = store.waiting_readings(con)
    roles = {
        role: sum(readings.get(x, 0) for x in extractors)
        for role, extractors in ROLE_WORK.items()
    }
    since = (datetime.now(UTC) - timedelta(hours=RATE_HOURS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    done = store.readings_done_since(con, since)
    rate = {name: round(n / RATE_HOURS, 1) for name, n in done.items() if n}
    left = {
        name: round(waiting / rate[name], 1)
        for name, waiting in readings.items()
        if rate.get(name)
    }
    return {"readings": readings, "roles": roles, "rate": rate, "hours_left": left}


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
    return len(gone)


def _free(step: str, item: int, now: float) -> bool:
    held = _leases.get((step, item))
    return held is None or held[1] < now


def _lease(
    step: str, items: list[int], worker: str, seconds: float = LEASE_SECONDS
) -> None:
    until = time.monotonic() + seconds
    for i in items:
        _leases[(step, i)] = (worker, until)


def _release(step: str, items: list[int]) -> None:
    for i in items:
        _leases.pop((step, i), None)


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


def _in_scope(con: sqlite3.Connection, doc_id: int, scope: str) -> bool:
    if scope == "all":
        return True
    return store.document_source(con, doc_id) in pipeline.CAPTURE_SOURCES


def _check(step: str, scope: str, limit: int) -> int:
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}; steps are {STEPS}")
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}; scopes are {SCOPES}")
    return max(1, min(int(limit), MAX_LIMIT))


# ---------------------------------------------------------------- hand out


def hand_out(
    con: sqlite3.Connection,
    step: str,
    *,
    limit: int = 10,
    scope: str = "captures",
    worker: str = "worker",
) -> dict[str, Any]:
    """A batch of work for ``step``, leased to ``worker``. A step whose
    model costs money offers nothing once the host's budget for the day
    or the month is spent (``prax.budget``); the reason comes back with
    the empty batch, and the worker says it. What the batch is, is the
    step's (``prax.steps``)."""
    from prax.steps.base import HandOut

    limit = _check(step, scope, limit)
    _asked[step] = store.now()
    from prax import budget

    may, why = budget.allows(con, step)
    if not may:
        return {"step": step, "items": [], "lease_seconds": 0, "held": why}
    h = HandOut(con, step, limit, scope, worker, time.monotonic())
    return steps.get(step).hand_out(h)


# ----------------------------------------------------------------- take in


def _note_spend(
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
    from prax import budget

    with contextlib.suppress(Exception):  # a ledger row is never worth an error
        budget.note(con, step, usage, doc_id=doc_id, run=run, model=model)


def extraction_to_dict(ex: extraction.Extraction) -> dict[str, Any]:
    return {
        "summary": ex.summary,
        "triples": [asdict(t) for t in ex.triples],
        "unmapped": ex.unmapped,
        "usage": ex.usage,
    }


def take_in(
    con: sqlite3.Connection,
    step: str,
    payload: dict[str, Any],
    *,
    worker: str = "worker",
) -> dict[str, Any]:
    """Apply a worker's results for ``step``; the leases go either way.
    What applying them means is the step's (``prax.steps``)."""
    from prax.steps.base import TakeIn

    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}; steps are {STEPS}")
    results = payload.get("results") or []
    out: dict[str, Any] = {"applied": 0, "errors": [], "skipped": 0}
    # "not yet": the item stays leased a while, the queue moves on. An
    # item is a document, or for the vocabulary an entity
    deferred = [int(r.get("doc_id", r.get("id"))) for r in results if r.get("defer")]
    if deferred:
        _lease(step, deferred, worker, seconds=DEFER_SECONDS)
        out["deferred"] = len(deferred)
        results = [r for r in results if not r.get("defer")]
    t = TakeIn(con, step, payload, results, worker, out)
    return steps.get(step).take_in(t)
