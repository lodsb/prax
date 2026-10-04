"""The passes the door runs on itself as jobs, started by a request or by
its clock (``prax.host.schedule``): maintenance, a resolution round, a
backup, the night's slice of unread figures. Each is ``run_as_job``: a
thread with its own connection, the job row carrying progress and error."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from typing import Any

from prax import models, store

# the passes the store does to itself, as the jobs they run under: the
# banner's list, and the Jobs view's (GET /maintenance)
MAINTENANCE = ("maintain", "heal", "resolve", "backup", "figures", "questions")


def run_as_job(
    job: store.Job, work: Callable[[sqlite3.Connection, store.Job], object]
) -> int:
    """Run ``work`` in a thread of its own, on a connection of its own,
    under ``job`` (already made, so the caller can answer with its id at
    once): the job row carries its progress, and its error if it fails."""

    def run() -> None:
        own = store.connect()
        try:
            with store.Job.existing(own, job.id) as mine:
                work(own, mine)
        except Exception:  # the job row carries the error
            logging.getLogger(f"prax.{job.name}").exception("%s failed", job.name)
        finally:
            own.close()

    threading.Thread(target=run, name=job.name, daemon=True).start()
    return int(job.id)


def start_maintain(con: Any, only: list[str] | None) -> dict[str, Any]:
    chosen = [p for p in (only or []) if p] or list(store.PASSES)
    job = store.Job(con, "maintain", note=", ".join(chosen))
    run_as_job(job, lambda own, mine: store.maintain(own, only=chosen, job=mine))
    return {"job": job.id, "passes": chosen}


def start_resolve(
    con: Any, plan: Any = None, *, twins: bool = True, subtypes: bool = True
) -> int:
    """A round of the safe tiers as a job: the sure merges, and the twins
    and subtype folds when asked; the likely pairs stay a person's.

    On the door's clock (``schedule: resolve``) as well as on request.
    Run by hand, the round was run too seldom: 205 sure merges, 59
    subtype folds and 130 twins had piled up by 2026-09-27, the missed
    merges a traverse from a name then showed as several things
    (docs/eval/fractured-names-2026-09-27.md). Each round is one run,
    which ``unmerge_run`` takes back."""
    from prax.graph import resolution

    if plan is None:
        plan = resolution.plan(con, likely=False)
    job = store.Job(
        con,
        "resolve",
        total=len(plan.sure)
        + len(plan.venues)
        + len(plan.editions)
        + (len(plan.subtypes) if subtypes else 0)
        + (len(plan.twins) if twins else 0),
        note=f"{len(plan.sure)} sure" + (f", {len(plan.twins)} twins" if twins else ""),
    )

    def work(own: sqlite3.Connection, mine: store.Job) -> None:
        rep = resolution.apply(own, plan, twins=twins, subtypes=subtypes)
        mine.note(
            f"done: merged {rep.merged_sure} sure,"
            f" {rep.merged_subtypes} subtypes, {rep.merged_twins} twins,"
            f" {rep.merged_venues} venues; {rep.editions} editions linked;"
            f" {len(plan.likely)} likely left for a person"
        )

    return run_as_job(job, work)


def start_backup(con: Any, where: str | None, *, archive: bool) -> dict[str, Any]:
    dest = store.backup_target(where)
    job = store.Job(con, "backup", note=str(dest))
    run_as_job(
        job, lambda own, mine: store.backup(own, dest, archive=archive, job=mine)
    )
    return {"job": job.id, "dest": str(dest)}


FIGURES_SLICE = 150  # documents a night: about three hours of the local model


def start_figures(con: Any, documents: int | None = None) -> dict[str, Any]:
    """The figures backlog, a slice a night (``schedule: figures``).

    13,070 captioned pictures in 1,984 documents had never been read on
    2026-09-27, and asked for at once they would have stood in front of
    every capture's parse for a day and a half: a reading that was asked
    for goes first. So the clock asks for the next ``documents`` whose
    pictures the vision model has not read, oldest first, and only when
    the last slice is done and the model is free. A document whose
    figures are captions with no picture behind them is not asked for:
    no reading changes those, and they are half the figure chunks.
    """
    from prax.capture import pipeline

    n = int(documents or FIGURES_SLICE)  # schedule: figures: {documents: N}
    with store.Job(con, "figures", note=f"a slice of {n}") as job:
        spec = models.resolve("vision")
        if spec is None or not pipeline.vision_is_free():
            job.update(note="the vision model is off or paid: nothing asked")
            return {"job": job.id, "requested": 0, "why": "vision model off or paid"}
        waiting = int(store.waiting_readings(con).get("figures", 0) or 0)
        if waiting:
            job.update(note=f"{waiting} figure readings still waiting: nothing asked")
            return {"job": job.id, "requested": 0, "waiting": waiting}
        chosen = []
        for doc_id in store.select_for_reading(con, unread_figures=True):
            if store.figures_to_read(con, doc_id, model=spec.runtime_name) > 0:
                chosen.append(doc_id)
                if len(chosen) >= n:
                    break
        got = store.request_readings(con, chosen, "figures", by="clock")
        job.update(note=f"asked for {got.get('requested', 0)} of {len(chosen)}")
        return {"job": job.id, **got}
