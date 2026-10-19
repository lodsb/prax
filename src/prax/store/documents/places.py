"""Where an original's bytes lie (plan item AQ, ``docs/media-by-reference.md``).

A place is a root and a path inside it. A root is a name in ``prax.yaml``
(``places.roots``) mapped to the directory where this host sees it, so the
NAS's share is ``//nas/media`` on the desktop and ``/mnt/media`` on the
board and the rows are the same. A crawl records what it saw at a root
(``record_places``), every file with its full hash, held by prax or not.
``original_file`` is the one answer to where an original is read from: the
archive's file, or else a place that still holds the same bytes.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from prax import config

from ..base import _serialized, archive_path, now

ROOT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
# a FAT or SMB share keeps a modification time to two seconds
MTIME_SLACK = 2


def place_roots() -> dict[str, Path]:
    """This host's roots: each one's name and the directory it is seen at
    (``places.roots``). A root another host maps and this one does not is
    absent: its places are named here but out of reach."""
    raw = config.setting("places.roots", default=None) or {}
    if not isinstance(raw, dict):
        raise config.ConfigError(
            "places.roots: a mapping of a root's name to this host's directory"
        )
    out: dict[str, Path] = {}
    for name, where in raw.items():
        if not ROOT_NAME.fullmatch(str(name)) or not where:
            raise config.ConfigError(
                f"places.roots: {name!r} needs a lower-case name and a directory"
            )
        out[str(name)] = Path(str(where))
    return out


def _parts(path: str) -> list[str] | None:
    """A place's path as its parts; None when it is not a relative path with
    ``/`` separators that stays inside its root."""
    if not path or path.startswith("/") or "\\" in path or "\0" in path:
        return None
    parts = path.split("/")
    if any(p in ("", ".", "..") or p.endswith(":") for p in parts):
        return None
    return parts


def original_file(con: sqlite3.Connection, digest: str) -> Path:
    """Where the original of hash ``digest`` is read from: the archive's
    file when prax holds it, else the newest place on a root this host maps
    whose file still has the size and modification time it was hashed
    with, else the archive's path, which does not exist, so a read fails
    as it did before places."""
    held = archive_path(digest)
    if held.exists():
        return held
    roots = place_roots()
    if not roots:
        return held
    for r in con.execute(
        "SELECT root, path, size, mtime FROM places"
        " WHERE sha256 = ? AND gone IS NULL ORDER BY last_seen DESC",
        (digest,),
    ):
        base = roots.get(r[0])
        parts = _parts(r[1])
        if base is None or parts is None:
            continue
        file = base.joinpath(*parts)
        try:
            st = file.stat()
        except OSError:
            continue
        if st.st_size == r[2] and abs(int(st.st_mtime) - r[3]) <= MTIME_SLACK:
            return file
    return held


@_serialized
def record_places(
    con: sqlite3.Connection, root: str, entries: list[dict[str, Any]]
) -> dict[str, int]:
    """What a crawl saw at ``root``: each entry's ``path``, ``size``,
    ``mtime`` (seconds) and ``sha256``. A place seen again is seen now and
    not gone; one whose file changed starts over as a new file. The root
    need not be mapped on this host. Returns ``{"recorded"}``."""
    if not ROOT_NAME.fullmatch(root or ""):
        raise ValueError(f"root: a lower-case name, not {root!r}")
    at = now()
    rows = []
    for e in entries:
        path, digest = str(e.get("path") or ""), str(e.get("sha256") or "").lower()
        if _parts(path) is None:
            raise ValueError(f"path: relative to its root with '/', not {path!r}")
        if not _SHA256.fullmatch(digest):
            raise ValueError(f"sha256: 64 hex digits, not {digest!r}")
        rows.append((root, path, int(e["size"]), int(e["mtime"]), digest, at, at))
    con.executemany(
        "INSERT INTO places (root, path, size, mtime, sha256, first_seen, last_seen)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (root, path) DO UPDATE SET"
        " first_seen = CASE WHEN places.sha256 = excluded.sha256"
        "  THEN places.first_seen ELSE excluded.first_seen END,"
        " size = excluded.size, mtime = excluded.mtime, sha256 = excluded.sha256,"
        " last_seen = excluded.last_seen, gone = NULL",
        rows,
    )
    con.commit()
    return {"recorded": len(rows)}
