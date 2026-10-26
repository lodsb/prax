"""Where an original's bytes lie (plan item AQ, ``docs/media-by-reference.md``).

A place is a root and a path inside it. A root is a name in ``prax.yaml``
(``places.roots``) mapped to the directory where this host sees it, so the
NAS's share is ``//nas/media`` on the desktop and ``/mnt/media`` on the
board and the rows are the same. A crawl records what it saw at a root
(``record_places``), every file with its full hash, held by prax or not.
``original_file`` is the one answer to where an original is read from: the
archive's file, or else a place that still holds the same bytes. A crawl
that ends says so (``end_crawl``), and what it did not see is gone. Where
a file is, its other copies and what exists once are reads of the table
(``places_of``, ``single_copies``, ``duplicate_places``), for the
administrator: a path can be as personal as the file.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from prax import config
from prax.text import mimes

from ..base import _reading, _serialized, archive_path, now

ROOT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
# a FAT or SMB share keeps a modification time to two seconds
MTIME_SLACK = 2
# what a crawl with --reference makes a document without its bytes
# (``places.reference``): recordings and pictures, the user's answer of
# 2026-10-23. Everything else is held, PDFs above all: their figures and
# pages are served out of the original, and a held one outlives the NAS
REFERENCED = ("audio/", "video/", "image/")
# a phone's Live Photo is a picture with a short clip of the same name
# beside it: the clip is a place, and the picture is the document
_LIVE_CLIP = (".mov",)
_LIVE_PICTURE = (".heic", ".heif", ".jpg", ".jpeg")
# icloudpd names a Live Photo's HEVC clip ``IMG_1234_HEVC.MOV`` beside
# ``IMG_1234.HEIC``: 25 of a library's clips were made documents for it
# (2026-10-26)
_LIVE_CLIP_TAGS = ("_HEVC",)
# heal stats a referenced original's file at most this many times a look,
# so a share of 20,000 photos is not walked over the network each time
REACH_CHECKS = 2000


def referenced_kind(mime: str) -> bool:
    """Whether a file of this type is referenced rather than held."""
    kinds = tuple(config.words("places.reference", default=REFERENCED))
    m = (mime or "").lower()
    return m.startswith(kinds) and m not in mimes.DOCUMENT_IMAGES


def referenced_private() -> bool:
    """Whether a referenced original is ``suspected`` from its
    registration (``private.referenced``, on by default; the user,
    2026-10-23): a phone's photos and recordings are personal more often
    than not, so a named token sees one only after a person releases it."""
    said = config.setting("private.referenced", default=True)
    return str(said).lower() not in ("false", "0", "no", "off")


def live_photo_clip(con: sqlite3.Connection, root: str, path: str) -> bool:
    """Whether the file at ``path`` is a Live Photo's clip: a ``.mov``
    with a picture of the same name in the same folder, seen at ``root``,
    or of that name without a downloader's tag (``_LIVE_CLIP_TAGS``)."""
    stem, dot, suffix = path.rpartition(".")
    if not dot or "." + suffix.lower() not in _LIVE_CLIP:
        return False
    stems = [stem] + [
        stem[: -len(t)] for t in _LIVE_CLIP_TAGS if stem.upper().endswith(t)
    ]
    names = [s + e for s in stems for p in _LIVE_PICTURE for e in (p, p.upper())]
    marks = ",".join("?" * len(names))
    return (
        con.execute(
            f"SELECT 1 FROM places WHERE root = ? AND path IN ({marks})"
            " AND gone IS NULL LIMIT 1",
            (root, *names),
        ).fetchone()
        is not None
    )


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
    return _place_file(con, digest, place_roots()) or held


def _place_file(
    con: sqlite3.Connection, digest: str, roots: dict[str, Path]
) -> Path | None:
    """The newest live place of ``digest`` on one of ``roots`` whose file
    has the size and modification time it was hashed with, or None."""
    if not roots:
        return None
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
    return None


def place_roots_of(con: sqlite3.Connection, digest: str) -> list[str]:
    """The roots that hold a live place of ``digest``, by name."""
    return [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT root FROM places WHERE sha256 = ? AND gone IS NULL"
            " ORDER BY root",
            (digest,),
        )
    ]


@_reading
def places_in_brief(
    con: sqlite3.Connection, digest: str, *, limit: int = 5
) -> dict[str, Any] | None:
    """What ``get`` says of where a file lies: the live places (at most
    ``limit``, the newest first), how many there are, and whether this
    host reaches one. None when it has none."""
    rows = con.execute(
        "SELECT root, path FROM places WHERE sha256 = ? AND gone IS NULL"
        " ORDER BY last_seen DESC, root, path",
        (digest,),
    ).fetchall()
    if not rows:
        return None
    out: dict[str, Any] = {
        "copies": len(rows),
        "reachable": _place_file(con, digest, place_roots()) is not None,
        "places": [{"root": r[0], "path": r[1]} for r in rows[: max(0, limit)]],
    }
    return out


@_reading
def referenced_originals(con: sqlite3.Connection) -> dict[str, int]:
    """The documents whose original prax does not hold, and their bytes
    (each file's size as a crawl last saw it): what a backup cannot bring
    back."""
    row = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(s), 0) FROM ("
        " SELECT (SELECT MAX(size) FROM places p WHERE p.sha256 = d.hash) AS s"
        " FROM documents d WHERE d.held = 0)"
    ).fetchone()
    return {"documents": int(row[0]), "bytes": int(row[1])}


def unreachable_originals(
    con: sqlite3.Connection, *, limit: int
) -> list[dict[str, Any]]:
    """Live referenced documents this host reaches no place of, each with
    the roots its places are on. A root this host does not map, or whose
    directory is not there (the share not mounted), answers for all of its
    files at once; a mounted one is looked at file by file, at most
    ``REACH_CHECKS`` files a call."""
    roots = place_roots()
    present = {n: p for n, p in roots.items() if p.is_dir()}
    out: list[dict[str, Any]] = []
    checks = 0
    for r in con.execute(
        "SELECT id, title, hash FROM documents WHERE held = 0"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
    ):
        on = place_roots_of(con, r["hash"])
        if any(n in present for n in on):
            if checks >= REACH_CHECKS:
                continue
            checks += 1
            if _place_file(con, r["hash"], present) is not None:
                continue
        out.append({"id": r["id"], "title": r["title"], "roots": on})
        if len(out) >= limit:
            break
    return out


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


def _prefix(prefix: str) -> str:
    """A crawl's folder inside its root: empty for the whole root."""
    prefix = (prefix or "").strip("/")
    if prefix and _parts(prefix) is None:
        raise ValueError(f"prefix: a folder inside the root with '/', not {prefix!r}")
    return prefix


def _under(prefix: str) -> tuple[str, list[str]]:
    """The SQL that keeps a root's rows to those under ``prefix``."""
    if not prefix:
        return "", []
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return " AND path LIKE ? ESCAPE '\\'", [like + "/%"]


@_serialized
def end_crawl(
    con: sqlite3.Connection, root: str, since: str, prefix: str = ""
) -> dict[str, int]:
    """A crawl of ``root`` (or of its folder ``prefix``) that began at
    ``since`` is over: each place in it the crawl did not see again is
    gone from now. The row stays, so prax can say where a file was.
    Returns ``{"gone"}``."""
    if not ROOT_NAME.fullmatch(root or ""):
        raise ValueError(f"root: a lower-case name, not {root!r}")
    clause, args = _under(_prefix(prefix))
    cur = con.execute(
        "UPDATE places SET gone = ? WHERE root = ? AND gone IS NULL"
        " AND last_seen < ?" + clause,
        [now(), root, since, *args],
    )
    con.commit()
    return {"gone": cur.rowcount}


def _place(r: Any, mapped: dict[str, Path]) -> dict[str, Any]:
    """A row of ``root, path, size, first_seen, last_seen, gone`` as an
    answer's place."""
    out = {
        "root": r[0],
        "path": r[1],
        "size": r[2],
        "first_seen": r[3],
        "last_seen": r[4],
        "mapped": r[0] in mapped,
    }
    if r[5]:
        out["gone"] = r[5]
    return out


@_reading
def places_of(
    con: sqlite3.Connection,
    *,
    digest: str | None = None,
    doc_id: int | None = None,
    root: str | None = None,
    path: str | None = None,
) -> dict[str, Any] | None:
    """Where the file of a hash, a document or a place lies: every place
    of its hash, the live ones first, each with ``mapped`` (whether this
    host maps its root) and, when gone, the moment it went. A gone place
    whose bytes turned up at a place first seen after it was last seen
    says ``moved_to``. ``copies`` counts the live places, ``held`` says
    whether the archive has the bytes too. None when the hash, the
    document or the place is unknown."""
    if doc_id is not None:
        row = con.execute(
            "SELECT hash FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        digest = row[0] if row else None
    elif root is not None and path is not None:
        row = con.execute(
            "SELECT sha256 FROM places WHERE root = ? AND path = ?", (root, path)
        ).fetchone()
        digest = row[0] if row else None
    if not digest:
        return None
    digest = digest.lower()
    rows = con.execute(
        "SELECT root, path, size, first_seen, last_seen, gone FROM places"
        " WHERE sha256 = ? ORDER BY gone IS NOT NULL, last_seen DESC, root, path",
        (digest,),
    ).fetchall()
    doc = con.execute("SELECT id FROM documents WHERE hash = ?", (digest,)).fetchone()
    if not rows and doc is None:
        return None
    mapped = place_roots()
    places = [_place(r, mapped) for r in rows]
    live = [p for p in places if "gone" not in p]
    for p in places:
        if "gone" in p:
            later = [q for q in live if q["first_seen"] > p["last_seen"]]
            if later:
                p["moved_to"] = f"{later[0]['root']}/{later[0]['path']}"
    out: dict[str, Any] = {
        "hash": digest,
        "copies": len(live),
        "held": archive_path(digest).exists(),
        "places": places,
    }
    if doc is not None:
        out["doc_id"] = doc[0]
    return out


@_reading
def single_copies(
    con: sqlite3.Connection, *, root: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """The files with one live place and no copy in the archive, largest
    first: what one disk's failure would lose. ``root`` keeps those whose
    one place is there. Returns the count and bytes of all of them and the
    first ``limit``."""
    rows = con.execute(
        "SELECT root, path, size, first_seen, last_seen, gone, sha256 FROM places"
        " WHERE gone IS NULL"
        " AND sha256 NOT IN (SELECT hash FROM documents WHERE held = 1)"
        " GROUP BY sha256 HAVING COUNT(*) = 1"
        + (" AND root = ?" if root else "")
        + " ORDER BY size DESC",
        (root,) if root else (),
    ).fetchall()
    mapped = place_roots()
    kept = rows
    return {
        "files": len(kept),
        "bytes": sum(r[2] for r in kept),
        "largest": [{**_place(r, mapped), "hash": r[6]} for r in kept[: max(0, limit)]],
    }


@_reading
def duplicate_places(
    con: sqlite3.Connection,
    *,
    root: str | None = None,
    min_size: int = 0,
    limit: int = 50,
    places_shown: int = 20,
) -> dict[str, Any]:
    """The files that lie at more than one live place, those whose extra
    copies take the most space first (the size times the copies beyond
    one). ``root`` keeps the groups with a place there. Each group lists
    at most ``places_shown`` of its places and counts all of them. Returns
    the count of groups, the bytes the extra copies take, and the first
    ``limit`` groups."""
    groups = con.execute(
        "SELECT sha256, MAX(size), COUNT(*) FROM places WHERE gone IS NULL"
        " AND size >= ? GROUP BY sha256 HAVING COUNT(*) > 1"
        + (" AND SUM(root = ?) > 0" if root else "")
        + " ORDER BY MAX(size) * (COUNT(*) - 1) DESC",
        (min_size, root) if root else (min_size,),
    ).fetchall()
    mapped = place_roots()
    out = []
    for digest, size, n in groups[: max(0, limit)]:
        rows = con.execute(
            "SELECT root, path, size, first_seen, last_seen, gone FROM places"
            " WHERE sha256 = ? AND gone IS NULL ORDER BY root, path LIMIT ?",
            (digest, places_shown),
        ).fetchall()
        doc = con.execute(
            "SELECT id FROM documents WHERE hash = ?", (digest,)
        ).fetchone()
        group: dict[str, Any] = {
            "hash": digest,
            "size": size,
            "copies": n,
            "places": [_place(r, mapped) for r in rows],
        }
        if doc is not None:
            group["doc_id"] = doc[0]
        out.append(group)
    return {
        "groups": len(groups),
        "extra_bytes": sum(size * (n - 1) for _, size, n in groups),
        "largest": out,
    }
