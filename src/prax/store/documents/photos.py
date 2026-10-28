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
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from prax.text import mimes

from ..base import (
    _archive_bytes,
    _guards,
    _reading,
    _serialized,
    archive_path,
    is_digest,
    now,
)
from .meta import _put_meta, _refresh_document_field, get_meta
from .places import _place_file, place_roots

# the copies a photo has, by the key ``meta.photo`` keeps each one's hash
PHOTO_COPIES = ("thumb", "preview")


# a referenced picture whose file on a mounted root is not the one hashed
# is passed over this long before it is looked at again
UNREACHED_REST = 3600.0
_UNREACHED: dict[int, float] = {}


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
    failing and being marked. A picture with no live place on a root whose
    directory is here is passed over in the query; one whose file on a
    mounted root is not the one hashed (moved, or touched since the
    crawl) is looked at once and then passed over for ``UNREACHED_REST``
    seconds, so a run of them never hides the pictures after them (the
    review of 2026-10-28)."""
    roots = {n: p for n, p in place_roots().items() if p.is_dir()}
    out: list[int] = []
    clock = time.monotonic()
    rows = con.execute(
        "SELECT id, hash, mime, held FROM documents d"
        " WHERE mime LIKE 'image/%' AND json_extract(meta, '$.retired') IS NULL"
        " AND coalesce(json_extract(meta, '$.photo.v'), 0) < ?"
        " AND (held = 1 OR EXISTS (SELECT 1 FROM places p WHERE p.sha256 = d.hash"
        "  AND p.gone IS NULL AND p.root IN (SELECT value FROM json_each(?))))"
        " ORDER BY id",
        (version, json.dumps(sorted(roots))),
    )
    for r in rows:
        if len(out) >= limit:
            break
        doc = int(r["id"])
        if doc in skip or not mimes.is_picture(r["mime"]):
            continue
        if not r["held"]:
            if _UNREACHED.get(doc, 0.0) > clock:
                continue
            if _place_file(con, r["hash"], roots) is None:
                _UNREACHED[doc] = clock + UNREACHED_REST
                continue
        out.append(doc)
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
    if "gps" in photo and not _a_position(photo["gps"]):
        del photo["gps"]
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


def _a_position(gps: Any) -> bool:
    """Whether a worker's ``gps`` is a position: finite degrees in range."""
    try:
        lat, lon = float(gps["lat"]), float(gps["lon"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(lat + lon) and abs(lat) <= 90 and abs(lon) <= 180


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
    if row is None or not is_digest(row[0]):
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


# a landmark may lie past the picture's edge, where the detector placed an
# eye of a face the frame cuts (5 of 2,022 pictures, 2026-10-27): kept
# where it was put, since the alignment needs it there
LANDMARK_REACH = 0.25


def _fraction(value: Any, reach: float = 0.01) -> float:
    x = float(value)
    if not -reach <= x <= 1 + reach:
        raise ValueError(f"not a fraction of the picture: {value!r}")
    return x


def _checked_face(face: dict[str, Any]) -> tuple[str, str, float, int]:
    """A face as the worker posted it, checked: its box and landmarks as
    JSON, its score and width in pixels. ValueError for a misshapen one."""
    box = [min(1.0, max(0.0, _fraction(v))) for v in face["box"]]
    marks = [
        [_fraction(x, LANDMARK_REACH), _fraction(y, LANDMARK_REACH)]
        for x, y in face["landmarks"]
    ]
    if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
        raise ValueError(f"a face's box is x, y, w, h: {face['box']!r}")
    if len(marks) != FACE_LANDMARKS:
        raise ValueError(f"a face has {FACE_LANDMARKS} landmarks")
    score = float(face["score"])
    if not 0 <= score <= 1:
        raise ValueError(f"a score is 0 to 1: {score!r}")
    return json.dumps(box), json.dumps(marks), score, max(0, int(face["px"]))


# how much a new face's box must cover an old one's to be the same face
# read again: past it, what a person said of the old face moves to it
SAME_FACE_IOU = 0.4


def _iou(a: list[float], b: list[float]) -> float:
    """The overlap of two ``x, y, w, h`` boxes over their union."""
    w = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = w * h
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _successors(
    old: dict[int, list[float]], new: dict[int, list[float]]
) -> dict[int, int]:
    """Each old face the new reading found again, with its new face: the
    pairs of most overlap first, each face in one pair at most."""
    pairs = sorted(
        ((_iou(ob, nb), o, n) for o, ob in old.items() for n, nb in new.items()),
        reverse=True,
    )
    out: dict[int, int] = {}
    taken: set[int] = set()
    for iou, o, n in pairs:
        if iou < SAME_FACE_IOU:
            break
        if o not in out and n not in taken:
            out[o] = n
            taken.add(n)
    return out


def _carry_answers(con: sqlite3.Connection, moves: dict[int, int], at: str) -> int:
    """What a person said of each old face, said of its successor: the
    live answer and refusals ended on the old face and written again on
    the new one with their run, person, edge and time, so an undo of
    their action takes them back as before. Returns the faces carried."""
    carried = 0
    for old, new in moves.items():
        for r in con.execute(
            "SELECT * FROM face_names WHERE face_id = ? AND ended_at IS NULL", (old,)
        ).fetchall():
            con.execute(
                "UPDATE face_names SET ended_at = ? WHERE id = ?", (at, r["id"])
            )
            con.execute(
                "INSERT INTO face_names (face_id, verdict, entity_id, edge_id, by,"
                " run, said_at) VALUES (?,?,?,?,?,?,?)",
                (
                    new,
                    r["verdict"],
                    r["entity_id"],
                    r["edge_id"],
                    r["by"],
                    r["run"],
                    r["said_at"],
                ),
            )
            carried += 1
        for r in con.execute(
            "SELECT * FROM face_refusals WHERE face_id = ? AND ended_at IS NULL",
            (old,),
        ).fetchall():
            con.execute(
                "UPDATE face_refusals SET ended_at = ? WHERE id = ?", (at, r["id"])
            )
            con.execute(
                "INSERT INTO face_refusals (face_id, entity_id, by, run, said_at)"
                " VALUES (?,?,?,?,?)",
                (new, r["entity_id"], r["by"], r["run"], r["said_at"]),
            )
    return carried


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
    reading, of which copy at what size, and how many. What a person said
    of an old face moves to the new face whose box covers it
    (``SAME_FACE_IOU``); an answer whose face was not found again stays on
    the ended face, its ``depicts`` fact with it, and its action's undo
    still takes it back. A reading older than the one the picture holds
    changes nothing (a worker's post after its lease ran out). Returns
    ``found``, ``none`` or ``stale``."""
    checked = [_checked_face(f) for f in faces]
    meta = get_meta(con, doc_id)
    if int((meta.get("faces") or {}).get("v") or 0) > version:
        return "stale"
    at = now()
    old = {
        int(r[0]): json.loads(r[1])
        for r in con.execute(
            "SELECT id, box FROM faces WHERE doc_id = ? AND gone_at IS NULL",
            (doc_id,),
        )
    }
    con.execute(
        "UPDATE faces SET gone_at = ? WHERE doc_id = ? AND gone_at IS NULL",
        (at, doc_id),
    )
    new: dict[int, list[float]] = {}
    for box, marks, score, px in checked:
        cur = con.execute(
            "INSERT INTO faces (doc_id, box, landmarks, score, px, detector, found_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc_id, box, marks, score, px, by, at),
        )
        new[int(cur.lastrowid or 0)] = json.loads(box)
    if old and new:
        _carry_answers(con, _successors(old, new), at)
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
