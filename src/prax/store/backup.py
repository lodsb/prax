"""A copy of the store somewhere else.

The store is a database, a few index files and a directory of
content-addressed files (invariants 1 and 2), so a backup is three
copies: the database through SQLite's online backup (one consistent
snapshot in a single step, the WAL folded in, writers not blocked), the
vector indexes and the config as files, and the archive file by file —
its files are immutable and named by their hash, so only what the copy
lacks is copied, and a nightly run costs what the day added. The copy is
itself a store: point ``PRAX_DATA_DIR`` at it and a door opens it.

The delta vector index is copied as it stands, so a vector written in the
seconds between the database snapshot and the file copy can be missing
from the copy; ``chunks-without-vectors`` in ``prax heal`` finds those
and the worker re-embeds them. Model files are not copied (``prax models
fetch`` gets them again).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prax import config

from .base import _LOCK
from .jobs import Job

MANIFEST = "backup.json"
CONFIG_FILES = ("prax.yaml",)
Log = Callable[[str], None]


def backup_target(dest: str | Path | None) -> Path:
    """Where a backup goes: ``dest``, or the ``paths.backup`` setting
    [``PRAX_BACKUP``]; an absolute path outside the store."""
    where = str(dest or config.setting("paths.backup", "PRAX_BACKUP") or "").strip()
    if not where:
        raise ValueError(
            "no backup directory: pass one, or set paths.backup in prax.yaml"
        )
    path = Path(where).expanduser()
    if not path.is_absolute():
        raise ValueError("the backup directory must be an absolute path")
    src = config.data_dir().resolve()
    target = path.resolve()
    if target == src or src in target.parents:
        raise ValueError("the backup directory must lie outside the store")
    return path


def backup(
    con: sqlite3.Connection,
    dest: str | Path | None = None,
    *,
    job: Job | None = None,
    log: Log | None = None,
) -> dict[str, Any]:
    """Copy the store to ``dest`` (``backup_target``; created when missing)
    and write a manifest there. Returns what was copied; a job (given, or
    started here) shows the progress in the Jobs view."""
    target = backup_target(dest)
    if job is not None:
        return _run(con, target, job, log)
    with Job(con, "backup", note=str(target)) as own:
        return _run(con, target, own, log)


def _run(
    con: sqlite3.Connection, dest: Path, job: Job, log: Log | None
) -> dict[str, Any]:
    started = time.time()
    src = config.data_dir()
    say = log or (lambda _: None)
    dest.mkdir(parents=True, exist_ok=True)
    job.update(note="database")
    db_bytes = _copy_database(con, dest / config.db_path().name)
    say(f"database: {db_bytes / 1e6:.0f} MB")
    job.update(note="vector indexes")
    indexes = _copy_indexes(src, dest)
    say(f"indexes: {indexes['copied']} of {indexes['files']} files copied")
    for name in CONFIG_FILES:
        if (src / name).is_file():
            shutil.copyfile(src / name, dest / name)
    archive = _copy_archive(
        config.archive_dir(), dest / config.archive_dir().name, job=job, log=log
    )
    say(f"archive: {archive['copied']} of {archive['files']} files copied")
    report = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": str(src),
        "dest": str(dest),
        "database_bytes": db_bytes,
        "indexes": indexes,
        "archive": archive,
        "seconds": round(time.time() - started, 1),
    }
    (dest / MANIFEST).write_text(json.dumps(report, indent=2), encoding="utf-8")
    job.update(
        note=f"done: {archive['copied']} new archive files,"
        f" {(db_bytes + archive['bytes']) / 1e6:.0f} MB, {report['seconds']} s"
    )
    return report


def _copy_database(con: sqlite3.Connection, target: Path) -> int:
    """SQLite's online backup in one step: a consistent snapshot of the
    source at that moment (WAL readers do not block the writer), written
    beside the target and moved into place when complete."""
    part = target.with_name(target.name + ".part")
    for stale in _sidecars(part):
        stale.unlink(missing_ok=True)
    out = sqlite3.connect(part)
    try:
        con.backup(out, pages=-1)
    finally:
        out.close()
    os.replace(part, target)
    for leftover in _sidecars(target)[1:]:
        leftover.unlink(missing_ok=True)
    return target.stat().st_size


def _sidecars(path: Path) -> list[Path]:
    return [
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
    ]


def _copy_indexes(src: Path, dest: Path) -> dict[str, int]:
    """The usearch files, under the store's write lock so a delta being
    saved is not replaced under the copy; unchanged main files (same size
    and time) are left alone."""
    files = copied = 0
    with _LOCK:
        for path in sorted(src.glob("vectors-*.usearch")):
            files += 1
            target = dest / path.name
            if _same_file(path, target):
                continue
            shutil.copy2(path, target)
            copied += 1
    return {"files": files, "copied": copied}


def _same_file(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except FileNotFoundError:
        return False
    return sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)


def _copy_archive(
    src: Path, dest: Path, *, job: Job, log: Log | None
) -> dict[str, int]:
    """Every archive file the copy lacks (or holds at another size: an
    interrupted copy), shard by shard."""
    files = copied = total = 0
    dest.mkdir(parents=True, exist_ok=True)
    shards = sorted(p for p in src.iterdir() if p.is_dir()) if src.is_dir() else []
    for n, shard in enumerate(shards, 1):
        out = dest / shard.name
        out.mkdir(exist_ok=True)
        have = {e.name: e.stat().st_size for e in os.scandir(out) if e.is_file()}
        for entry in os.scandir(shard):
            if not entry.is_file():
                continue
            files += 1
            size = entry.stat().st_size
            if have.get(entry.name) == size:
                continue
            shutil.copyfile(entry.path, out / entry.name)
            copied += 1
            total += size
        if n % 16 == 0 or n == len(shards):
            job.update(
                done=n,
                total=len(shards),
                note=f"archive {n}/{len(shards)}: {copied} copied",
            )
            if log:
                log(f"archive {n}/{len(shards)}: {copied} files copied")
    return {"files": files, "copied": copied, "bytes": total}
