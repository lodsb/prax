"""The door's clock: maintain and backup at their hours, remembered in
the jobs table; the worker's nightly pass on the same rule."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import config, store, worker
from prax.host import schedule


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
    clock = {"now": datetime(2026, 9, 16, 12, 0, tzinfo=UTC)}

    def starter(name: str) -> Any:
        def start(options: dict[str, Any]) -> None:
            started.append((name, options))
            job = store.job_start(con, name)
            store.job_finish(con, job, status="done")
            # the row is stamped with the real clock; the test's is this one
            con.execute(
                "UPDATE jobs SET started_at = ? WHERE id = ?",
                (clock["now"].isoformat(), job),
            )

        return start

    def tick(now: datetime) -> list[str]:
        clock["now"] = now
        return schedule.tick(con, starters, now=now, entries_=entries)

    starters = {"maintain": starter("maintain"), "backup": starter("backup")}
    entries = schedule.entries(
        {"maintain": "03:30", "backup": {"at": "04:30", "archive": False}}
    )
    noon = clock["now"]
    assert tick(noon) == [
        "maintain",
        "backup",
    ]
    assert started == [("maintain", {}), ("backup", {"archive": False})]
    # the same day again: nothing, the jobs table remembers
    assert tick(noon + timedelta(hours=1)) == []
    # the next day, past the hour of one of them
    next_day = datetime(2026, 9, 17, 4, 0, tzinfo=UTC)
    assert tick(next_day) == ["maintain"]
    # one still running is left alone
    running = store.job_start(con, "backup")
    later = next_day.replace(hour=12)
    # stamped with the test's clock, not the real one (the real day caught
    # up with the test's on 2026-09-21 and made "backup" look already run)
    con.execute(
        "UPDATE jobs SET started_at = ? WHERE id = ?", (later.isoformat(), running)
    )
    assert tick(later + timedelta(days=2)) == ["maintain"]
    store.job_finish(con, running, status="done")
    # a starter that fails does not stop the others
    starters["maintain"] = lambda o: (_ for _ in ()).throw(RuntimeError("no"))
    assert tick(later + timedelta(days=4)) == ["backup"]


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


def _ticking_clock(monkeypatch: pytest.MonkeyPatch, start: datetime, step: float):
    """The worker's clock: ``start`` on the first look, ``step`` seconds
    later on each next one."""
    looks = {"n": 0}

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            t = start + timedelta(seconds=step * looks["n"])
            looks["n"] += 1
            return t.astimezone(tz) if tz else t

    monkeypatch.setattr(worker, "datetime", Clock)


class _Door:
    name = "test"

    def post_json(self, *_: Any, **__: Any) -> dict[str, Any]:
        return {"job_id": 1}


def test_a_worker_started_after_the_hour_waits_for_tomorrows_nightly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker restarted at eight in the morning (prax up after a crash,
    a person after a change) ran the nightly pass at once — a hundred
    documents a step over everything — because it had no memory of the
    night. It waits for the hour instead."""
    passes: list[dict[str, Any]] = []

    def fake_run_once(door: Any, **kw: Any) -> dict[str, Any]:
        passes.append(kw)
        return {}

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    _ticking_clock(monkeypatch, datetime(2026, 9, 17, 8, 0).astimezone(), 20)
    worker.watch(_Door(), once=True, nightly="03:00", nightly_limit=7)  # type: ignore[arg-type]
    assert [p["scope"] for p in passes] == ["captures"]
    passes.clear()
    worker.watch(_Door(), once=True)  # type: ignore[arg-type]
    assert [p["scope"] for p in passes] == ["captures"]


def test_the_nightly_pass_happens_at_the_hour_and_once_a_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A watching worker passes every few seconds: the pass over
    everything comes once, when the hour comes round, with the nightly
    limit; the hour having passed must not make every pass one."""
    passes: list[dict[str, Any]] = []

    def fake_run_once(door: Any, **kw: Any) -> dict[str, Any]:
        passes.append(kw)
        if len(passes) == 7:
            raise KeyboardInterrupt  # the person stops the worker
        return {}

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    # started at 02:59:10, a look every 20 s: the hour comes round on the way
    _ticking_clock(monkeypatch, datetime(2026, 9, 17, 2, 59, 10).astimezone(), 20)
    with pytest.raises(KeyboardInterrupt):
        worker.watch(_Door(), interval=0.01, nightly="03:00", nightly_limit=7)  # type: ignore[arg-type]
    scopes = [p["scope"] for p in passes]
    assert scopes.count("all") == 1 and scopes[0] == "captures"
    assert passes[scopes.index("all")]["limit"] == 7
    assert scopes[-1] == "captures"


def _pdf_with(con: sqlite3.Connection, name: str, figure: str) -> int:
    doc = store.register(con, name.encode() * 3, mime="application/pdf", title=name)
    store.index_text(con, doc["doc_id"], "# Pictures\n\n" + "Prose. " * 20 + figure)
    return int(doc["doc_id"])


def test_the_figures_backlog_is_asked_for_a_slice_at_a_time(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A night's slice of the documents whose pictures the vision model has
    not read, and nothing while the last slice waits: all of it at once
    would stand in front of every capture's parse for a day and a half."""
    from prax import models
    from prax.api.passes import start_figures
    from prax.capture import pipeline

    local = models.ModelSpec(
        name="server", kind="openai", base_url="http://127.0.0.1:1/v1", model="v"
    )
    monkeypatch.setattr(models, "resolve", lambda s: local if s == "vision" else None)
    monkeypatch.setattr(pipeline, "free", lambda step: step == "vision")
    pictured = [
        _pdf_with(con, f"paper {i}", f"\n\n![Fig. {i}. A rig.](figure:{'d' * 63}{i})\n")
        for i in range(3)
    ]
    # a caption no picture stands behind: no reading will change it
    _pdf_with(con, "captions", "\n\nFigure 3: a plot the extractor lost.\n")
    got = start_figures(con, 2)
    assert got["requested"] == 2
    assert set(store.waiting_readings(con)) == {"figures"}
    # the next night, with that slice still waiting: nothing more
    assert start_figures(con, 2)["requested"] == 0
    asked = {r["doc_id"] for r in store.reading_requests(con, limit=None)}
    assert asked == set(pictured[:2])
    assert store.last_job(con, "figures") is not None

    # the first could not be read (its picture is not in the original), the
    # second ran and changed nothing: the next night asks for the third
    # alone, neither of the others again
    store.finish_reading(
        con,
        pictured[0],
        outcome="error",
        stamp="figures/v",
        error="ExtractionError: no figure could be read",
        extractor="figures",
    )
    store.finish_reading(
        con, pictured[1], outcome="same", stamp="figures/v", extractor="figures"
    )
    assert start_figures(con, 2)["requested"] == 1
    waiting = {r["doc_id"] for r in store.reading_requests(con, state="requested")}
    assert waiting == {pictured[2]}
    # a new text of it is tried again
    assert store.tried_on_this_text(con, "figures") == {pictured[0], pictured[1]}
    con.execute(
        "UPDATE documents SET parsed_at = '2999-01-01T00:00:00Z' WHERE id = ?",
        (pictured[0],),
    )
    assert store.tried_on_this_text(con, "figures") == {pictured[1]}


def _found(con: sqlite3.Connection, doc_id: int, *findings: dict[str, Any]) -> None:
    """What the text checks found in a document's current text."""
    meta = store.get_meta(con, doc_id)
    row = con.execute("SELECT text_hash FROM documents WHERE id = ?", (doc_id,))
    meta["quality"] = {"text_hash": row.fetchone()[0], "findings": list(findings)}
    store.set_meta(con, doc_id, meta)


def test_the_fixes_the_checks_name_are_asked_for_a_slice_a_night(
    con: sqlite3.Connection,
) -> None:
    """A slice a night of each reading the findings name, nothing for a
    reading while its last slice waits, never a fix that failed on the
    text the document holds, and nothing for a finding no reading fixes."""
    from prax.api.passes import start_fixes

    scans = [_pdf_with(con, f"scan {i}", "") for i in range(3)]
    for doc_id in scans:
        _found(
            con,
            doc_id,
            {
                "check": "ocr-layer",
                "count": 1,
                "fix": {"extractor": "marker", "ocr": True},
            },
        )
    crops = [_pdf_with(con, f"plots {i}", "") for i in range(2)]
    for doc_id in crops:
        _found(
            con,
            doc_id,
            {"check": "bare-figures", "count": 3, "fix": {"extractor": "figure-crops"}},
        )
    # a finding no reading fixes stays for a person
    _found(con, _pdf_with(con, "odd", ""), {"check": "leftovers", "count": 2})
    # one document, two findings naming the same reading: asked once, and
    # in the mode that reads every page itself
    _found(
        con,
        scans[0],
        {"check": "maths-as-text", "count": 4, "fix": {"extractor": "marker"}},
        {"check": "ocr-layer", "count": 1, "fix": {"extractor": "marker", "ocr": True}},
    )

    got = start_fixes(con, 2)
    assert got["requested"] == {"marker": 2, "figure-crops": 2}
    asked = {
        (r["doc_id"], r["extractor"]) for r in store.reading_requests(con, limit=None)
    }
    assert asked == {
        (scans[0], "marker"),
        (scans[1], "marker"),
        (crops[0], "figure-crops"),
        (crops[1], "figure-crops"),
    }
    mode = next(
        r["mode"]
        for r in store.reading_requests(con, limit=None)
        if r["doc_id"] == scans[0]
    )
    assert str(mode).endswith("-ocr")
    # the next night, with those still waiting: nothing more
    again = start_fixes(con, 2)
    assert again["requested"] == {} and again["waiting"] == {
        "marker": 2,
        "figure-crops": 2,
    }

    # one marker read failed on the text it holds, one came out no better
    # than the OCR layer and kept it, so its finding still names marker:
    # the next night asks for the third scan alone
    store.finish_reading(
        con, scans[0], outcome="error", stamp="marker/x", error="no", extractor="marker"
    )
    store.finish_reading(
        con, scans[1], outcome="kept", stamp="marker/x", extractor="marker"
    )
    for doc_id in crops:
        store.finish_reading(
            con,
            doc_id,
            outcome="same",
            stamp="figure-crops/x",
            extractor="figure-crops",
        )
    _found(con, crops[0])  # the crops filled it: no finding left
    _found(con, crops[1])
    third = start_fixes(con, 5)
    assert third["requested"] == {"marker": 1}
    waiting = {r["doc_id"] for r in store.reading_requests(con, state="requested")}
    assert waiting == {scans[2]}
    assert store.last_job(con, "fixes") is not None


def test_the_fixes_entry_is_one_the_clock_knows() -> None:
    got = schedule.entries({"fixes": {"at": "21:30", "documents": 200}})
    assert [(e.name, e.options) for e in got] == [("fixes", {"documents": 200})]


def test_the_figures_entry_is_one_the_clock_knows() -> None:
    got = schedule.entries({"figures": {"at": "21:00", "documents": 150}})
    assert [(e.name, e.options) for e in got] == [("figures", {"documents": 150})]


# ------------------------------------------------- one workflow, stage J


@pytest.fixture()
def client() -> Any:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_the_passes_are_one_list_with_a_start_where_it_is_safe(
    client: TestClient,
) -> None:
    got = client.get("/maintenance").json()
    by = {p["name"]: p for p in got["passes"]}
    assert {"maintain", "resolve", "figures", "questions", "backup", "heal"} <= set(by)
    assert set(store.PASSES) <= set(by)
    assert by["acronyms"]["start"] == {
        "path": "/maintain",
        "body": {"only": ["acronyms"]},
    }
    assert by["acronyms"]["what"]  # from the pass's own docstring
    # the rechunk's cost and backup's destination are the command line's
    assert by["rechunk"]["start"] is None and by["backup"]["start"] is None
    assert got["running"] is None


def test_the_banner_names_the_pass_that_runs(client: TestClient) -> None:
    con = client.app.state.con
    assert client.get("/changes").json()["maintenance"] is None
    job = store.Job(con, "maintain", note="rechunk", total=9962)
    job.update(done=3400)
    banner = client.get("/changes").json()["maintenance"]
    assert (banner["name"], banner["done"], banner["total"]) == ("maintain", 3400, 9962)
    store.job_finish(con, job.id, status="done")
    assert client.get("/changes").json()["maintenance"] is None
