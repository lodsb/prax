"""Jobs: what runs, how far it is, and what ran lately.

Bookkeeping only — nothing reads these rows to decide what to do next. A
pass announces itself, beats while it works, and is closed when its process
is gone or its heartbeat stopped (the door reaps).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

from .base import _INDEX_LOCK, _NOW, _indexes, _reading, _serialized

# What runs on the batch host, for the door and the UI to show: each pass
# is a row with a heartbeat; one that stops beating without finishing is
# reported stale. Bookkeeping only (migration 0009).

JOB_STALE_SECONDS = 600


JOB_DEAD_SECONDS = 1800  # no heartbeat this long: the job is closed as failed


def _job_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@_serialized
def job_start(
    con: sqlite3.Connection,
    name: str,
    *,
    total: int | None = None,
    note: str | None = None,
    host: str | None = None,
    pid: int | None = None,
) -> int:
    """A job row for this process, or for a worker elsewhere (``host``,
    ``pid`` 0: the reaper leaves those to their heartbeat)."""
    import os
    import socket

    now = _job_now()
    cur = con.execute(
        "INSERT INTO jobs (name, host, pid, started_at, updated_at, status, done,"
        " total, note) VALUES (?,?,?,?,?,'running',0,?,?)",
        (
            name,
            host or socket.gethostname(),
            os.getpid() if pid is None else pid,
            now,
            now,
            total,
            note,
        ),
    )
    con.commit()
    return int(cur.lastrowid or 0)


@_serialized
def job_update(
    con: sqlite3.Connection,
    job_id: int,
    *,
    done: int | None = None,
    total: int | None = None,
    note: str | None = None,
) -> None:
    con.execute(
        "UPDATE jobs SET updated_at = ?, done = coalesce(?, done),"
        " total = coalesce(?, total), note = coalesce(?, note) WHERE id = ?",
        (_job_now(), done, total, note, job_id),
    )
    con.commit()


@_serialized
def job_finish(
    con: sqlite3.Connection,
    job_id: int,
    *,
    status: str = "done",
    note: str | None = None,
) -> None:
    now = _job_now()
    con.execute(
        "UPDATE jobs SET status = ?, finished_at = ?, updated_at = ?,"
        " note = coalesce(?, note) WHERE id = ?",
        (status, now, now, note, job_id),
    )
    con.commit()


def _pid_alive(pid: int) -> bool:
    import os
    import sys

    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@_serialized
def job_reap(con: sqlite3.Connection) -> int:
    """Close the running jobs of this host whose process is gone (killed,
    crashed, a reboot) and, from any host, those without a heartbeat for
    ``JOB_DEAD_SECONDS``: they would otherwise sit as stale for ever. The
    door does this at startup and on each drop-folder pass. Returns how
    many were closed."""
    import socket

    n = 0
    for r in con.execute(
        "SELECT id, pid FROM jobs WHERE status = 'running' AND host = ?",
        (socket.gethostname(),),
    ).fetchall():
        if r["pid"] and not _pid_alive(r["pid"]):
            con.execute(
                f"UPDATE jobs SET status = 'failed', finished_at = {_NOW},"
                " note = coalesce(note, '') || ' (process gone)' WHERE id = ?",
                (r["id"],),
            )
            n += 1
    # a job from another host, or one without a pid to check, whose
    # heartbeat stopped long ago is gone too (a worker's pass is far shorter)
    cur = con.execute(
        f"UPDATE jobs SET status = 'failed', finished_at = {_NOW},"
        " note = coalesce(note, '') || ' (no heartbeat)'"
        " WHERE status = 'running'"
        " AND (julianday('now') - julianday(updated_at)) * 86400 > ?",
        (JOB_DEAD_SECONDS,),
    )
    n += cur.rowcount
    con.commit()
    return n


@_reading
def get_job(con: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def last_job(con: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    """The newest job of that name, running or not: the clock's memory
    (``prax.schedule``)."""
    row = con.execute(
        "SELECT * FROM jobs WHERE name = ? ORDER BY started_at DESC, id DESC LIMIT 1",
        (name,),
    ).fetchone()
    return dict(row) if row else None


def list_jobs(con: sqlite3.Connection, *, limit: int = 20) -> dict[str, Any]:
    """``running`` (with ``stale`` when the heartbeat is old) and the last
    ``limit`` finished jobs, newest first."""
    now = datetime.now(UTC)
    running = []
    for r in con.execute(
        "SELECT * FROM jobs WHERE status = 'running' ORDER BY started_at DESC"
    ):
        row = dict(r)
        try:
            beat = datetime.fromisoformat(row["updated_at"])
            age = (now - beat).total_seconds()
        except ValueError:
            age = 0.0
        row["stale"] = age > JOB_STALE_SECONDS
        row["age"] = int(age)
        running.append(row)
    recent = [
        dict(r)
        for r in con.execute(
            "SELECT * FROM jobs WHERE status != 'running'"
            " ORDER BY finished_at DESC, id DESC LIMIT ?",
            (limit,),
        )
    ]
    return {"running": running, "recent": recent}


class Job:
    """``with store.Job(con, "extract", total=n) as job: job.update(done=i)``:
    started on entry, finished on exit (failed with the error's text when
    the block raised)."""

    def __init__(
        self,
        con: sqlite3.Connection,
        name: str,
        *,
        total: int | None = None,
        note: str | None = None,
    ) -> None:
        self.con = con
        self.name = name
        self.id = job_start(con, name, total=total, note=note)

    @classmethod
    def existing(cls, con: sqlite3.Connection, job_id: int) -> Job:
        """The job row another thread started, to carry on and finish
        here (a thread that opens its own connection)."""
        job = cls.__new__(cls)
        job.con = con
        job.id = job_id
        row = con.execute("SELECT name FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"no job {job_id}")
        job.name = row["name"]
        return job

    def update(
        self,
        *,
        done: int | None = None,
        total: int | None = None,
        note: str | None = None,
    ) -> None:
        job_update(self.con, self.id, done=done, total=total, note=note)

    def note(self, text: str) -> None:
        job_update(self.con, self.id, note=text)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None and exc_type is not None:
            job_finish(
                self.con, self.id, status="failed", note=f"{exc_type.__name__}: {exc}"
            )
        else:
            job_finish(self.con, self.id, status="done")


def release_vector_views() -> int:
    """Drop every memory-mapped read view of the index files so a batch job
    on this machine can replace them (Windows refuses otherwise); the next
    query reopens them. Returns how many views were closed."""
    n = 0
    with _INDEX_LOCK:
        for key in [k for k in _indexes if not k[1]]:
            idx = _indexes.pop(key, None)
            if idx is not None:
                idx.close()
                n += 1
    return n


@_reading
def data_version(con: sqlite3.Connection) -> int:
    """Changes whenever another connection commits (``PRAGMA data_version``):
    the cheap "did anything change" signal the UI polls. Serialized like
    every other use of the door's one connection: the endpoints run in a
    thread pool and a connection's cursor is not shareable."""
    return int(con.execute("PRAGMA data_version").fetchone()[0])


@_reading
def running_jobs(con: sqlite3.Connection) -> int:
    return int(
        con.execute("SELECT count(*) FROM jobs WHERE status = 'running'").fetchone()[0]
    )
