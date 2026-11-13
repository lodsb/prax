"""The one writer (invariant 4): the process-wide re-entrant lock a write
takes (``_serialized``), a read's retry without it (``_reading``), the
busy-retry with rollback, the connection's settings, and the log of a
write that waits or holds too long."""

from __future__ import annotations

import contextlib
import functools
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

_LOCK = threading.RLock()
BUSY_TIMEOUT = 30.0


_TOKEN = re.compile(r"\w+", re.UNICODE)


# SQLite's page cache per connection (``door.sqlite_cache_mb``) and how
# much of the file it maps (``door.sqlite_mmap_mb``). The defaults SQLite
# ships with are a 2 MB cache and no mapping: a keyword query over a
# million chunks read its posting lists from disk every time (18 s cold,
# 1.7 s warm on the desktop; 1.7 s and 1.3 s with these). The mapping is
# the operating system's cache, file-backed and shared, so a small board
# gives it back under pressure; the cache is the connection's own.
CACHE_MB = 64
MMAP_MB = 1024
# what the write-ahead log is cut back to when a checkpoint empties it:
# nothing set it, and it stood at 48 MB between writes (2026-09-30)
JOURNAL_MB = 16


_SURROGATE = re.compile(r"[\ud800-\udfff]")


P = ParamSpec("P")


R = TypeVar("R")
F = TypeVar("F", bound=Callable[..., Any])


_depth = threading.local()


LOCK_RETRIES = 6


class _NoLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


def _take_back(con: sqlite3.Connection, mark: str | None) -> None:
    """Undo a failed call's writes: to its savepoint when the transaction
    held earlier work, else the whole transaction, which was its own."""
    if mark is None:
        con.rollback()
        return
    try:
        con.execute(f"ROLLBACK TO {mark}")
        con.execute(f"RELEASE {mark}")
    except sqlite3.Error:
        con.rollback()  # the savepoint went with a commit inside the call


def _guarded(fn: Callable[P, R], *, lock: bool) -> Callable[P, R]:
    """The retry that both guards share: across processes (the door, a
    backlog pass on one database) SQLite's busy handler waits
    ``BUSY_TIMEOUT`` and then says "database is locked"; the outermost
    store call rolls back and tries again a few times, so a capture
    arriving while a batch pass holds a long transaction is not lost."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        asked = time.monotonic()
        with _LOCK if lock else _NoLock():
            depth = getattr(_depth, "n", 0)
            _depth.n = depth + 1
            if lock and depth == 0:
                _note_wait(fn, time.monotonic() - asked)
                _HOLDER["name"], _HOLDER["since"] = fn.__name__, time.monotonic()
            con = next((a for a in args if isinstance(a, sqlite3.Connection)), None)
            # the outermost call is atomic: when it fails, what it wrote is
            # taken back and the write lock let go. Left open, a failed
            # statement's transaction held the lock against every other
            # connection until a later commit, which then saved the failed
            # call's half too: a label the vocabulary pass could not write
            # kept the door's writers waiting 200 s a time (2026-09-30).
            # Work an earlier call left uncommitted is kept: the call runs
            # inside a savepoint then.
            outer = depth == 0 and con is not None
            mark = None
            if outer and con is not None and con.in_transaction:
                mark = f"prax_call_{id(con)}_{time.monotonic_ns()}"
                con.execute(f"SAVEPOINT {mark}")
            try:
                for attempt in range(LOCK_RETRIES):
                    try:
                        got = fn(*args, **kwargs)
                        if mark and con is not None and con.in_transaction:
                            # gone when the call committed and began again
                            with contextlib.suppress(sqlite3.OperationalError):
                                con.execute(f"RELEASE {mark}")
                        return got
                    except sqlite3.OperationalError as exc:
                        if (
                            depth
                            or "locked" not in str(exc)
                            or attempt == LOCK_RETRIES - 1
                        ):
                            raise
                        if con is not None:
                            con.rollback()
                            mark = None  # the rollback took the savepoint too
                        time.sleep(0.3 * (attempt + 1))
                raise AssertionError("unreachable")
            except BaseException:
                if outer and con is not None and con.in_transaction:
                    _take_back(con, mark)
                raise
            finally:
                _depth.n = depth
                if lock and depth == 0:
                    _note_hold(fn, time.monotonic() - float(_HOLDER["since"] or 0.0))
                    _HOLDER["last"], _HOLDER["name"] = fn.__name__, None

    return wrapper


# Who holds the store's write lock, and since when: what a write that had
# to wait names in the log. A write waits for whatever holds it, so one
# function that holds it for minutes stalls every capture, append and
# worker post behind it (the document fields pass over the whole library,
# nightly at 03:30, until 2026-10-04: an agent's append took over 120 s).
LOCK_WAIT_SLOW = 5.0  # seconds a write may wait before it is logged
LOCK_HOLD_SLOW = 10.0  # seconds a write may hold the lock before it is logged
_HOLDER: dict[str, Any] = {"name": None, "since": None, "last": None}
_lock_log = logging.getLogger("prax.store")


def _note_wait(fn: Callable[..., Any], seconds: float) -> None:
    if seconds >= LOCK_WAIT_SLOW:
        _lock_log.warning(
            "store: %s waited %.1f s for the write lock (held by %s)",
            fn.__name__,
            seconds,
            _HOLDER.get("last") or "?",
        )


def _note_hold(fn: Callable[..., Any], seconds: float) -> None:
    if seconds >= LOCK_HOLD_SLOW:
        _lock_log.warning("store: %s held the write lock %.1f s", fn.__name__, seconds)


def _serialized(fn: Callable[P, R]) -> Callable[P, R]:
    """One writer at a time in this process: every store function that
    writes takes ``_LOCK`` for its duration."""
    return _guarded(fn, lock=True)


def _committing(fn: Callable[P, R]) -> Callable[P, R]:
    """A core that does not commit, with the commit after it: the public
    face of a write that a larger write also composes, so the larger one
    commits once and a failure halfway takes all of it back (a page split
    written as several pages and moves). The name is the core's without
    its underscore, as the lock's log names it."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        got = fn(*args, **kwargs)
        next(a for a in args if isinstance(a, sqlite3.Connection)).commit()
        return got

    wrapper.__name__ = fn.__name__.lstrip("_")
    wrapper.__qualname__ = wrapper.__name__
    return wrapper


def _reading(fn: Callable[P, R]) -> Callable[P, R]:
    """A read: no lock, only the retry. A request thread reads on its own
    connection and WAL gives it a consistent snapshot, so a search never
    waits for a book being indexed or a heal retiring documents — it did
    until 2026-09-19, when every read was serialized too, and a
    seventeen-second scan under the lock each worker cycle was what
    "loading takes ages" was."""
    return _guarded(fn, lock=False)
