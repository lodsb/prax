"""Whose face this probably is (plan item AT step 5): an open face matched
against every named face, offered as the person most of its nearest named
faces are; "not them" kept and never offered again; an undo taking it
back; a hidden picture's face neither offered nor matched against."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.ml import matching
from tests.test_face_naming import _grouped


def _rows(*vectors: list[float]) -> np.ndarray:
    return matching.unit(np.asarray(vectors, dtype=np.float32))


def test_the_nearest_named_faces_vote() -> None:
    named = _rows([1, 0, 0], [0.9, 0.1, 0], [0.8, 0.3, 0], [0, 1, 0], [0, 0.9, 0.2])
    people = [7, 7, 9, 3, 3]
    asked = _rows([1, 0.05, 0], [0.1, 1, 0], [0, 0, 1])
    near = matching.nearest_people(named, people, asked, k=3)
    assert near[0] is not None and near[0].person == 7 and near[0].votes == 2
    assert near[0].like == 0 and 0.99 < near[0].similarity <= 1.0
    assert near[0].margin == pytest.approx(
        near[0].similarity - float(asked[0] @ named[2]), abs=1e-5
    )
    assert near[1] is not None and near[1].person == 3
    # far from everyone, it is still someone's nearest: the line is the caller's
    assert near[2] is not None and near[2].similarity < 0.3


def test_a_person_left_out_for_a_face() -> None:
    named = _rows([1, 0, 0], [0, 1, 0])
    asked = _rows([1, 0.2, 0])
    got = matching.nearest_people(named, [7, 3], asked, k=2, excluded={0: {7}})
    assert got[0] is not None and got[0].person == 3 and got[0].margin == 1.0
    none = matching.nearest_people(named, [7, 3], asked, k=2, excluded={0: {7, 3}})
    assert none == [None]
    assert matching.nearest_people(named[:0], [], asked) == [None]
    assert matching.nearest_people(named, [7, 3], asked[:0]) == []


def test_slices_give_the_same_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(3)
    named = matching.unit(rng.normal(size=(40, 8)))
    people = [i % 5 for i in range(40)]
    asked = matching.unit(rng.normal(size=(30, 8)))
    whole = matching.nearest_people(named, people, asked, k=3)
    monkeypatch.setattr(matching, "SLICE", 50)  # one face a slice
    sliced = matching.nearest_people(named, people, asked, k=3)
    assert [(m.person, m.votes, m.like) for m in sliced if m] == [
        (m.person, m.votes, m.like) for m in whole if m
    ]
    assert [m.similarity for m in sliced if m] == pytest.approx(
        [m.similarity for m in whole if m], abs=1e-5
    )


pytestmark_vectors = pytest.mark.skipif(
    not store.vectors_available(), reason="usearch is not installed"
)


def _offered(con: sqlite3.Connection) -> dict[str, list[int]]:
    return {
        m["name"]: sorted(f["id"] for f in m["faces"])
        for m in store.face_matches(con)["items"]
    }


@pytestmark_vectors
def test_open_faces_offered_as_who_they_look_like(con: sqlite3.Connection) -> None:
    people = _grouped(con)
    a = [f for _, f in people["a"]]
    b = [f for _, f in people["b"]]
    assert store.face_matches(con)["items"] == []  # nobody named yet
    ada = store.name_faces(con, a[:3], name="Ada Lovelace")["entity_id"]
    store.name_faces(con, b[:3], name="Grace Hopper")
    got = store.face_matches(con)
    assert got["open"] == len(a) + len(b) - 6
    assert _offered(con) == {"Ada Lovelace": sorted(a[3:]), "Grace Hopper": b[3:]}
    row = next(m for m in got["items"] if m["entity_id"] == ada)
    assert row["like"]["id"] in a[:3] and row["pictures"] == len(a) - 3
    assert all(f["similarity"] >= got["line"] for f in row["faces"])
    # named from the offer, one face clicked away: not her, never offered
    # as her again, and the same undo takes both back
    named = store.name_faces(con, a[3:5], entity_id=ada, refused=a[5:6], matched=True)
    assert named["run"].startswith("match-") and named["refused"] == 1
    offered = _offered(con)
    assert a[5] not in offered.get("Ada Lovelace", [])
    assert a[5] in offered["Grace Hopper"]  # open to anyone else
    back = store.unname_faces(con, run=named["run"])
    assert back["faces"] == 2 and back["refusals"] == 1
    assert _offered(con)["Ada Lovelace"] == sorted(a[3:])
    # "not her" for every face left, then named her after all: the refusal ends
    said = store.refuse_faces(con, a[3:], entity_id=ada)
    assert said["faces"] == len(a) - 3 and "Ada Lovelace" not in _offered(con)
    store.name_faces(con, a[3:4], entity_id=ada)
    live = con.execute(
        "SELECT count(*) FROM face_refusals WHERE ended_at IS NULL"
    ).fetchone()[0]
    assert live == len(a) - 4
    # one person's faces whole
    whole = store.face_matches(con, person=ada)
    assert [m["entity_id"] for m in whole["items"]] == [ada]
    with pytest.raises(KeyError):
        store.face_matches(con, person=ada + 10_000)


@pytestmark_vectors
def test_a_hidden_picture_neither_offered_nor_matched_against(
    con: sqlite3.Connection,
) -> None:
    people = _grouped(con)
    a = people["a"]
    store.name_faces(con, [f for _, f in a[:3]], name="Ada Lovelace")
    open_doc, open_face = a[3]
    named_doc = a[0][0]
    for doc in (open_doc, named_doc):
        store.set_sensitivity(con, doc, "personal")
    token = store.VIEWER.set(store.Viewer(name="t", domains=None, personal=False))
    try:
        got = store.face_matches(con)
        shown = {f["id"] for m in got["items"] for f in m["faces"]}
        assert open_face not in shown
        assert all(m["like"]["doc_id"] != named_doc for m in got["items"])
    finally:
        store.VIEWER.reset(token)
    assert open_face in {
        f["id"] for m in store.face_matches(con)["items"] for f in m["faces"]
    }


@pytestmark_vectors
def test_the_routes(con: sqlite3.Connection, client: TestClient) -> None:
    people = _grouped(con)
    a = [f for _, f in people["a"]]
    ada = client.post("/faces/name", json={"faces": a[:3], "name": "Ada"}).json()
    r = client.get("/faces/matches")
    assert r.status_code == 200 and r.json()["items"][0]["name"] == "Ada"
    assert client.get("/faces/matches", params={"person": 99999}).status_code == 404
    named = client.post(
        "/faces/name",
        json={
            "faces": a[3:4],
            "entity_id": ada["entity_id"],
            "refused": a[4:5],
            "matched": True,
        },
    )
    assert named.status_code == 200 and named.json()["refused"] == 1
    both = {"faces": a[5:6], "entity_id": ada["entity_id"], "refused": a[5:6]}
    assert client.post("/faces/name", json=both).status_code == 400
    no = client.post(
        "/faces/refuse", json={"faces": a[5:], "entity_id": ada["entity_id"]}
    )
    assert no.status_code == 200 and no.json()["faces"] == len(a) - 5
    assert (
        client.post("/faces/refuse", json={"faces": a[5:], "entity_id": 99999})
    ).status_code == 404
    u = client.post("/faces/unname", json={"run": no.json()["run"]})
    assert u.status_code == 200 and u.json()["refusals"] == len(a) - 5
