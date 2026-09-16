"""The door's clock: maintain and backup at their hours, remembered in
the jobs table; the worker's nightly pass on the same rule."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import config, schedule, store, worker


def test_entries_read_the_hours() -> None:
    got = schedule.entries(
        {"maintain": "03:30", "backup": {"at": "04:30", "archive": False}}
    )
    assert [(e.name, e.at) for e in got] == [
        ("maintain", time(3, 30)),
        ("backup", time(4, 30)),
    ]
    assert got[1].options == {"archive": False}
    assert schedule.entries({}) == []
    assert schedule.entries({"maintain": None}) == []
    with pytest.raises(config.ConfigError, match="unknown entry"):
        schedule.entries({"vacuum": "01:00"})
    with pytest.raises(config.ConfigError, match="not an HH:MM"):
        schedule.entries({"maintain": "half past three"})
    with pytest.raises(config.ConfigError, match="needs at"):
        schedule.entries({"backup": {"archive": True}})


def test_due_once_past_the_hour_and_not_again_that_day() -> None:
    at = time(3, 30)
    tz = UTC
    early = datetime(2026, 9, 16, 2, 0, tzinfo=tz)
    later = datetime(2026, 9, 16, 9, 0, tzinfo=tz)
    assert not schedule.due(at, early, None)
    assert schedule.due(at, later, None)  # never ran: catch up
    yesterday = datetime(2026, 9, 15, 3, 31, tzinfo=tz)
    assert schedule.due(at, later, yesterday)
    today = datetime(2026, 9, 16, 3, 30, 5, tzinfo=tz)
    assert not schedule.due(at, later, today)  # a door restarted at nine
    # a naive 'last' from another clock is compared on now's clock
    plus_two = datetime(2026, 9, 16, 5, 31, tzinfo=UTC).astimezone(
        __import__("datetime").timezone(timedelta(hours=2))
    )
    assert not schedule.due(at, later, plus_two)


def test_tick_starts_what_is_due_and_remembers_it_in_the_jobs_table(
    con: sqlite3.Connection,
) -> None:
    started: list[tuple[str, dict[str, Any]]] = []

    def starter(name: str) -> Any:
        def start(options: dict[str, Any]) -> None:
            started.append((name, options))
            store.job_finish(con, store.job_start(con, name), status="done")

        return start

    starters = {"maintain": starter("maintain"), "backup": starter("backup")}
    entries = schedule.entries(
        {"maintain": "03:30", "backup": {"at": "04:30", "archive": False}}
    )
    noon = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    assert schedule.tick(con, starters, now=noon, entries_=entries) == [
        "maintain",
        "backup",
    ]
    assert started == [("maintain", {}), ("backup", {"archive": False})]
    # the same day again: nothing, the jobs table remembers
    assert (
        schedule.tick(con, starters, now=noon + timedelta(hours=1), entries_=entries)
        == []
    )
    # the next day, past the hour of one of them
    next_day = datetime(2026, 9, 17, 4, 0, tzinfo=UTC)
    assert schedule.tick(con, starters, now=next_day, entries_=entries) == ["maintain"]
    # one still running is left alone
    running = store.job_start(con, "backup")
    later = next_day.replace(hour=12)
    assert schedule.tick(
        con, starters, now=later + timedelta(days=2), entries_=entries
    ) == ["maintain"]
    store.job_finish(con, running, status="done")
    # a starter that fails does not stop the others
    starters["maintain"] = lambda o: (_ for _ in ()).throw(RuntimeError("no"))
    assert schedule.tick(
        con, starters, now=later + timedelta(days=4), entries_=entries
    ) == ["backup"]


def test_the_door_runs_its_clock(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``schedule:`` set, the door's clock thread starts a maintain job
    whose hour has passed; ``door.clock_seconds`` makes it tick fast."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text(
        "schedule: {maintain: '00:00'}\n"
        "door: {clock_seconds: 0.05, inbox_scan_seconds: 0}\n",
        encoding="utf-8",
    )
    from prax.api import app

    with TestClient(app) as client:
        import time as clock

        deadline = clock.monotonic() + 10
        names: list[str] = []
        while clock.monotonic() < deadline:
            jobs = client.get("/jobs").json()
            names = [
                j["name"] for j in jobs.get("running", []) + jobs.get("recent", [])
            ]
            if "maintain" in names:
                break
            clock.sleep(0.05)
        assert "maintain" in names


def test_the_worker_does_its_nightly_pass_once_past_the_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passes: list[dict[str, Any]] = []

    def fake_run_once(door: Any, **kw: Any) -> dict[str, Any]:
        passes.append(kw)
        return {}

    monkeypatch.setattr(worker, "run_once", fake_run_once)

    class Door:
        name = "test"

        def post_json(self, *_: Any, **__: Any) -> dict[str, Any]:
            return {"job_id": 1}

    worker.watch(Door(), once=True, nightly="00:00", nightly_limit=7)  # type: ignore[arg-type]
    assert [p["scope"] for p in passes] == ["captures", "all"]
    assert passes[1]["limit"] == 7
    passes.clear()
    worker.watch(Door(), once=True)  # type: ignore[arg-type]
    assert [p["scope"] for p in passes] == ["captures"]
