"""A picture's own facts and its two smaller copies (the photos step,
``prax.steps.photos``; ``docs/biography.md`` §7).

The step's worker reads a picture's EXIF and makes a thumbnail and a
preview (``prax.parsers.photos``). The door files both copies in the
archive by their own hash, like a scanned page's crop: never in SQLite
(invariant 2), and kept when the share the original lies on is gone. What
the EXIF says goes into ``meta.photo``, and when it was taken into
``meta.taken``, which is not ``meta.published``: a photo is made, not
published. A person's date is never replaced.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from prax.text import mimes

from ..base import (
    _archive_bytes,
    _guards,
    _reading,
    _serialized,
    archive_path,
    now,
)
from .meta import _put_meta, _refresh_document_field, get_meta
from .places import _place_file, place_roots

# the copies a photo has, by the key ``meta.photo`` keeps each one's hash
PHOTO_COPIES = ("thumb", "preview")


@_reading
def photos_due(
    con: sqlite3.Connection,
    version: int,
    *,
    limit: int,
    skip: frozenset[int] = frozenset(),
) -> list[int]:
    """Pictures with no reading of ``version`` (or a later one), held or
    referenced, oldest first, whose original this door reaches now: a
    referenced one whose share is not mounted waits for it, rather than
    failing and being marked. A root whose directory is absent is passed
    over whole; a mounted one is looked at file by file, at most four
    times ``limit`` files a call."""
    roots = {n: p for n, p in place_roots().items() if p.is_dir()}
    out: list[int] = []
    checks = 0
    rows = con.execute(
        "SELECT id, hash, mime, held FROM documents"
        " WHERE mime LIKE 'image/%' AND json_extract(meta, '$.retired') IS NULL"
        " AND coalesce(json_extract(meta, '$.photo.v'), 0) < ?"
        + ("" if roots else " AND held = 1")
        + " ORDER BY id",
        (version,),
    )
    for r in rows:
        if len(out) >= limit or checks >= 4 * limit:
            break
        if r["id"] in skip or not mimes.is_picture(r["mime"]):
            continue
        if not r["held"]:
            checks += 1
            if _place_file(con, r["hash"], roots) is None:
                continue
        out.append(int(r["id"]))
    return out


@_reading
def photo_counts(con: sqlite3.Connection, version: int) -> dict[str, int]:
    """How many pictures are read at ``version``, failed at it, and wait."""
    row = con.execute(
        "SELECT"
        " sum(coalesce(json_extract(meta, '$.photo.v'), 0) >= ?"
        "     AND json_extract(meta, '$.photo.error') IS NULL),"
        " sum(coalesce(json_extract(meta, '$.photo.v'), 0) >= ?"
        "     AND json_extract(meta, '$.photo.error') IS NOT NULL),"
        " sum(coalesce(json_extract(meta, '$.photo.v'), 0) < ?)"
        " FROM documents WHERE mime LIKE 'image/%'"
        " AND mime NOT IN (" + ",".join("?" * len(mimes.DOCUMENT_IMAGES)) + ")"
        " AND json_extract(meta, '$.retired') IS NULL",
        (version, version, version, *sorted(mimes.DOCUMENT_IMAGES)),
    ).fetchone()
    return {"read": row[0] or 0, "failed": row[1] or 0, "waiting": row[2] or 0}


@_serialized
def apply_photo(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    facts: dict[str, Any],
    copies: dict[str, bytes],
    version: int,
    by: str,
) -> str:
    """A picture's reading: each copy filed in the archive, the facts in
    ``meta.photo``, the date it was taken in ``meta.taken`` unless a person
    gave one, and the document field rewritten, so a search finds it by
    its month and its camera. Returns ``read``."""
    meta = get_meta(con, doc_id)
    photo: dict[str, Any] = {"v": version, "by": by, "at": now()}
    for key in ("camera", "lens", "gps", "width", "height"):
        if facts.get(key) not in (None, ""):
            photo[key] = facts[key]
    for key in PHOTO_COPIES:
        if copies.get(key):
            photo[key] = _archive_bytes(copies[key])
    meta["photo"] = photo
    held = meta.get("taken") or {}
    if facts.get("taken") and held.get("by") != "human":
        meta["taken"] = {"date": facts["taken"], "precision": "second", "by": "exif"}
    _put_meta(con, doc_id, meta)
    _refresh_document_field(con, doc_id)
    con.commit()
    return "read"


@_serialized
def photo_failed(
    con: sqlite3.Connection, doc_id: int, *, error: str, version: int, by: str
) -> str:
    """A picture the reader could not open: kept as ``meta.photo.error``
    at this version, so it is not handed out again until a later one."""
    meta = get_meta(con, doc_id)
    meta["photo"] = {"v": version, "by": by, "at": now(), "error": error[:300]}
    _put_meta(con, doc_id, meta)
    con.commit()
    return "failed"


@_guards("doc", lambda: None)
@_reading
def photo_copy(con: sqlite3.Connection, doc_id: int, which: str) -> Path | None:
    """The file of a picture's ``thumb`` or ``preview``, or None when it has
    none (not read yet, or not a picture)."""
    if which not in PHOTO_COPIES:
        raise ValueError(f"a copy is one of {PHOTO_COPIES}")
    row = con.execute(
        "SELECT json_extract(meta, '$.photo.' || ?) FROM documents WHERE id = ?",
        (which, doc_id),
    ).fetchone()
    if row is None or not row[0]:
        return None
    path = archive_path(str(row[0]))
    return path if path.exists() else None


# --------------------------------------------------------------- the faces
# Where the faces in a picture are (plan item AT step 1, the faces step,
# ``prax.ml.faces``): read in its preview, so a picture is due once the
# photos step has made one, whether or not its share is mounted.
FACE_LANDMARKS = 5


@_reading
def faces_due(
    con: sqlite3.Connection,
    version: int,
    *,
    limit: int,
    skip: frozenset[int] = frozenset(),
) -> list[int]:
    """Pictures with a preview and no faces reading of ``version`` (or a
    later one), oldest first."""
    rows = con.execute(
        "SELECT id FROM documents"
        " WHERE json_extract(meta, '$.photo.preview') IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
        " AND coalesce(json_extract(meta, '$.faces.v'), 0) < ? ORDER BY id",
        (version,),
    )
    out: list[int] = []
    for (doc,) in rows:
        if doc not in skip:
            out.append(int(doc))
            if len(out) >= limit:
                break
    return out


@_reading
def face_counts(con: sqlite3.Connection, version: int) -> dict[str, int]:
    """Pictures read at ``version``, how many hold a face, the faces found,
    and the pictures that wait."""
    row = con.execute(
        "SELECT"
        " sum(coalesce(json_extract(meta, '$.faces.v'), 0) >= ?),"
        " sum(coalesce(json_extract(meta, '$.faces.v'), 0) >= ?"
        "     AND json_extract(meta, '$.faces.n') > 0),"
        " sum(coalesce(json_extract(meta, '$.faces.v'), 0) < ?)"
        " FROM documents WHERE json_extract(meta, '$.photo.preview') IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL",
        (version, version, version),
    ).fetchone()
    found = con.execute("SELECT count(*) FROM faces WHERE gone_at IS NULL").fetchone()
    return {
        "read": row[0] or 0,
        "with_faces": row[1] or 0,
        "faces": int(found[0]),
        "waiting": row[2] or 0,
    }


def _fraction(value: Any) -> float:
    x = float(value)
    if not -0.01 <= x <= 1.01:  # a landmark may sit a little past the edge
        raise ValueError(f"not a fraction of the picture: {value!r}")
    return min(1.0, max(0.0, x))


def _checked_face(face: dict[str, Any]) -> tuple[str, str, float, int]:
    """A face as the worker posted it, checked: its box and landmarks as
    JSON, its score and width in pixels. ValueError for a misshapen one."""
    box = [_fraction(v) for v in face["box"]]
    marks = [[_fraction(x), _fraction(y)] for x, y in face["landmarks"]]
    if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
        raise ValueError(f"a face's box is x, y, w, h: {face['box']!r}")
    if len(marks) != FACE_LANDMARKS:
        raise ValueError(f"a face has {FACE_LANDMARKS} landmarks")
    score = float(face["score"])
    if not 0 <= score <= 1:
        raise ValueError(f"a score is 0 to 1: {score!r}")
    return json.dumps(box), json.dumps(marks), score, max(0, int(face["px"]))


@_serialized
def apply_faces(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    faces: list[dict[str, Any]],
    version: int,
    by: str,
    width: int,
    height: int,
    source: str = "preview",
) -> str:
    """A picture's faces reading: the faces an earlier reading found ended
    (``gone_at``), the new ones written, and ``meta.faces`` saying which
    reading, of which copy at what size, and how many. Returns ``found``
    or ``none``."""
    checked = [_checked_face(f) for f in faces]
    meta = get_meta(con, doc_id)
    at = now()
    con.execute(
        "UPDATE faces SET gone_at = ? WHERE doc_id = ? AND gone_at IS NULL",
        (at, doc_id),
    )
    con.executemany(
        "INSERT INTO faces (doc_id, box, landmarks, score, px, detector, found_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(doc_id, box, marks, score, px, by, at) for box, marks, score, px in checked],
    )
    meta["faces"] = {
        "v": version,
        "by": by,
        "at": at,
        "n": len(checked),
        "from": source,
        "width": int(width),
        "height": int(height),
    }
    _put_meta(con, doc_id, meta)
    con.commit()
    return "found" if checked else "none"


@_guards("doc", list)
@_reading
def faces_of(con: sqlite3.Connection, doc_id: int) -> list[dict[str, Any]]:
    """The faces a picture holds now, the most certain first: each its
    ``id``, ``box`` and ``landmarks`` (fractions of the upright picture),
    ``score``, ``px`` and ``detector``."""
    return [
        {
            "id": int(r["id"]),
            "box": json.loads(r["box"]),
            "landmarks": json.loads(r["landmarks"]),
            "score": float(r["score"]),
            "px": int(r["px"]),
            "detector": r["detector"],
        }
        for r in con.execute(
            "SELECT * FROM faces WHERE doc_id = ? AND gone_at IS NULL"
            " ORDER BY score DESC, id",
            (doc_id,),
        )
    ]
