"""Where the faces in a picture are (plan item AT step 1): YuNet's outputs
decoded as OpenCV decodes them, the faces step from preview to the faces
table, a later reading ending the faces of the one before, and the wall.
No model is fetched: the session and the detector are stand-ins."""

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


def _outputs(height: int, width: int) -> dict[str, Any]:
    """YuNet's twelve outputs for a padded input, all quiet."""
    out: dict[str, Any] = {}
    for s in faces.STRIDES:
        n = (height // s) * (width // s)
        out[f"cls_{s}"] = np.zeros((1, n, 1), np.float32)
        out[f"obj_{s}"] = np.zeros((1, n, 1), np.float32)
        out[f"bbox_{s}"] = np.zeros((1, n, 4), np.float32)
        out[f"kps_{s}"] = np.zeros((1, n, 10), np.float32)
    return out


def _anchor(
    out: dict[str, Any],
    stride: int,
    width: int,
    row: int,
    col: int,
    *,
    score: float,
    size: tuple[float, float],
) -> None:
    i = row * (width // stride) + col
    out[f"cls_{stride}"][0, i, 0] = score
    out[f"obj_{stride}"][0, i, 0] = score
    out[f"bbox_{stride}"][0, i] = [
        0.5,
        0.5,
        math.log(size[0] / stride),
        math.log(size[1] / stride),
    ]
    out[f"kps_{stride}"][0, i] = [
        0.25,
        0.25,
        0.75,
        0.25,
        0.5,
        0.5,
        0.25,
        0.75,
        0.75,
        0.75,
    ]


def test_the_outputs_are_decoded_as_opencv_decodes_them() -> None:
    out = _outputs(64, 64)
    # a face 16 x 24 centred at (28, 20), its score sqrt(0.9 * 0.9)
    _anchor(out, 8, 64, 2, 3, score=0.9, size=(16, 24))
    # the same face seen at stride 16, less sure: suppressed
    _anchor(out, 16, 64, 1, 1, score=0.85, size=(16, 22))
    # another face, clear of the first
    _anchor(out, 32, 64, 1, 1, score=0.95, size=(20, 20))
    # under the line: never kept
    _anchor(out, 8, 64, 6, 6, score=0.5, size=(10, 10))
    got = faces.decode(out, 64, 64)
    assert [round(s, 3) for _, _, s in got] == [0.95, 0.9]
    box, marks, _ = got[1]
    assert box == pytest.approx([20.0, 8.0, 16.0, 24.0], abs=1e-4)
    # landmarks: offsets from the anchor's cell, in strides
    assert marks[:2] == pytest.approx([(3 + 0.25) * 8, (2 + 0.25) * 8])
    assert faces.decode(_outputs(64, 64), 64, 64) == []


class _Output:
    def __init__(self, name: str) -> None:
        self.name = name


class _Session:
    """onnxruntime's session as the detector uses it: the outputs in order
    for the input it is given, with one face planted."""

    def __init__(self) -> None:
        self.shapes: list[tuple[int, ...]] = []

    def get_outputs(self) -> list[_Output]:
        return [_Output(k) for k in _outputs(32, 32)]

    def run(self, _names: Any, feed: dict[str, Any]) -> list[Any]:
        blob = feed["input"]
        self.shapes.append(blob.shape)
        h, w = blob.shape[2], blob.shape[3]
        out = _outputs(h, w)
        # a face 20 x 20 at the bottom right, past the picture's own edge
        _anchor(out, 8, w, (h // 8) - 2, (w // 8) - 2, score=0.9, size=(20, 20))
        return list(out.values())


def test_detect_pads_reads_at_full_size_and_keeps_fractions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    monkeypatch.setattr(faces, "_session", lambda: session)
    image = Image.new("RGB", (60, 50), (200, 180, 160))
    (face,) = faces.detect(image)
    assert session.shapes == [(1, 3, 64, 64)]  # padded to a multiple of 32
    x, y, w, h = face.box
    # centred at (52, 52) in the padded input: clipped to the picture
    assert (x, y) == pytest.approx((42 / 60, 42 / 50), abs=1e-4)
    assert x + w == pytest.approx(1.0) and y + h == pytest.approx(1.0)
    assert face.px == 18 and len(face.landmarks) == len(faces.LANDMARKS)
    assert all(0 <= v <= 1.2 for pt in face.landmarks for v in pt)


def test_the_model_file_must_be_the_one_measured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wrong = tmp_path / "yunet.onnx"
    wrong.write_bytes(b"not the model")
    monkeypatch.setattr(fetch, "model_file", lambda *a, **k: wrong)
    faces._session.cache_clear()
    with pytest.raises(RuntimeError, match="not the YuNet file"):
        faces._session()
    faces._session.cache_clear()


def _jpeg(size: tuple[int, int], seed: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (seed * 40 % 255, 90, 120)).save(buf, "JPEG")
    return buf.getvalue()


def _picture(con: sqlite3.Connection, seed: int, *, preview: bool = True) -> int:
    doc = int(
        store.register(
            con, _jpeg((40, 30), seed), mime="image/jpeg", title=f"IMG_{seed}.JPG"
        )["doc_id"]
    )
    store.apply_photo(
        con,
        doc,
        facts={"taken": "2019-05-03T10:00:00"},
        copies={"preview": _jpeg((160, 120), seed)} if preview else {},
        version=photos.VERSION,
        by="test",
    )
    return doc


def _pass(client: TestClient) -> base.Pass:
    return base.Pass(
        worker.Door("http://testserver", client=client, name="test-worker")
    )


ONE = faces.Face(
    box=(0.25, 0.2, 0.1, 0.15),
    landmarks=((0.27, 0.25), (0.32, 0.25), (0.3, 0.29), (0.28, 0.32), (0.32, 0.32)),
    score=0.93,
    px=16,
)


def test_the_step_finds_faces_in_each_preview(
    con: sqlite3.Connection, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[int, int]] = []

    def detect(image: Image.Image, **_: Any) -> list[faces.Face]:
        seen.append(image.size)
        return [ONE] if image.getpixel((0, 0))[0] < 60 else []

    monkeypatch.setattr(faces, "detect", detect)
    with_face, without, unread = (
        _picture(con, 1),
        _picture(con, 2),
        _picture(con, 3, preview=False),
    )
    assert store.faces_due(con, faces.VERSION, limit=10) == [with_face, without]
    line = step_faces.Faces().run(_pass(client))
    assert line is not None and "1 with faces" in line and "1 without" in line
    assert seen == [(160, 120), (160, 120)]  # the preview, at its own size
    (face,) = store.faces_of(con, with_face)
    assert face["box"] == [0.25, 0.2, 0.1, 0.15] and face["score"] == 0.93
    assert face["detector"] == faces.stamp()
    assert store.get_meta(con, with_face)["faces"]["n"] == 1
    held = store.get_meta(con, without)["faces"]
    assert (held["n"], held["from"], held["width"], held["v"]) == (
        0,
        "preview",
        160,
        faces.VERSION,
    )
    assert "faces" not in store.get_meta(con, unread)
    assert store.face_counts(con, faces.VERSION) == {
        "read": 2,
        "with_faces": 1,
        "faces": 1,
        "waiting": 0,
    }
    assert client.get(f"/doc/{with_face}/faces").json()[0]["id"] == face["id"]
    # nothing more is due; a later reading ends what the earlier one found
    assert client.get("/work/faces").json()["items"] == []
    store.apply_faces(
        con,
        with_face,
        faces=[],
        version=faces.VERSION + 1,
        by="test",
        width=160,
        height=120,
    )
    assert store.faces_of(con, with_face) == []
    gone = con.execute(
        "SELECT gone_at FROM faces WHERE id = ?", (face["id"],)
    ).fetchone()[0]
    assert gone is not None  # ended, never deleted


def test_a_misshapen_face_is_refused(con: sqlite3.Connection) -> None:
    doc = _picture(con, 4)
    good = {"box": [0.1, 0.1, 0.2, 0.2], "landmarks": [[0.1, 0.1]] * 5, "score": 0.9}
    for bad in (
        {**good, "box": [0.1, 0.1, 0.0, 0.2]},
        {**good, "box": [0.1, 2.0, 0.1, 0.2]},
        {**good, "landmarks": [[0.1, 0.1]] * 4},
        {**good, "score": 3.0},
    ):
        with pytest.raises(ValueError):
            store.apply_faces(
                con,
                doc,
                faces=[{**bad, "px": 10}],
                version=1,
                by="test",
                width=1,
                height=1,
            )
    assert store.faces_of(con, doc) == []


def test_a_hidden_picture_hides_its_faces(con: sqlite3.Connection) -> None:
    doc = _picture(con, 5)
    store.apply_faces(
        con,
        doc,
        faces=[{**ONE.__dict__, "box": list(ONE.box)}],
        version=faces.VERSION,
        by="test",
        width=160,
        height=120,
    )
    store.set_sensitivity(con, doc, "personal")
    token = store.VIEWER.set(store.Viewer(name="t", domains=None, personal=False))
    try:
        assert store.faces_of(con, doc) == []
    finally:
        store.VIEWER.reset(token)
    assert len(store.faces_of(con, doc)) == 1
