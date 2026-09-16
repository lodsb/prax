"""The door's clock: what runs at an hour of the day.

``schedule:`` in ``prax.yaml`` names the hour for the passes the door runs
on itself::

    schedule:
      maintain: "03:30"                       # every pass (store.PASSES)
      backup:   {at: "04:30", archive: false}  # to paths.backup

The door is the single writer and already runs both as jobs, so the clock
is a thread of its own (``prax.api``): every half minute it asks
:func:`due` whether an entry's hour has passed today without a job of
that name started since — the jobs table is the memory, so a door
restarted at noon does not run the night's pass again, and one that was
down at the hour catches up once. A pass still running is left alone.
The worker's nightly backlog pass uses the same :func:`due` with its own
memory (``prax.worker``). Hours are local time: the person's clock.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from typing import Any

from prax import config

NAMES = ("maintain", "backup")
TICK = 30.0


@dataclass(frozen=True)
class Entry:
    name: str
    at: time
    options: dict[str, Any] = field(default_factory=dict)


def parse_hour(text: str) -> time:
    """``"03:30"`` as a time of day."""
    try:
        hour, minute = str(text).strip().split(":")
        return time(int(hour), int(minute))
    except (ValueError, TypeError) as exc:
        raise config.ConfigError(f"schedule: {text!r} is not an HH:MM hour") from exc


def entries(section: dict[str, Any] | None = None) -> list[Entry]:
    raw = config.setting("schedule") if section is None else section
    if not raw:
        return []
    if not isinstance(raw, dict):
        raise config.ConfigError("schedule: must be a mapping")
    unknown = sorted(k for k in raw if k not in NAMES)
    if unknown:
        raise config.ConfigError(
            f"schedule: unknown entr{'ies' if len(unknown) > 1 else 'y'}"
            f" {', '.join(unknown)}; known: {', '.join(NAMES)}"
        )
    out = []
    for name in NAMES:
        if name not in raw or raw[name] in (None, "", False):
            continue
        value = raw[name]
        if isinstance(value, dict):
            if "at" not in value:
                raise config.ConfigError(f"schedule.{name}: needs at: HH:MM")
            options = {k: v for k, v in value.items() if k != "at"}
            out.append(Entry(name, parse_hour(value["at"]), options))
        else:
            out.append(Entry(name, parse_hour(value)))
    return out


def due(at: time, now: datetime, last: datetime | None) -> bool:
    """Has today's ``at`` passed with nothing started since? ``now`` and
    ``last`` are aware; the hour is read on ``now``'s clock."""
    today_at = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if now < today_at:
        return False
    return last is None or last.astimezone(now.tzinfo) < today_at


def _aware(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    parsed = datetime.fromisoformat(stamp)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def tick(
    con: Any,
    start: dict[str, Callable[[dict[str, Any]], Any]],
    *,
    now: datetime | None = None,
    entries_: list[Entry] | None = None,
) -> list[str]:
    """Start what is due; the names started. ``start[name](options)`` is
    what begins the job (``prax.api`` binds the endpoints' own code);
    ``con`` reads the jobs table."""
    from prax import store

    when = now or datetime.now().astimezone()
    started = []
    for entry in entries() if entries_ is None else entries_:
        if entry.name not in start:
            continue
        last = store.last_job(con, entry.name)
        if last and last["status"] == "running":
            continue
        if not due(entry.at, when, _aware(last["started_at"] if last else None)):
            continue
        try:
            start[entry.name](entry.options)
            started.append(entry.name)
            logging.getLogger("prax.schedule").info(
                "%s started by the clock (%s)", entry.name, entry.at.strftime("%H:%M")
            )
        except Exception:
            logging.getLogger("prax.schedule").exception(
                "the clock could not start %s", entry.name
            )
    return started
