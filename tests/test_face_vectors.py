"""What a face looks like (plan item AT step 2): the alignment OpenCV's
recogniser uses, the facevectors step from preview to the face index, the
faces most like one, the crops and the Review page's list, through the
wall. No model is fetched: the vector is a stand-in."""

from __future__ import annotations

import io
import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from prax import store, worker
from prax.ml import faces, fetch
from prax.parsers import photos
from prax.steps import base
from prax.steps import faces as step_faces

pytestmark = pytest.mark.skipif(
    not store.vectors_available(), reason="usearch is not installed"
)


def test_the_similarity_transform_is_recovered() -> None:
    angle, scale, shift = 0.3, 2.5, np.array([10.0, -4.0])
    rot = np.array(
        [[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]]
    )
    src = np.array(faces.TEMPLATE)
    dst = (scale * (rot @ src.T)).T + shift
    m = faces.similarity_transform(src, dst)
    assert m[:, :2] == pytest.approx(scale * rot, abs=1e-9)
    assert m[:, 2] == pytest.approx(shift, abs=1e-9)


def test_a_face_on_the_template_aligns_to_itself() -> None:
    rng = np.random.default_rng(7)
    pixels = rng.integers(0, 255, (112, 112, 3), dtype=np.uint8)
    image = Image.fromarray(pixels)
    aligned = faces.align(image, faces.TEMPLATE)
    assert aligned.size == (faces.ALIGNED_PX, faces.ALIGNED_PX)
    # the identity, sampled where OpenCV samples: the picture itself
    assert np.abs(np.asarray(aligned, np.int16) - pixels.astype(np.int16)).max() <= 1
    shot = Image.open(io.BytesIO(faces.crop(image, (0.25, 0.25, 0.5, 0.5))))
    assert shot.size == (faces.CROP_PX, faces.CROP_PX) and shot.format == "JPEG"


def test_the_recogniser_file_must_be_the_one_measured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wrong = tmp_path / "sface.onnx"
    wrong.write_bytes(b"not the model")
    monkeypatch.setattr(fetch, "model_file", lambda *a, **k: wrong)
    faces._recognizer.cache_clear()
    with pytest.raises(RuntimeError, match="not the SFace file"):
        faces._recognizer()
    faces._recognizer.cache_clear()


def _jpeg(size: tuple[int, int], shade: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (shade, 90, 120)).save(buf, "JPEG")
    return buf.getvalue()


def _picture_with_faces(con: sqlite3.Connection, shade: int, n: int) -> list[int]:
    doc = int(
        store.register(con, _jpeg((40, 30), shade), mime="image/jpeg", title="p.jpg")[
            "doc_id"
        ]
    )
    store.apply_photo(
        con,
        doc,
        facts={},
        copies={"preview": _jpeg((200, 150), shade)},
        version=photos.VERSION,
        by="test",
    )
    store.apply_faces(
        con,
        doc,
        faces=[
            {
                "box": [0.1 + 0.3 * i, 0.2, 0.2, 0.3],
                "landmarks": [[0.15 + 0.3 * i, 0.3]] * 5,
                "score": 0.9 + 0.01 * i,
                "px": 40,
            }
            for i in range(n)
        ],
        version=faces.VERSION,
        by="test",
        width=200,
        height=150,
    )
    return [f["id"] for f in store.faces_of(con, doc)]


def _unit(*xs: float) -> list[float]:
    v = np.zeros(faces.DIM)
    v[: len(xs)] = xs
    return list(v / np.linalg.norm(v))


def _pass(client: TestClient) -> base.Pass:
    return base.Pass(
        worker.Door("http://testserver", client=client, name="test-worker")
    )


def test_the_step_makes_vectors_and_alike_faces_are_found(
    con: sqlite3.Connection, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    one = _picture_with_faces(con, 40, 2)
    two = _picture_with_faces(con, 200, 1)
    # the faces of the first picture are one person, the other someone
    # else: the stand-in answers in the order the faces are read
    looks = {0: _unit(1, 0.1), 1: _unit(1, 0.12), 2: _unit(0, 1)}

    def vector(aligned: Any) -> list[float]:
        return looks[vector.calls.pop(0)]

    vector.calls = [0, 1, 2]  # type: ignore[attr-defined]
    monkeypatch.setattr(faces, "vector", vector)
    monkeypatch.setattr(faces, "align", lambda image, points: image)
    due = store.face_vectors_due(con, store.FACE_MODEL, limit=10)
    assert [len(d["faces"]) for d in due] == [2, 1]
    line = step_faces.FaceVectors().run(_pass(client))
    assert line is not None and line.startswith("3 faces in 2 pictures")
    assert store.face_vectors_due(con, store.FACE_MODEL, limit=10) == []
    store.save_face_vectors()
    near = client.get(f"/face/{one[0]}/similar").json()
    assert [f["id"] for f in near] == [one[1], two[0]]
    assert near[0]["match"] is True and near[1]["match"] is False
    crop = client.get(f"/face/{one[0]}/crop")
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/jpeg"
    page = client.get("/faces", params={"order": "surest"}).json()
    assert page["total"] == 3 and page["vectored"] == 3 and page["pictures"] == 2
    assert page["items"][0]["score"] == pytest.approx(0.91)
    assert page["by_size"] == {"under 40 px": 0, "40 to 80 px": 3, "80 px and over": 0}
    assert client.get("/faces", params={"order": "nope"}).status_code == 400
    # a hidden picture's faces are not there, and not among the alike
    store.set_sensitivity(con, _doc(con, two[0]), "personal")
    token = store.VIEWER.set(store.Viewer(name="t", domains=None, personal=False))
    try:
        assert [f["id"] for f in store.similar_faces(con, one[0])] == [one[1]]
        assert store.face_crop(con, two[0]) is None
        assert store.faces_page(con)["total"] == 2
        with pytest.raises(KeyError):
            store.similar_faces(con, two[0])
    finally:
        store.VIEWER.reset(token)


def _doc(con: sqlite3.Connection, face_id: int) -> int:
    return int(
        con.execute("SELECT doc_id FROM faces WHERE id = ?", (face_id,)).fetchone()[0]
    )


def test_a_vector_lost_at_a_stop_is_read_again(con: sqlite3.Connection) -> None:
    (face,) = _picture_with_faces(con, 120, 1)
    store.apply_face_vectors(
        con,
        _doc(con, face),
        faces=[{"id": face, "vector": _unit(1)}],
        model=store.FACE_MODEL,
    )
    # the door stopped before the delta was written: nothing on disk
    assert store.reconcile_face_vectors(con) == {"checked": 1, "forgotten": 1}
    assert (
        store.face_vectors_due(con, store.FACE_MODEL, limit=5)[0]["faces"][0]["id"]
        == face
    )


def test_a_vector_must_fit_the_face(con: sqlite3.Connection) -> None:
    (face,) = _picture_with_faces(con, 160, 1)
    other = _picture_with_faces(con, 80, 1)[0]
    doc = _doc(con, face)
    for bad in (
        {"id": face, "vector": [1.0] * 3},
        {"id": face, "vector": [math.nan] * faces.DIM},
        {"id": other, "vector": _unit(1)},  # another picture's face
    ):
        with pytest.raises(ValueError):
            store.apply_face_vectors(con, doc, faces=[bad], model=store.FACE_MODEL)
    assert store.face_vectors_due(con, store.FACE_MODEL, limit=5)
