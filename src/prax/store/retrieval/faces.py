"""What a face looks like, and which faces look alike (plan item AT step
2; ``prax.ml.faces``, the ``facevectors`` step).

A face found by the faces step gets a vector of 128 numbers from SFace,
made on the worker, and a small square picture of it for the Review page.
The vectors live in a usearch index of their own beside the text ones,
``vectors-face-<model>.usearch`` keyed by the face's id, with a delta
that takes new vectors and a merge that folds it in: invariant 1's
pattern, the same code as the chunks' (``base._INDEX_DIMS`` gives it its
dimension). ``faces.vector_model`` records which faces it holds; a door
killed between two saves forgets the rows whose vector was lost
(``reconcile_face_vectors``), so those faces are read again.

Faces are biometric. Every read here goes through the wall: a face of a
picture the viewer may not see is not there, and no named token's route
reaches these reads.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from prax import config
from prax.ml import faces as faces_ml

from ..base import (
    _INDEX_DIMS,
    _add_to_delta,
    _archive_bytes,
    _delta_path,
    _get_vector,
    _knn,
    _reading,
    _serialized,
    archive_path,
    document_hidden,
    hidden_documents,
    now,
)
from .vectors import RECONCILE_SLACK, _index_has, _save_delta

FACE_MODEL = faces_ml.RECOGNIZER
FACES_PAGE = 200  # crops a page of the Review page shows at most
FACE_ORDERS = ("least", "smallest", "surest")
# the bands the Review page counts faces in
SCORE_BANDS = (0.95, 0.9)
SIZE_BANDS = (40, 80)


def face_index_path(model: str) -> Path:
    """The face index of ``model``, registered with its dimension."""
    path = config.data_dir() / f"vectors-face-{model}.usearch"
    _INDEX_DIMS[str(path)] = faces_ml.DIM
    return path


@_reading
def face_vectors_due(
    con: sqlite3.Connection,
    model: str,
    *,
    limit: int,
    skip: frozenset[int] = frozenset(),
) -> list[dict[str, Any]]:
    """Pictures holding faces with no vector of ``model``, oldest first,
    each with those faces' ids, boxes and landmarks: ``{doc_id, faces}``."""
    out: dict[int, list[dict[str, Any]]] = {}
    for r in con.execute(
        "SELECT id, doc_id, box, landmarks FROM faces WHERE gone_at IS NULL"
        " AND (vector_model IS NULL OR vector_model != ?) ORDER BY doc_id, id",
        (model,),
    ):
        doc = int(r["doc_id"])
        if doc in skip:
            continue
        if doc not in out and len(out) >= limit:
            break
        out.setdefault(doc, []).append(
            {
                "id": int(r["id"]),
                "box": json.loads(r["box"]),
                "landmarks": json.loads(r["landmarks"]),
            }
        )
    return [{"doc_id": d, "faces": f} for d, f in out.items()]


def _checked_vector(values: Any) -> list[float]:
    vec = [float(v) for v in values]
    if len(vec) != faces_ml.DIM or not all(math.isfinite(v) for v in vec):
        raise ValueError(f"a face's vector is {faces_ml.DIM} finite numbers")
    return vec


@_serialized
def apply_face_vectors(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    faces: list[dict[str, Any]],
    model: str,
) -> str:
    """A picture's faces as vectors: each added to the face index's delta
    (written by ``save_face_vectors``), its crop filed in the archive, and
    the face's row saying which model holds it. A face that is not a live
    face of this picture is refused (ValueError), and nothing is written."""
    import numpy as np

    live = {
        int(r[0])
        for r in con.execute(
            "SELECT id FROM faces WHERE doc_id = ? AND gone_at IS NULL", (doc_id,)
        )
    }
    rows = []
    for f in faces:
        face = int(f["id"])
        if face not in live:
            raise ValueError(f"face {face} is not a face of document {doc_id} now")
        rows.append((face, _checked_vector(f["vector"]), f.get("crop")))
    if not rows:
        return "none"
    _add_to_delta(
        face_index_path(model),
        [face for face, _, _ in rows],
        np.asarray([vec for _, vec, _ in rows], dtype=np.float32),
    )
    at = now()
    con.executemany(
        "UPDATE faces SET vector_model = ?, vector_at = ?,"
        " crop = coalesce(?, crop) WHERE id = ?",
        [
            (model, at, _archive_bytes(crop) if crop else None, face)
            for face, _, crop in rows
        ],
    )
    con.commit()
    return "vectored"


def save_face_vectors(model: str = FACE_MODEL) -> dict[str, Any]:
    """Write the face index's delta, merged into its main file when large
    (``retrieval.vectors._save_delta``, as the chunks' is)."""
    return _save_delta(face_index_path(model))


@_serialized
def reconcile_face_vectors(
    con: sqlite3.Connection, model: str = FACE_MODEL
) -> dict[str, int]:
    """Forget the rows written since the face index was last saved whose
    vector it does not hold, so those faces are read again: what a door
    killed between two saves lost (``reconcile_unsaved`` for the chunks)."""
    path = face_index_path(model)
    files = [f for f in (path, _delta_path(path)) if f.exists()]
    since = "0"
    if files:
        saved = max(f.stat().st_mtime for f in files) - RECONCILE_SLACK
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(saved))
    ids = [
        int(r[0])
        for r in con.execute(
            "SELECT id FROM faces WHERE vector_model = ? AND vector_at >= ?",
            (model, since),
        )
    ]
    held = set(_index_has(path, ids)) if files else set()
    lost = [i for i in ids if i not in held]
    con.executemany(
        "UPDATE faces SET vector_model = NULL, vector_at = NULL WHERE id = ?",
        [(i,) for i in lost],
    )
    con.commit()
    return {"checked": len(ids), "forgotten": len(lost)}


def _face_doc(con: sqlite3.Connection, face_id: int) -> int | None:
    """The picture a live face is in, when the viewer may see it."""
    row = con.execute(
        "SELECT doc_id FROM faces WHERE id = ? AND gone_at IS NULL", (face_id,)
    ).fetchone()
    if row is None or document_hidden(con, int(row[0])):
        return None
    return int(row[0])


@_reading
def face_crop(con: sqlite3.Connection, face_id: int) -> Path | None:
    """The file of a face's crop, or None (no such face, not yet read, or
    in a picture the viewer may not see)."""
    if _face_doc(con, face_id) is None:
        return None
    row = con.execute("SELECT crop FROM faces WHERE id = ?", (face_id,)).fetchone()
    if not row or not row[0]:
        return None
    path = archive_path(str(row[0]))
    return path if path.exists() else None


@_reading
def similar_faces(
    con: sqlite3.Connection, face_id: int, *, k: int = 12, model: str = FACE_MODEL
) -> list[dict[str, Any]]:
    """The faces nearest to this one in the face index, the nearest first:
    each its ``id``, ``doc_id`` and cosine ``similarity``, and whether it
    reaches ``prax.ml.faces.MATCH``. Hidden pictures' faces are left out."""
    if _face_doc(con, face_id) is None:
        raise KeyError(f"no such face: {face_id}")
    path = face_index_path(model)
    vec = _get_vector(path, face_id)
    if vec is None:
        return []
    near = _knn(path, vec, k * 2 + 1)
    ids = [key for key, _ in near if key != face_id]
    docs = {
        int(r[0]): int(r[1])
        for r in con.execute(
            "SELECT id, doc_id FROM faces WHERE gone_at IS NULL"
            " AND id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        )
    }
    hidden = hidden_documents(con)
    out = []
    for key, distance in near:
        if key == face_id or key not in docs or docs[key] in hidden:
            continue
        sim = round(1.0 - float(distance), 4)
        out.append(
            {
                "id": key,
                "doc_id": docs[key],
                "similarity": sim,
                "match": sim >= faces_ml.MATCH,
            }
        )
        if len(out) >= k:
            break
    return out


def _band(value: float, lines: tuple[float, ...]) -> int:
    return next((i for i, line in enumerate(lines) if value >= line), len(lines))


@_reading
def faces_page(
    con: sqlite3.Connection,
    *,
    order: str = "least",
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Faces to look at, with their crops: the least sure first
    (``least``), the smallest (``smallest``) or the surest (``surest``),
    and the counts by score and by size a judgement of the detector
    starts from. Hidden pictures' faces are left out of both."""
    if order not in FACE_ORDERS:
        raise ValueError(f"order is one of {FACE_ORDERS}")
    hidden = sorted(hidden_documents(con))
    seen = "gone_at IS NULL AND doc_id NOT IN (SELECT value FROM json_each(?))"
    scores = [0] * (len(SCORE_BANDS) + 1)
    sizes = [0] * (len(SIZE_BANDS) + 1)
    vectored = 0
    docs: set[int] = set()
    for r in con.execute(
        f"SELECT doc_id, score, px, vector_model FROM faces WHERE {seen}",
        (json.dumps(hidden),),
    ):
        scores[_band(float(r["score"]), SCORE_BANDS)] += 1
        sizes[sum(int(r["px"]) >= line for line in SIZE_BANDS)] += 1
        vectored += r["vector_model"] is not None
        docs.add(int(r["doc_id"]))
    pictures = len(docs)
    sort = {
        "least": "score ASC, id",
        "smallest": "px ASC, id",
        "surest": "score DESC, id",
    }[order]
    limit = max(1, min(limit, FACES_PAGE))
    items = [
        {
            "id": int(r["id"]),
            "doc_id": int(r["doc_id"]),
            "score": float(r["score"]),
            "px": int(r["px"]),
            "crop": bool(r["crop"]),
        }
        for r in con.execute(
            f"SELECT id, doc_id, score, px, crop FROM faces WHERE {seen}"
            f" ORDER BY {sort} LIMIT ? OFFSET ?",
            (json.dumps(hidden), limit, max(0, offset)),
        )
    ]
    return {
        "order": order,
        "total": sum(scores),
        "pictures": pictures,
        "vectored": vectored,
        "by_score": dict(
            zip(("0.95 and over", "0.9 to 0.95", "under 0.9"), scores, strict=True)
        ),
        "by_size": dict(
            zip(("under 40 px", "40 to 80 px", "80 px and over"), sizes, strict=True)
        ),
        "items": items,
    }
