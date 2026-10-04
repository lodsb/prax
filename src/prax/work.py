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
import threading
import time
from collections.abc import Iterator
from typing import Any

from prax import models, steps, store
from prax.steps import STEPS, WATCHED_STEPS
from prax.steps.leases import (  # noqa: F401 - the table's names, as callers knew them
    DEFER_SECONDS,
    LEASE_SECONDS,
    _leases,
    defer_reading,
    leased,
    leases,
    reading_deferred,
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
    """The role of ``prax up`` that serves ``step``'s model on this host,
    or None: the role whose ``run`` entry names that model, or serves
    another at the same address. A step may name an entry the host does
    not serve right now while the server on its address does (a model
    under trial, stage AK); its work still waits for that server."""
    with contextlib.suppress(Exception):  # a step off, a file unreadable
        spec = models.resolve(step)
        if spec is None:
            return None
        run = models.load().get("run") or {}
        for role in ("llama-server", "reranker"):
            served = (run.get(role) or {}).get("model")
            if not served:
                continue
            if served == spec.name:
                return role
            other = models.spec(str(served))
            if other is not None and _same_address(other.base_url, spec.base_url):
                return role
    return None


def _same_address(a: str | None, b: str | None) -> bool:
    """Two base URLs that reach one server (a trailing slash aside)."""
    return bool(a and b) and str(a).rstrip("/") == str(b).rstrip("/")


def wanted_steps() -> dict[tuple[str, str], int]:
    """How many deferred items wait now, by role and step."""
    now = time.monotonic()
    out: dict[tuple[str, str], int] = {}
    for (step, i), until in list(_wanted.items()):
        if until < now:
            _wanted.pop((step, i), None)
            continue
        role = role_of_step(step)
        if role:
            out[(role, step)] = out.get((role, step), 0) + 1
    return out


def wanted_roles() -> dict[str, int]:
    """How many deferred items wait on each role now."""
    out: dict[str, int] = {}
    for (role, _step), n in wanted_steps().items():
        out[role] = out.get(role, 0) + n
    return out


# an ask in flight, or one that ended in the last ``ASK_HOLD_SECONDS``,
# keeps the card where it is: a swap never interrupts a streaming answer,
# nor the next question of the same person (stage AI)
ASK_HOLD_SECONDS = 300.0
_asks = {"running": 0, "ended": float("-inf")}
_asks_lock = threading.Lock()


@contextlib.contextmanager
def asking() -> Iterator[None]:
    """Around an answer's generation (``POST /ask``)."""
    with _asks_lock:
        _asks["running"] += 1
    try:
        yield
    finally:
        with _asks_lock:
            _asks["running"] -= 1
            _asks["ended"] = time.monotonic()


def ask_holds() -> bool:
    """Whether an ask holds the card now."""
    with _asks_lock:
        return bool(_asks["running"]) or (
            time.monotonic() - _asks["ended"] < ASK_HOLD_SECONDS
        )


# a person's "do it now" (``POST /work/now``): role -> (action, until).
# The supervisor gives that role the card at its next look at the
# demand, unless an ask holds it. It stands until nothing of it waits
NOW_SECONDS = 6 * 3600.0
_now: dict[str, tuple[str | None, float]] = {}


def do_now(role: str, action: str | None = None) -> dict[str, Any]:
    """Fast-forward what waits for ``role`` (one ``action`` of it, an
    extractor or a step, or all of it): the deferrals of it are let go,
    so the next hand-out offers it, and the demand names the role under
    ``now`` until nothing of it waits."""
    actions = ROLE_WORK.get(role, ())
    wanted = {step for (r, step) in wanted_steps() if r == role}
    if not actions and not wanted:
        raise ValueError(f"nothing waits for {role!r} on this host")
    if action is not None and action not in actions and action not in wanted:
        raise ValueError(f"{role} does not do {action!r}")
    _now[role] = (action, time.monotonic() + NOW_SECONDS)
    if action in wanted:
        released = release_deferred(str(action))
    else:
        released = release_deferred("parse", (action,) if action else actions)
    return {
        "role": role,
        "action": action,
        "released": released,
        "after_ask": ask_holds(),
    }


def _groups(
    readings: dict[str, int],
    rate: dict[str, float],
    left: dict[str, float],
    since: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """What waits, by the role that must run to do it and by action (an
    extractor's readings, or a step's deferred items), with the queue's
    rate where one was measured, when its oldest item was asked for and
    whether a person asked (``store.waiting_since``)."""
    out = [
        {
            "role": role,
            "action": name,
            "waiting": readings[name],
            "rate": rate.get(name),
            "hours_left": left.get(name),
            "oldest": (since.get(name) or {}).get("oldest"),
            "asked_by": (since.get(name) or {}).get("asked_by", "door"),
        }
        for role, names in ROLE_WORK.items()
        for name in names
        if readings.get(name)
    ]
    for (role, step), n in sorted(wanted_steps().items()):
        out.append(
            {
                "role": role,
                "action": step,
                "waiting": n,
                "rate": None,
                "hours_left": None,
                "oldest": None,  # a deferral keeps no moment
                "asked_by": "human" if step == "ask" else "door",
            }
        )
    for g in out:
        asked = _now.get(str(g["role"]))
        g["now"] = asked is not None and asked[0] in (None, g["action"])
    return out


def _settle_now(groups: list[dict[str, Any]]) -> dict[str, str | None]:
    """The standing "do it now"s: one whose work is done, or that is
    older than ``NOW_SECONDS``, is forgotten."""
    clock = time.monotonic()
    for role, (action, until) in list(_now.items()):
        left = [
            g for g in groups if g["role"] == role and action in (None, g["action"])
        ]
        if until < clock or not left:
            _now.pop(role, None)
    return {role: action for role, (action, _until) in _now.items()}


def demand(con: Any) -> dict[str, Any]:
    """What waits, per extractor and per role of ``prax up``: the reading
    requests nobody has taken. A role with a count has work it cannot do
    unless it is running, which is what a swap of the card is for.
    ``groups`` is the same by role and action, for the Jobs view's "do it
    now"; ``now`` the roles a person fast-forwarded (``do_now``), and
    ``ask_holds`` whether an ask keeps the card where it is. The
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
    groups = _groups(readings, rate, left, store.waiting_since(con))
    return {
        "readings": readings,
        "roles": roles,
        "rate": rate,
        "hours_left": left,
        "groups": groups,
        "now": _settle_now(groups),
        "ask_holds": ask_holds(),
    }


# ------------------------------------------------------------------ status

WORKER_SEEN_SECONDS = 120  # a worker asks every 20 s when idle (prax up's interval)


def worker_state() -> dict[str, Any]:
    """Whether a worker is about: one asked the door for work lately, or
    holds a lease now (a long read asks for nothing new for minutes but
    renews its lease). ``last_asked`` is when any step was last asked for
    by this door's workers since it started."""
    asked = [t for t in _asked.values() if t]
    last = max(asked) if asked else None
    recent = False
    if last:
        with contextlib.suppress(ValueError):
            from datetime import UTC, datetime

            then = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            recent = (datetime.now(UTC) - then).total_seconds() < WORKER_SEEN_SECONDS
    working = leases()
    return {"alive": recent or bool(working), "last_asked": last, "working": working}


_COMMIT: dict[str, str | None] = {}


def door_commit() -> str | None:
    """The commit the door runs (the working copy it was started from),
    read once: what tells a client of a newer prax that its door is older."""
    if "c" not in _COMMIT:
        import subprocess

        from prax import config

        try:
            out = subprocess.run(
                ["git", "-C", str(config.REPO_ROOT), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                check=True,
                timeout=10,
            )
            _COMMIT["c"] = out.stdout.decode().strip() or None
        except (OSError, subprocess.SubprocessError):
            _COMMIT["c"] = None
    return _COMMIT["c"]


def status(con: Any, doc_ids: list[int]) -> dict[str, Any]:
    """Where each document is on its way to being read (the first client's
    feedback, 2026-10-03: 24 captures sat for hours, and "no text, nothing
    pending" could not tell "not yet" from "never" from "no worker").

    A document's ``state`` is ``indexed`` (it has text), ``processing``
    (a worker holds it), ``reading`` (it waits for a reading a person or
    the door asked for; ``waits_for`` names it and, when its server is not
    up, the role of ``prax up`` that must be), ``queued`` (a capture the
    parse step will take; ``place`` in the queue), ``nothing found`` (the
    readers ran and found no text: a scan wants a reading, OCR or the
    vision model) or ``failed`` (the last attempt's ``error``). A document
    the caller may not see is not there at all (stage U). ``worker`` says
    whether one is about. A document in ``reading`` says ``searchable``
    when its text is indexed already (a figure reading only adds to it);
    ``door`` names the door's commit, so a client can tell an older door."""
    from prax import parsers
    from prax.capture import inbox
    from prax.parsers import queue

    queued = inbox.pending_captures(con)
    held = leased("parse")
    now = time.monotonic()
    out = []
    for doc_id in doc_ids:
        doc = store.get_document(con, int(doc_id), max_chars=0)
        if doc is None:
            out.append({"doc_id": doc_id, "state": "unknown"})
            continue
        row: dict[str, Any] = {"doc_id": doc_id, "text_len": doc["text_len"]}
        readings = store.pending_readings(con, int(doc_id))
        history = doc["meta"].get("parse_history") or []
        last = history[-1] if history else {}
        if doc_id in held:
            row["state"] = "processing"
        elif readings:
            row["state"] = "reading"
            row["waits_for"] = [r["extractor"] for r in readings]
            # the text a reading adds to may be there already: an agent that
            # read "reading" as "not ready" put off work it could do (O3)
            row["searchable"] = bool(doc["text_len"])
            for r in readings:
                if reading_deferred(r["extractor"], int(doc_id), now):
                    role = next(
                        (n for n, xs in ROLE_WORK.items() if r["extractor"] in xs), None
                    )
                    row["server_down"] = role or r["extractor"]
        elif doc["text_len"]:
            row["state"] = "indexed"
        elif doc_id in queued:
            exts = parsers.candidates(doc["mime"] or "", doc["meta"].get("parser"))
            if exts and any(queue.seen(doc["meta"], e.stamp) for e in exts):
                row["state"] = "nothing found"
                row["why"] = (
                    "the readers ran and found no text; a scan wants a reading"
                    " (OCR or the vision model) asked for on its page"
                )
            else:
                row["state"] = "queued"
                row["place"] = queued.index(doc_id) + 1
        elif last.get("error"):
            row["state"] = "failed"
            row["error"] = str(last["error"])[:300]
        elif history:
            row["state"] = "nothing found"
        else:
            # not a capture: an import's document, read by the backlog pass
            row["state"] = "queued"
            row["why"] = "for the nightly backlog pass (a worker with scope all)"
        if last:
            row["last_attempt"] = {
                k: last[k] for k in ("extractor", "outcome", "at", "error") if k in last
            }
        out.append(row)
    return {"documents": out, "worker": worker_state(), "door": door_commit()}


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
