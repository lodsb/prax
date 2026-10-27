"""The faces that are probably one person (plan item AT step 3): HDBSCAN
over nearest-neighbour lists (``prax.ml.clusters``), the facegroups pass
from the face index to the groups, an id kept across runs, and the reads
through the wall. No model is fetched: the vectors are stand-ins."""

from __future__ import annotations

import io
import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from prax import store
from prax.ml import clusters, faces
from prax.parsers import photos


def _lists(points: np.ndarray, k: int = 10) -> list[list[tuple[int, float]]]:
    m = points / np.linalg.norm(points, axis=1, keepdims=True)
    d = np.clip(1 - m @ m.T, 0, 2)
    return [
        [(int(j), float(d[i, j])) for j in np.argsort(d[i], kind="stable") if j != i][
            :k
        ]
        for i in range(len(m))
    ]


def _blobs(seed: int, spread: float = 0.15) -> tuple[np.ndarray, list[int]]:
    rng = np.random.default_rng(seed)
    centres = np.eye(16)[:3] * 3
    pts, truth = [], []
    for c, centre in enumerate(centres):
        for _ in range(25):
            pts.append(centre + spread * rng.normal(size=16))
            truth.append(c)
    for _ in range(10):
        pts.append(rng.normal(size=16))
        truth.append(-1)
    return np.array(pts), truth


def test_three_people_are_three_groups() -> None:
    pts, truth = _blobs(3)
    got = clusters.group(_lists(pts), min_size=5, min_samples=3)
    assert len(got.persistence) == 3
    for c in range(3):
        mine = {got.labels[i] for i, t in enumerate(truth) if t == c}
        assert len(mine) == 1 and clusters.NOISE not in mine
    assert all(0.0 <= s <= 1.0 for s in got.strength)
    assert all(s == 0.0 for s, g in zip(got.strength, got.labels, strict=True) if g < 0)


def test_parts_the_lists_do_not_connect_are_groups_of_their_own() -> None:
    # neighbour lists that never cross between the blobs: a forest
    pts, truth = _blobs(5, spread=0.05)
    pts, truth = pts[:75], truth[:75]  # the three people, no strangers
    rows = _lists(pts)
    rows = [
        [(j, d) for j, d in row if truth[j] == truth[i]] for i, row in enumerate(rows)
    ]
    got = clusters.group(rows, min_size=5, min_samples=3)
    assert len(got.persistence) == 3


def test_near_copies_do_not_split_a_person() -> None:
    # one person's faces, and a burst of four near copies among them
    rng = np.random.default_rng(1)
    centre = np.eye(16)[0] * 3
    pts = [centre + 0.3 * rng.normal(size=16) for _ in range(20)]
    burst = centre + 0.3 * rng.normal(size=16)
    pts += [burst + 0.001 * rng.normal(size=16) for _ in range(4)]
    # someone else, and a few strangers
    pts += [np.eye(16)[1] * 3 + 0.3 * rng.normal(size=16) for _ in range(20)]
    pts += [rng.normal(size=16) for _ in range(6)]
    rows = _lists(np.array(pts))
    whole = clusters.group(rows, min_size=3, min_samples=3, floor=0.3)
    person = set(whole.labels[:24])
    assert len(person) == 1 and clusters.NOISE not in person
    assert len(set(whole.labels[24:44])) == 1 and person != set(whole.labels[24:44])
    # without a floor the burst is a group of its own, the rest left out
    split = clusters.group(rows, min_size=3, min_samples=3)
    assert len(set(split.labels[20:24])) == 1
    assert split.labels.count(split.labels[20]) == 4


def test_too_few_points_make_no_group() -> None:
    assert clusters.group([[(1, 0.1)], [(0, 0.1)]], min_size=3).labels == [-1, -1]
    with pytest.raises(ValueError):
        clusters.group([], min_size=1)


def _jpeg(size: tuple[int, int], shade: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (shade, 90, 120)).save(buf, "JPEG")
    return buf.getvalue()


def _face(con: sqlite3.Connection, seed: int, n: int = 1) -> tuple[int, list[int]]:
    doc = int(
        store.register(
            con, _jpeg((40, 30), seed * 7 % 256), mime="image/jpeg", title="p.jpg"
        )["doc_id"]
    )
    store.apply_photo(
        con,
        doc,
        facts={},
        copies={"preview": _jpeg((200, 150), seed * 7 % 256)},
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
                "score": 0.9,
                "px": 60,
            }
            for i in range(n)
        ],
        version=faces.VERSION,
        by="test",
        width=200,
        height=150,
    )
    return doc, [f["id"] for f in store.faces_of(con, doc)]


def _looks(rng: np.random.Generator, axis: int) -> list[float]:
    """A face of person ``axis``: the two people of the test are nearer
    each other (a distance of some 0.45) than either is to the stranger
    (some 1.0), as people are among a library's faces."""
    v = np.zeros(faces.DIM)
    v[0] = 1.0 if axis in (0, 1) else 0.0
    v[axis] = 1.5 if axis else 1.0
    v += 0.02 * rng.normal(size=faces.DIM)
    return list(v / np.linalg.norm(v))


pytestmark_vectors = pytest.mark.skipif(
    not store.vectors_available(), reason="usearch is not installed"
)


@pytestmark_vectors
def test_the_pass_groups_the_faces_and_keeps_an_id(
    con: sqlite3.Connection, client: TestClient
) -> None:
    rng = np.random.default_rng(4)
    people: dict[str, list[tuple[int, int]]] = {"a": [], "b": []}
    for seed in range(12):
        who = "a" if seed % 2 else "b"
        doc, (face,) = _face(con, seed + 1)
        store.apply_face_vectors(
            con,
            doc,
            faces=[{"id": face, "vector": _looks(rng, 0 if who == "a" else 1)}],
            model=store.FACE_MODEL,
        )
        people[who].append((doc, face))
    # a picture with both of them: never each other's neighbour
    both, (fa, fb) = _face(con, 40, n=2)
    store.apply_face_vectors(
        con,
        both,
        faces=[
            {"id": fa, "vector": _looks(rng, 0)},
            {"id": fb, "vector": _looks(rng, 1)},
        ],
        model=store.FACE_MODEL,
    )
    people["a"].append((both, fa))
    people["b"].append((both, fb))
    lone, (stranger,) = _face(con, 50)
    store.apply_face_vectors(
        con,
        lone,
        faces=[{"id": stranger, "vector": _looks(rng, 5)}],
        model=store.FACE_MODEL,
    )
    store.save_face_vectors()
    got = store.maintain(con, only=["facegroups"])
    assert got["facegroups"]["groups"] == 2 and got["facegroups"]["doubled"] == 0
    page = client.get("/faces/groups").json()
    assert page["total"] == 2 and page["grouped"] == 14
    by_face = {f["id"]: g["id"] for g in page["items"] for f in g["faces"]}
    for who in people.values():
        assert len({by_face[f] for _, f in who}) == 1
    assert stranger not in by_face
    first = page["items"][0]
    assert first["size"] == 7 and first["pictures"] == 7 and first["doubled"] == []
    whole = client.get(f"/faces/groups/{first['id']}").json()
    assert len(whole["faces"]) == 7
    assert [f["strength"] for f in whole["faces"]] == sorted(
        (f["strength"] for f in whole["faces"]), reverse=True
    )
    assert client.get("/faces/groups/999").status_code == 404
    # again: the same groups keep their ids
    again = store.maintain(con, only=["facegroups"])["facegroups"]
    assert again["carried"] == 2 and again["new"] == 0
    assert {g["id"] for g in client.get("/faces/groups").json()["items"]} == {
        g["id"] for g in page["items"]
    }
    # a hidden picture's face is not in its group, and the count follows
    doc_a = people["a"][0][0]
    group_a = by_face[people["a"][0][1]]
    store.set_sensitivity(con, doc_a, "personal")
    token = store.VIEWER.set(store.Viewer(name="t", domains=None, personal=False))
    try:
        seen = store.face_group(con, group_a)
        assert people["a"][0][1] not in {f["id"] for f in seen["faces"]}
        assert seen["size"] == 6
        assert store.face_groups(con)["grouped"] == 13
    finally:
        store.VIEWER.reset(token)


def test_no_face_index_no_groups(con: sqlite3.Connection) -> None:
    got = store.maintain(con, only=["facegroups"])["facegroups"]
    assert got["skipped"] == "no face index"
    assert store.face_groups(con) == {
        "total": 0,
        "grouped": 0,
        "done": 0,
        "built_at": None,
        "items": [],
        "events": [],
    }
