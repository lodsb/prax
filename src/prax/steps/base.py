"""What a step is: the three halves of one pass, in one object.

The door hands out a batch (``hand_out``), a worker does it and posts the
results (``run``, which fetches, does and posts), and the door applies
them (``take_in``). They used to be three ``if step == …`` chains in two
modules, and a new pass was three edits in the same order with the same
shape (docs/audit/engineering-2026-09-25.md, finding 1). What the
protocol does for every step stays in ``prax.work``: the budget gate,
the leases, what a worker defers, who asked when.

The three shapes that repeat are here once: handing out documents a
batch at a time, applying results a document at a time, and a worker's
pass over a local model.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from prax import models

from . import leases

Log = Callable[[str], None]


@dataclass
class HandOut:
    """What the door knows when a worker asks for a batch."""

    con: Any
    step: str
    limit: int
    scope: str
    worker: str
    now: float

    def free(self, item: int, step: str | None = None) -> bool:
        return leases.free(step or self.step, item, self.now)

    def lease(self, items: list[int], step: str | None = None) -> None:
        leases.lease(step or self.step, items, self.worker)

    def batch(self, items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        """The answer: the items, and how long they are leased."""
        return {
            "step": self.step,
            "items": items,
            "lease_seconds": leases.LEASE_SECONDS,
            **extra,
        }

    def nothing(self, **extra: Any) -> dict[str, Any]:
        """The answer when there is nothing, or the step is off."""
        return {"step": self.step, "items": [], "lease_seconds": 0, **extra}

    def documents(
        self,
        candidates: Iterable[Any],
        build: Callable[[Any], dict[str, Any] | None],
        *,
        scoped: bool = True,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Documents a batch at a time: each candidate that is not leased
        and is in scope becomes an item by ``build`` (None skips it), up
        to the limit, and the batch is leased by document id. A candidate
        is a document id, or a tuple whose first element is one."""
        cap = self.limit if limit is None else limit
        items: list[dict[str, Any]] = []
        for c in candidates:
            doc_id = c[0] if isinstance(c, tuple) else c
            if len(items) >= cap or not self.free(doc_id):
                continue
            if scoped and not leases.in_scope(self.con, doc_id, self.scope):
                continue
            item = build(c)
            if item is not None:
                items.append(item)
        self.lease([i["doc_id"] for i in items])
        return self.batch(items)


@dataclass
class TakeIn:
    """What the door knows when a worker posts results."""

    con: Any
    step: str
    payload: dict[str, Any]
    results: list[dict[str, Any]]
    worker: str
    out: dict[str, Any] = field(default_factory=dict)

    def run(self, prefix: str | None = None) -> str:
        """The batch's run name: what the worker said, or one of the
        step's own stamped now."""
        stamp = time.strftime("%Y%m%dT%H%M%S")
        return str(self.payload.get("run") or f"{prefix or self.step}-{stamp}")

    def release(self, items: list[int], step: str | None = None) -> None:
        leases.release(step or self.step, items)

    def each(
        self,
        apply: Callable[[int, dict[str, Any]], str | None],
        *,
        key: str = "doc_id",
        tried: Callable[[int, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Results a document at a time: released either way, an error
        counted against its item, a ``tried`` result recorded and skipped,
        and ``apply`` for the rest, one failure never stopping the others.
        ``apply`` may name what it did, and the names are counted."""
        actions: dict[str, int] = {}
        for r in self.results:
            item = int(r[key])
            self.release([item])
            if r.get("error"):
                self.out["errors"].append({key: item, "error": r["error"]})
                continue
            if tried is not None and r.get("tried"):
                tried(item, r)
                self.out["skipped"] += 1
                continue
            try:
                action = apply(item, r)
            except Exception as exc:  # noqa: BLE001 - one result must not stop the rest
                self.out["errors"].append(
                    {key: item, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            if action is not None:
                actions[action] = actions.get(action, 0) + 1
            self.out["applied"] += 1
        return actions


@dataclass
class Pass:
    """What a worker's pass knows: the door it asks, and how it was told
    to run."""

    door: Any
    scope: str = "captures"
    limit: int = 10
    workers: int = 3
    session: int | None = None
    spend: bool = False
    log: Log | None = None

    def fetch(self, step: str, **params: Any) -> dict[str, Any]:
        return self.door.get_json(
            f"/work/{step}", {"limit": self.limit, "scope": self.scope, **params}
        )

    def post(self, step: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.door.post_json(f"/work/{step}", body)

    def note(self, text: str, **extra: Any) -> None:
        """A line on the worker's session, where the Jobs view shows it."""
        if self.session:
            self.door.post_json(
                f"/work/session/{self.session}", {"note": text, **extra}
            )

    def say(self, text: str) -> None:
        say(self.log, text)


class Step:
    """One pass. ``run`` returns the line a worker's pass reports for the
    step, or None when there was nothing to do and nothing to say."""

    name: str = ""

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        raise NotImplementedError

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        raise NotImplementedError

    def run(self, p: Pass) -> str | None:
        raise NotImplementedError


def paid_refusal(step: str, spec: models.ModelSpec | None) -> str | None:
    """Why a worker will not run a step whose model costs money, or None
    when it may. Asked before anything is fetched: a batch fetched and
    not done sits leased for a quarter of an hour."""
    if spec is not None and spec.paid:
        return f"skipped: the {step} step is {spec.name} (paid)"
    return None


class ModelStep(Step):
    """A step whose worker asks a local model about each item and posts
    the answers with a run name: titles, summaries, sections, vocabulary.
    What differs is ``do`` and the line ``report`` makes of the reply."""

    def do(self, items: list[dict[str, Any]], runtime: Any, log: Log | None) -> Any:
        raise NotImplementedError

    def report(self, rep: dict[str, Any], results: list[dict[str, Any]]) -> str:
        raise NotImplementedError

    def run(self, p: Pass) -> str | None:
        spec = models.resolve(self.name)
        refused = paid_refusal(self.name, spec)
        if refused:
            return refused
        batch = p.fetch(self.name)
        items = batch.get("items") or []
        if not items:
            return None
        p.note(f"{self.name}: {len(items)} items", total=len(items), done=0)
        runtime = models.runtime(spec) if spec else None
        results = self.do(items, runtime, p.log)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        rep = p.post(self.name, {"results": results, "run": f"{self.name}-{stamp}"})
        return self.report(rep, results)


_log = logging.getLogger("prax.worker")


def say(logger: Log | None, text: str) -> None:
    """A line of a worker's pass: to the caller's logger, or the worker's."""
    if logger:
        logger(text)
    else:
        _log.info(text)
