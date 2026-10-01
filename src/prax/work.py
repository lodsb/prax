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
from typing import Any

from prax import models, steps, store
from prax.steps import STEPS, WATCHED_STEPS
from prax.steps.leases import (  # noqa: F401 - the table's names, as callers knew them
    DEFER_SECONDS,
    LEASE_SECONDS,
    _leases,
    defer_reading,
    leases,
    release_deferred,
    renew,
)
from prax.steps.leases import lease as _lease
from prax.steps.leases import release as release_lease

# which role of ``prax up`` a reading waits for: what the door reports as
# demand (``GET /work/demand``) so the supervisor can give it the card
ROLE_WORK = {
    "marker": ("marker",),
    "llama-server": ("figures", "vision", "vision-pages", "formulas", "polish"),
}


SCOPES = ("captures", "all")
MAX_LIMIT = 200

# what was deferred because its model server was not there, until when:
# the demand that brings an idle server back (``demand``, ``prax up``'s
# ``idle_minutes``). An item leaves it when its result arrives
_wanted: dict[tuple[str, int], float] = {}
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
    from prax.config import ConfigError
    from prax.ml import budget

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
        from prax.ml import embeddings

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


def want(step: str, items: list[int] | None = None) -> None:
    """Work of ``step`` waited for a model server that was not there (a
    worker's "not yet", an ask that found it gone): it counts as demand
    for the role that serves the step's model for ``DEFER_SECONDS``."""
    until = time.monotonic() + DEFER_SECONDS
    for i in items or [0]:
        _wanted[(step, int(i))] = until


def role_of_step(step: str) -> str | None:
    """The role of ``prax up`` that serves ``step``'s model on this host
    (``run.llama-server.model`` names it), or None."""
    with contextlib.suppress(Exception):  # a step off, a file unreadable
        spec = models.resolve(step)
        run = models.load().get("run") or {}
        for role in ("llama-server", "reranker"):
            opts = run.get(role) or {}
            if spec is not None and opts.get("model") == spec.name:
                return role
    return None


def wanted_roles() -> dict[str, int]:
    """How many deferred items wait on each role now."""
    now = time.monotonic()
    out: dict[str, int] = {}
    for (step, i), until in list(_wanted.items()):
        if until < now:
            _wanted.pop((step, i), None)
            continue
        role = role_of_step(step)
        if role:
            out[role] = out.get(role, 0) + 1
    return out


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
    for role, n in wanted_roles().items():  # what found its server gone
        roles[role] = roles.get(role, 0) + n
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
    or the month is spent (``prax.ml.budget``); the reason comes back with
    the empty batch, and the worker says it. What the batch is, is the
    step's (``prax.steps``)."""
    from prax.steps.base import HandOut

    limit = _check(step, scope, limit)
    _asked[step] = store.now()
    from prax.ml import budget

    may, why = budget.allows(con, step)
    if not may:
        return {"step": step, "items": [], "lease_seconds": 0, "held": why}
    h = HandOut(con, step, limit, scope, worker, time.monotonic())
    return steps.get(step).hand_out(h)


# ----------------------------------------------------------------- take in


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
    # a reading says which: only it waits, the document's others go on
    for r in results:
        if r.get("defer") and r.get("extractor"):
            defer_reading(str(r["extractor"]), int(r["doc_id"]))
            release_lease(step, [int(r["doc_id"])])
    results = [r for r in results if not (r.get("defer") and r.get("extractor"))]
    deferred = [int(r.get("doc_id", r.get("id"))) for r in results if r.get("defer")]
    if deferred:
        _lease(step, deferred, worker, seconds=DEFER_SECONDS)
        want(step, deferred)
        out["deferred"] = len(deferred)
        results = [r for r in results if not r.get("defer")]
    for r in results:
        with contextlib.suppress(TypeError, ValueError):
            _wanted.pop((step, int(r.get("doc_id", r.get("id")))), None)
    t = TakeIn(con, step, payload, results, worker, out)
    return steps.get(step).take_in(t)
