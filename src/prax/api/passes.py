"""The passes the door runs on itself as jobs, started by a request or by
its clock (``prax.host.schedule``): maintenance, a resolution round, a
backup, the night's slices of unread figures and of the fixes the text
checks name. Each is ``run_as_job``: a
thread with its own connection, the job row carrying progress and error.
``JOBS`` at the end is the one list of them: the clock's starters, the
banner's names and the Jobs view are read from it."""

from __future__ import annotations

import contextvars
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from prax import models, store


def run_as_job(
    job: store.Job,
    work: Callable[[sqlite3.Connection, store.Job], object],
    *,
    done: Callable[[], None] | None = None,
) -> int:
    """Run ``work`` in a thread of its own, on a connection of its own,
    under ``job`` (already made, so the caller can answer with its id at
    once): the job row carries its progress, and its error if it fails.
    The thread runs in a copy of the caller's context, so a viewer the
    request set (``store.VIEWER``) holds in the job too; ``done`` is
    called at the end whatever happened."""

    def run() -> None:
        try:
            own = store.connect()
            try:
                with store.Job.existing(own, job.id) as mine:
                    work(own, mine)
            except Exception:  # the job row carries the error
                logging.getLogger(f"prax.{job.name}").exception("%s failed", job.name)
            finally:
                own.close()
        finally:
            if done is not None:
                done()

    context = contextvars.copy_context()
    threading.Thread(
        target=context.run, args=(run,), name=job.name, daemon=True
    ).start()
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
        if spec is None or not pipeline.free("vision"):
            job.update(note="the vision model is off or paid: nothing asked")
            return {"job": job.id, "requested": 0, "why": "vision model off or paid"}
        waiting = int(store.waiting_readings(con).get("figures", 0) or 0)
        if waiting:
            job.update(note=f"{waiting} figure readings still waiting: nothing asked")
            return {"job": job.id, "requested": 0, "waiting": waiting}
        chosen = []
        tried = store.tried_on_this_text(con, "figures")
        for doc_id in store.select_for_reading(con, unread_figures=True):
            if doc_id in tried:
                continue  # its pictures are not in the original: no night helps
            if store.figures_to_read(con, doc_id, model=spec.runtime_name) > 0:
                chosen.append(doc_id)
                if len(chosen) >= n:
                    break
        got = store.request_readings(con, chosen, "figures", by="clock")
        job.update(note=f"asked for {got.get('requested', 0)} of {len(chosen)}")
        return {"job": job.id, **got}


FIXES_SLICE = 200  # documents a reading a night: marker read 529 in one


def request_fixes(
    con: Any,
    fixes: Any,
    *,
    by: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Each document asked for the reading its fix names, in the mode this
    host gives it (``quality.reading``): ``fixes`` is ``(doc_id, Fix)``
    pairs, one a document. The clock's slice and the Admin page's button
    (``POST /readings/bulk`` with ``finding``) both ask through here.
    Returns ``requested`` by extractor and how many were ``skipped``."""
    from prax.parsers import quality

    groups: dict[tuple[str, str | None], list[int]] = {}
    for doc_id, fix in fixes:
        groups.setdefault(quality.reading(fix), []).append(doc_id)
    return request_groups(con, groups, by=by, dry_run=dry_run)


def request_groups(
    con: Any,
    groups: dict[tuple[str, str | None], list[int]],
    *,
    by: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One request of many documents per reading and mode: ``requested``
    by extractor and how many were ``skipped`` (waiting already)."""
    asked: dict[str, int] = {}
    skipped = 0
    for (extractor, mode), ids in groups.items():
        got = store.request_readings(
            con, ids, extractor, mode=mode, by=by, dry_run=dry_run
        )
        asked[extractor] = asked.get(extractor, 0) + int(got["requested"])
        skipped += int(got["skipped"])
    return {"requested": asked, "skipped": skipped}


def start_fixes(con: Any, documents: int | None = None) -> dict[str, Any]:
    """The readings the text checks name as fixes, a slice a night
    (``schedule: fixes``; stage AO).

    A new text gets its fix when it lands (``pipeline.follow_ups``); the
    texts read before a check existed wait. On 2026-11-05: 203 scans read
    from their OCR layer (marker) and 10 documents with captions and no
    picture (the crops). Asked by hand in batches until then. So the clock
    asks, for each reading the findings name, the next ``documents``
    oldest first, in the mode this host gives it (``quality.reading``),
    and nothing for a reading while any of it still waits. It passes by a
    document already waiting for that reading, and one whose last reading
    of it failed or changed nothing on the text it holds
    (``store.tried_on_this_text``): a scan's finding still names marker
    after a marker read that came out shorter than its OCR layer, and a
    reading asked for again on the same text comes out the same. A finding
    no reading fixes (leftovers, a short text) stays on the Admin page's
    "text checks" list for a person.
    """
    from prax.parsers import quality

    n = int(documents or FIXES_SLICE)  # schedule: fixes: {documents: N}
    with store.Job(con, "fixes", note=f"a slice of {n} a reading") as job:
        waiting = store.waiting_readings(con)
        # a reading a document, in one mode: two findings naming marker
        # are one read, and the one that must read every page itself wins
        chosen: dict[str, dict[int, quality.Fix]] = {}
        held: dict[str, int] = {}
        tried: dict[str, frozenset[int]] = {}
        for doc_id, _check, kept in store.finding_fixes(con):
            fix = quality.Fix.of(kept)
            if fix is None:
                continue
            extractor = fix.extractor
            if waiting.get(extractor):
                held[extractor] = int(waiting[extractor])
                continue
            if extractor not in tried:
                tried[extractor] = store.tried_on_this_text(con, extractor)
            if doc_id in tried[extractor]:
                continue
            these = chosen.setdefault(extractor, {})
            if doc_id in these or len(these) < n:
                these[doc_id] = quality.stronger(these.get(doc_id), fix)
        got = request_fixes(
            con,
            [(d, fix) for these in chosen.values() for d, fix in these.items()],
            by="clock",
        )
        asked, skipped = got["requested"], got["skipped"]
        said = ", ".join(f"{k} {v}" for k, v in sorted(asked.items())) or "nothing"
        if held:
            said += "; still waiting: " + ", ".join(
                f"{k} {v}" for k, v in sorted(held.items())
            )
        job.update(note=f"asked for {said}")
        return {"job": job.id, "requested": asked, "skipped": skipped, "waiting": held}


# what the questions pass has in hand: the ``only`` of each running job
# ("" for every question, a slug, a slug#id) and its job id, so a page
# and a listing can say "asking…" instead of leaving a click unanswered,
# and a second click while the first runs joins that job instead of
# starting another (a run is a model call; a page once took twenty-six
# in four minutes from one person's clicks)
_answering: dict[str, int] = {}
_answering_lock = threading.Lock()


def start_questions(
    con: Any,
    *,
    only: str | None = None,
    force: bool = False,
    briefing: bool = True,
    release: bool = False,
) -> dict[str, Any]:
    """The standing questions asked again where the library learned
    something, then the day's briefing (``schedule: questions``); one job
    a key at a time, a second start joining the running one."""
    from prax.answering import questions

    key = only or ""
    with _answering_lock:
        running = _answering.get(key)
        if running is not None:
            return {"job": running, "running": True}
        job = store.Job(con, "questions", note=only or "every question")
        _answering[key] = job.id

    def work(own: sqlite3.Connection, mine: store.Job) -> None:
        rep = questions.refresh_all(
            own, force=force, only=only, job=mine, release=release
        )
        note = f"{rep['refreshed']} of {rep['checked']} asked again"
        if briefing:
            b = questions.briefing(own, job=mine)
            note += f"; briefing: {b['documents']} documents"
        mine.update(note=note)

    def release_key() -> None:
        with _answering_lock:
            _answering.pop(key, None)

    try:
        run_as_job(job, work, done=release_key)
    except Exception:
        release_key()
        raise
    return {"job": job.id}


@dataclass(frozen=True)
class DoorJob:
    """A pass the door runs on itself as a job. ``clock`` begins it at
    its hour (``schedule:``, ``prax.host.schedule.NAMES``) from the
    entry's options; ``start`` is the request that begins it from the
    Jobs view, or None where that is not safe from a page; ``where`` says
    where else it is started."""

    name: str
    what: str
    clock: Callable[[Any, dict[str, Any]], Any] | None = None
    start: dict[str, Any] | None = None
    where: str | None = None


# the Jobs view's order, after "maintain" and the passes it holds
JOBS = (
    DoorJob(
        "maintain",
        "every pass below that the nightly runs, one job",
        clock=lambda con, o: start_maintain(con, o.get("only")),
        start={"path": "/maintain", "body": {}},
    ),
    DoorJob(
        "resolve",
        "the safe merges: equal names in a type, a type and its subtype,"
        " the concept/method twins; the likely pairs stay a person's",
        clock=lambda con, o: start_resolve(
            con, twins=o.get("twins", True), subtypes=o.get("subtypes", True)
        ),
        start={
            "path": "/graph/resolve",
            "body": {"apply": True, "twins": True, "subtypes": True, "likely": False},
        },
    ),
    DoorJob(
        "figures",
        "a slice of the documents whose pictures the vision model has not read",
        clock=lambda con, o: start_figures(con, o.get("documents")),
        start={"path": "/figures", "body": {}},
    ),
    DoorJob(
        "fixes",
        "a slice of the readings the text checks name as fixes: marker for a"
        " scan read from its OCR layer, the crops for a caption with no picture",
        clock=lambda con, o: start_fixes(con, o.get("documents")),
        start={"path": "/fixes", "body": {}},
    ),
    DoorJob(
        "questions",
        "the standing questions asked again where the library learned"
        " something, then the day's briefing",
        clock=lambda con, o: start_questions(con, briefing=o.get("briefing", True)),
        start={"path": "/questions/run", "body": {}},
    ),
    DoorJob(
        "backup",
        "what cannot be rebuilt, copied to the backup path",
        clock=lambda con, o: start_backup(
            con, o.get("dest"), archive=o.get("archive", True)
        ),
    ),
    DoorJob(
        "heal",
        "the recurring ailments, each repaired as a job: the health panel lists them",
        where="the health panel",
    ),
)

# the passes the store does to itself, as the jobs they run under: the
# banner's list, and the Jobs view's (GET /maintenance)
MAINTENANCE = tuple(j.name for j in JOBS)


def clock_starters(con: Any) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """What the door's clock begins each scheduled job with, on ``con``."""

    def bound(
        clock: Callable[[Any, dict[str, Any]], Any],
    ) -> Callable[[dict[str, Any]], Any]:
        return lambda options: clock(con, options)

    return {j.name: bound(j.clock) for j in JOBS if j.clock is not None}
