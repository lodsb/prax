"""The worker: does the model work the door hands out, on this machine or
another, and never opens the database.

    prax work --door http://<board>:8000 --watch

Each pass asks the door for a batch per step (parse, titles, extract,
embed), runs it with the models of this machine's prax.yaml, and posts
the results. A step whose model would cost money is skipped with a note,
so a worker left running never spends. Files in this machine's drop
folders (``Downloads/prax-inbox``, ``--also``) are uploaded to the door
with their sidecars, then removed, the way the store host's own folder
is consumed. The worker announces itself as a job with heartbeats, so
the Jobs view shows it wherever it runs.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from prax import steps as steps_mod
from prax.capture import drop
from prax.client import Door
from prax.host import hostinfo
from prax.steps.base import Log, say

log = logging.getLogger("prax.worker")
STEPS = steps_mod.STEPS
# passes that fail in a row before the worker stops and lets the
# supervisor start one with the current code (2026-09-24: 519 identical).
# Counted by the *kind* of failure, not its text: the first version of
# this rule wanted byte-identical messages and so never fired on three
# documents whose errors differed only in a token count — 71 failures,
# no give-up (2026-09-25). Counting kinds makes the rule bite sooner, so
# the count is higher: a door restarting is one kind repeated, and at the
# worker's 20 s interval twenty passes is about seven minutes, long
# enough to outlive any restart and short of the hours both incidents
# cost.
GIVE_UP_AFTER = 20


def _kind_of_trouble(exc: BaseException) -> str:
    """What sort of failure this is, with the varying parts taken out.

    A worker cycling through three documents it cannot read is as stuck
    as one repeating a single error, but its messages differ — in a token
    count, a document id, a duration. Numbers become a mark and only the
    head of the message is kept, so the counter sees a kind rather than a
    string.
    """
    return f"{type(exc).__name__}: {re.sub(chr(92) + 'd+', '#', str(exc))[:160]}"


# ------------------------------------------------------------------ steps


# ------------------------------------------------------------------- pass


def run_once(
    door: Door,
    *,
    steps: tuple[str, ...] = STEPS,
    scope: str = "captures",
    limit: int = 10,
    workers: int = 3,
    session: int | None = None,
    spend: bool = False,
    log_: Log | None = None,
) -> dict[str, Any]:
    """One pass over the steps: fetch a batch, do it, post it. Returns what
    each step did; a step with nothing to do is absent. ``spend`` lets the
    promote step run its paid model; without it a paid step is refused
    before anything is fetched. What each step does is the step's
    (``prax.steps``)."""
    from prax.steps.base import Pass

    p = Pass(door, scope, limit, workers, session, spend, log_)
    out: dict[str, Any] = {}
    for step in steps:
        line = steps_mod.get(step).run(p)
        if line is None:
            continue
        out[step] = line
        say(log_, f"{step}: {line}")
    return out


# ------------------------------------------------------------- drop folders


def push_folder(
    door: Door,
    folder: Path,
    *,
    domains: list[str] | None = None,
    log_: Log | None = None,
) -> dict[str, int]:
    """Upload the files of a local drop folder to the door with their
    sidecars, removing what was taken; what the door refused goes to
    ``failed/`` beside them."""
    counts = {"sent": 0, "failed": 0, "waiting": 0}
    if not folder.is_dir():
        return counts
    for item in drop.walk(folder):
        if item is None:
            counts["waiting"] += 1
            continue
        path, extra = item.path, item.extra
        rel = path.relative_to(folder).parts
        doms = (
            list(extra.get("domains") or [])
            or ([rel[0]] if len(rel) > 1 else [])
            or list(domains or [])
        )
        fields = {
            k: v
            for k, v in {
                "title": extra.get("title") or path.stem,
                "source_url": extra.get("source_url"),
                "domains": ",".join(doms) if doms else None,
                "tags": ",".join(extra.get("tags") or []) or None,
                "session": extra.get("session"),
                "by": extra.get("by") or "worker",
            }.items()
            if v
        }
        try:
            door.upload(path, fields)
        except Exception as exc:  # noqa: BLE001
            say(log_, f"{path.name}: {exc}")
            counts["failed"] += 1
            drop.fail(folder, item)
            continue
        counts["sent"] += 1
        drop.taken(item)
    return counts


def watch(
    door: Door,
    *,
    interval: float = 20.0,
    steps: tuple[str, ...] = STEPS,
    scope: str = "captures",
    limit: int = 10,
    workers: int = 3,
    folders: list[Path] | None = None,
    domains: list[str] | None = None,
    spend: bool = False,
    log_: Log | None = None,
    once: bool = False,
    nightly: str | None = None,
    nightly_limit: int = 100,
) -> None:
    """Keep passing; announce a session job so the door's Jobs view shows
    this worker. With ``nightly`` (``"03:00"``) one bounded pass over the
    whole library follows the regular one once that hour has passed each
    day: the backlog and the stale texts, ``nightly_limit`` documents a
    step, the same steps."""
    from prax.host import schedule

    night = schedule.parse_hour(nightly) if nightly else None
    # the nightly pass is at its hour: a worker (re)started after it — prax
    # up brings one back after a crash, a person after a change — waits
    # for tomorrow's rather than running a pass over everything at noon
    last_night: datetime | None = datetime.now().astimezone() if night else None
    same, last_trouble = 0, ""  # a failure that repeats is not a bad pass
    session = None
    try:
        session = door.post_json(
            "/work/session",
            {
                "name": "worker",
                "host": door.name,
                "pid": os.getpid(),
                "note": ", ".join(steps),
            },
        )["job_id"]
    except Exception as exc:  # noqa: BLE001
        say(log_, f"no session job: {exc}")
    try:
        while True:
            for folder in folders or []:
                c = push_folder(door, folder, domains=domains, log_=log_)
                if c["sent"] or c["failed"]:
                    say(log_, f"{folder}: {c}")
            try:
                done = run_once(
                    door,
                    steps=steps,
                    scope=scope,
                    limit=limit,
                    workers=workers,
                    session=session,
                    spend=spend,
                    log_=log_,
                )
                now = datetime.now().astimezone()
                if night and schedule.due(night, now, last_night):
                    last_night = now
                    say(
                        log_,
                        f"the nightly pass: {nightly_limit} a step over everything",
                    )
                    done = run_once(
                        door,
                        steps=steps,
                        scope="all",
                        limit=nightly_limit,
                        workers=workers,
                        session=session,
                        spend=spend,
                        log_=log_,
                    )
                if session:
                    door.post_json(
                        f"/work/session/{session}",
                        {
                            "note": f"last pass {time.strftime('%H:%M:%S')}:"
                            f" {json.dumps(done)[:120] if done else 'nothing waiting'}"
                            + (f" · {mb} MB" if (mb := hostinfo.process_mb()) else "")
                        },
                    )
            except Exception as exc:  # a bad pass is outlived; a repeated one is not
                trouble = f"{type(exc).__name__}: {exc}"
                say(log_, f"pass failed: {trouble}")
                kind = _kind_of_trouble(exc)
                # a bad pass is worth outliving; the same bad pass over and
                # over is not. A worker started before a change to
                # prax.yaml or to the code fails identically for ever —
                # 519 passes in three hours on 2026-09-24, doing no work
                # and growing by 350 MB an hour. Exiting is the repair:
                # `prax up` restarts the role, and the new process reads
                # the new configuration with the new code.
                same = same + 1 if kind == last_trouble else 1
                last_trouble = kind
                if not once and same >= GIVE_UP_AFTER:
                    say(
                        log_,
                        f"the same failure {same} times: stopping so the"
                        " supervisor starts a worker that has read the"
                        " current configuration and code",
                    )
                    raise SystemExit(1) from exc
            else:
                same, last_trouble = 0, ""
            if once:
                return
            time.sleep(interval)
    finally:
        if session:
            try:
                door.post_json(
                    f"/work/session/{session}", {"status": "done", "note": "stopped"}
                )
            except Exception as exc:  # noqa: BLE001
                say(log_, f"could not close the session job: {exc}")
