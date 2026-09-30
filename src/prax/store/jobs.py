"""Jobs: what runs, how far it is, and what ran lately.

Bookkeeping only — nothing reads these rows to decide what to do next. A
pass announces itself, beats while it works, and is closed when its process
is gone or its heartbeat stopped (the door reaps).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

from prax.graph import ontology

from .base import (
    _INDEX_LOCK,
    _NOW,
    UNASSIGNED,
    Viewer,
    _indexes,
    _reading,
    _serialized,
    now,
)

# What runs on the batch host, for the door and the UI to show: each pass
# is a row with a heartbeat; one that stops beating without finishing is
# reported stale. Bookkeeping only (migration 0009).

JOB_STALE_SECONDS = 600


JOB_DEAD_SECONDS = 1800  # no heartbeat this long: the job is closed as failed


def _job_now() -> str:
    return now()  # kept by name: the jobs' own clock is the store's


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
    (``prax.host.schedule``)."""
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
@_serialized
def record_spend(
    con: sqlite3.Connection,
    *,
    step: str,
    model: str,
    usage: dict[str, Any] | None = None,
    usd: float = 0.0,
    doc_id: int | None = None,
    run: str | None = None,
) -> float:
    """One paid call in the ledger (``spend``), with the money at the
    price of this moment. Returns what it cost. A call that cost nothing
    and moved no tokens is not written: the ledger is what was paid."""
    u = usage or {}
    tokens = {
        "input_tokens": int(u.get("input_tokens", 0) or 0),
        "output_tokens": int(u.get("output_tokens", 0) or 0),
        "cached_tokens": int(
            (u.get("cache_read_input_tokens") or 0) + (u.get("cached_tokens") or 0)
        ),
    }
    if not usd and not any(tokens.values()):
        return 0.0
    con.execute(
        "INSERT INTO spend (at, step, model, doc_id, run, input_tokens,"
        " output_tokens, cached_tokens, usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            now(),
            step,
            model,
            doc_id,
            run,
            tokens["input_tokens"],
            tokens["output_tokens"],
            tokens["cached_tokens"],
            round(float(usd), 6),
        ),
    )
    con.commit()
    return round(float(usd), 6)


@_reading
def spent_usd(con: sqlite3.Connection, since: str) -> float:
    """What the paid steps have cost since a timestamp (``store.now()``
    shape, so a day is ``2026-09-22T00:00:00Z``)."""
    row = con.execute(
        "SELECT coalesce(sum(usd), 0) FROM spend WHERE at >= ?", (since,)
    ).fetchone()
    return round(float(row[0] or 0.0), 6)


@_reading
def spending(
    con: sqlite3.Connection, *, since: str = "", limit: int = 20
) -> dict[str, Any]:
    """The ledger read three ways: the total, what each step and each
    model cost, and the last calls. What the Jobs view shows."""
    where, args = ("WHERE at >= ?", (since,)) if since else ("", ())
    by_step = [
        {"step": r[0], "usd": round(float(r[1]), 6), "calls": int(r[2])}
        for r in con.execute(
            f"SELECT step, sum(usd), count(*) FROM spend {where}"
            " GROUP BY step ORDER BY sum(usd) DESC",
            args,
        )
    ]
    by_model = [
        {"model": r[0], "usd": round(float(r[1]), 6), "calls": int(r[2])}
        for r in con.execute(
            f"SELECT model, sum(usd), count(*) FROM spend {where}"
            " GROUP BY model ORDER BY sum(usd) DESC",
            args,
        )
    ]
    recent = [
        dict(r)
        for r in con.execute(
            f"SELECT at, step, model, doc_id, run, input_tokens, output_tokens,"
            f" cached_tokens, usd FROM spend {where} ORDER BY id DESC LIMIT ?",
            (*args, int(limit)),
        )
    ]
    total = sum(x["usd"] for x in by_step)
    return {
        "usd": round(total, 6),
        "calls": sum(x["calls"] for x in by_step),
        "by_step": by_step,
        "by_model": by_model,
        "recent": recent,
    }


def data_version(con: sqlite3.Connection) -> int:
    """Changes whenever another connection commits (``PRAGMA data_version``):
    the cheap "did anything change" signal the UI polls. Serialized like
    every other use of the door's one connection: the endpoints run in a
    thread pool and a connection's cursor is not shareable."""
    return int(con.execute("PRAGMA data_version").fetchone()[0])


@_reading
def running_of(
    con: sqlite3.Connection, names: tuple[str, ...]
) -> dict[str, Any] | None:
    """The newest running job of one of these names: ``name``, ``done``,
    ``total``, ``note``. What the UI's banner says while the door is busy
    with a maintenance pass."""
    if not names:
        return None
    marks = ",".join("?" * len(names))
    row = con.execute(
        f"SELECT name, done, total, note FROM jobs WHERE status = 'running'"
        f" AND name IN ({marks}) ORDER BY started_at DESC LIMIT 1",
        names,
    ).fetchone()
    return dict(row) if row else None


@_reading
def running_jobs(con: sqlite3.Connection) -> int:
    return int(
        con.execute("SELECT count(*) FROM jobs WHERE status = 'running'").fetchone()[0]
    )


# ---------------------------------------------------------------- tokens
# Named API tokens beside the administrator's PRAX_TOKEN (stage U, the
# wall). A token is a name, the modules it sees and whether it sees
# personal documents; only the sha256 of its secret is kept. The door is
# the only writer, so the lookup the middleware makes on every request is
# served from memory and forgotten on every change.

TOKEN_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_tokens: dict[str, dict[str, Any]] | None = None


def _token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@_serialized
def add_token(
    con: sqlite3.Connection,
    name: str,
    *,
    domains: list[str] | None = None,
    personal: bool = False,
) -> str:
    """A new named token; returns its secret, which is kept nowhere."""
    global _tokens
    if not TOKEN_NAME.fullmatch(name):
        raise ValueError("a token name is lowercase letters, digits, - and _")
    if con.execute("SELECT 1 FROM tokens WHERE name = ?", (name,)).fetchone():
        raise ValueError(f"a token named {name!r} exists; remove it first")
    secret = "prax_" + secrets.token_urlsafe(32)
    con.execute(
        "INSERT INTO tokens (name, hash, domains, personal) VALUES (?, ?, ?, ?)",
        (
            name,
            _token_hash(secret),
            json.dumps(sorted(set(domains))) if domains else None,
            int(bool(personal)),
        ),
    )
    con.commit()
    _tokens = None
    return secret


@_reading
def list_tokens(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """The named tokens, without their secrets."""
    return [
        {
            "name": r["name"],
            "domains": json.loads(r["domains"]) if r["domains"] else None,
            "personal": bool(r["personal"]),
            "created_at": r["created_at"],
            "last_used": r["last_used"],
        }
        for r in con.execute(
            "SELECT name, domains, personal, created_at, last_used FROM tokens"
            " ORDER BY name"
        )
    ]


@_serialized
def remove_token(con: sqlite3.Connection, name: str) -> bool:
    global _tokens
    cur = con.execute("DELETE FROM tokens WHERE name = ?", (name,))
    con.commit()
    _tokens = None
    return cur.rowcount > 0


# a token's last use is written at most this often: the lookup is on every
# request, and a write on every read would put the store's lock on it
TOKEN_USE_EVERY = 3600.0
_token_used: dict[str, float] = {}


def note_token_use(con: sqlite3.Connection, name: str) -> bool:
    """Record that a named token was used, once an hour at most: what the
    admin page shows to say which tokens can go."""
    now_m = time.monotonic()
    if now_m - _token_used.get(name, float("-inf")) < TOKEN_USE_EVERY:
        return False
    _token_used[name] = now_m
    _write_token_use(con, name)
    return True


@_serialized
def _write_token_use(con: sqlite3.Connection, name: str) -> None:
    con.execute(f"UPDATE tokens SET last_used = {_NOW} WHERE name = ?", (name,))
    con.commit()


@_reading
def token_viewer(con: sqlite3.Connection, secret: str) -> Viewer | None:
    """The viewer a named token's secret stands for, or None."""
    global _tokens
    if _tokens is None:
        _tokens = {
            str(r["hash"]): dict(r)
            for r in con.execute("SELECT name, hash, domains, personal FROM tokens")
        }
    row = _tokens.get(_token_hash(secret))
    if row is None:
        return None
    domains = None
    if row["domains"]:
        onto = ontology.current()
        seen: set[str] = set()
        for d in json.loads(row["domains"]):
            seen |= set(onto.within(d)) if d != UNASSIGNED else {UNASSIGNED}
        domains = frozenset(seen)
    return Viewer(str(row["name"]), domains, bool(row["personal"]))
