"""Who a face is (plan item AT step 4): faces named as a person, each
picture then depicting them; no face, nobody to name; an answer changed
or taken back ending what it held up; the groups showing what is left;
one event's faces; through the wall."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.store import Edge
from tests.test_face_groups import _face, _looks

pytestmark = pytest.mark.skipif(
    not store.vectors_available(), reason="usearch is not installed"
)


def _depicts(con: sqlite3.Connection, doc: int) -> list[tuple[str, str, str]]:
    return [
        (r[0], r[1], r[2])
        for r in con.execute(
            "SELECT t.name, e.producer, e.confidence FROM edges e"
            " JOIN entities t ON t.id = e.dst WHERE e.rel = 'depicts'"
            " AND e.valid_to IS NULL AND e.source_doc = ? ORDER BY t.name",
            (doc,),
        )
    ]


def _sensitivity(con: sqlite3.Connection, doc: int) -> str | None:
    row = con.execute("SELECT sensitivity FROM documents WHERE id = ?", (doc,))
    return row.fetchone()[0]


def _grouped(con: sqlite3.Connection) -> dict[str, list[tuple[int, int]]]:
    """Seven pictures of a, seven of b (one of them with both), grouped."""
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
    store.save_face_vectors()
    store.maintain(con, only=["facegroups"])
    return people


def test_faces_named_as_a_person(con: sqlite3.Connection) -> None:
    people = _grouped(con)
    a = [f for _, f in people["a"]]
    got = store.name_faces(con, a[:5], name="  Ada   Lovelace ")
    assert got["name"] == "Ada Lovelace" and got["faces"] == 5
    assert got["pictures"] == 5 and got["facts"] == 5 and got["suspected"] == 5
    doc0 = people["a"][0][0]
    assert _depicts(con, doc0) == [("Ada Lovelace", "human", "EXTRACTED")]
    assert _sensitivity(con, doc0) == "suspected"
    assert store.face_answers(con, a[:1])[a[0]]["name"] == "Ada Lovelace"
    page = store.person_page(con, got["entity_id"])
    assert page["photos"] == 5
    # the rest named as the same person, picked by id: one entity
    more = store.name_faces(con, a[5:], entity_id=got["entity_id"])
    assert more["entity_id"] == got["entity_id"] and more["facts"] == 2
    # the groups: a's is answered whole, b's still open
    groups = store.face_groups(con)
    assert groups["done"] == 1 and groups["total"] == 1
    assert groups["items"][0]["open"] == 7 and groups["items"][0]["named"] == []
    whole = store.face_group(con, store.face_groups(con)["items"][0]["id"])
    assert whole["open"] == 7


def test_an_answer_changed_or_taken_back(con: sqlite3.Connection) -> None:
    people = _grouped(con)
    doc, face = people["b"][0]
    first = store.name_faces(con, [face], name="Grace Hopper")
    assert _depicts(con, doc) == [("Grace Hopper", "human", "EXTRACTED")]
    # another name for the face: the first picture-fact ends
    store.name_faces(con, [face], name="Hedy Lamarr")
    assert _depicts(con, doc) == [("Hedy Lamarr", "human", "EXTRACTED")]
    ended = con.execute(
        "SELECT valid_to FROM edges e JOIN entities t ON t.id = e.dst"
        " WHERE t.name = 'Grace Hopper' AND e.rel = 'depicts'"
    ).fetchone()[0]
    assert ended is not None  # ended, never deleted
    with pytest.raises(KeyError):  # its answer was ended by the new one
        store.unname_faces(con, run=first["run"])
    # nobody to name: the fact it held up ends
    out = store.judge_faces(con, [face], verdict="nobody")
    assert out["ended"] == 1 and _depicts(con, doc) == []
    assert store.face_answers(con, [face])[face]["verdict"] == "nobody"
    # taken back: the face is open again
    assert store.unname_faces(con, [face]) == {"faces": 1, "ended": 0, "refusals": 0}
    assert store.face_answers(con, [face]) == {}
    named = store.name_faces(con, [face], name="Hedy Lamarr")
    assert store.unname_faces(con, run=named["run"])["ended"] == 1
    assert _depicts(con, doc) == []
    with pytest.raises(KeyError):
        store.unname_faces(con, run=named["run"])


def test_two_faces_of_a_picture_named_alike_are_one_fact(
    con: sqlite3.Connection,
) -> None:
    doc, faces = _face(con, 60, n=2)
    got = store.name_faces(con, faces, name="Ada Lovelace")
    assert got["facts"] == 1 and got["pictures"] == 1
    # one face taken back: the other still holds the fact up
    store.unname_faces(con, faces[:1])
    assert _depicts(con, doc) == [("Ada Lovelace", "human", "EXTRACTED")]
    store.unname_faces(con, faces[1:])
    assert _depicts(con, doc) == []


def test_what_cannot_be_named(con: sqlite3.Connection) -> None:
    doc, (face,) = _face(con, 70)
    with pytest.raises(ValueError, match="needs a name"):
        store.name_faces(con, [face], name=" ")
    with pytest.raises(ValueError, match="no faces"):
        store.name_faces(con, [], name="Ada")
    with pytest.raises(ValueError, match="no such face"):
        store.name_faces(con, [face + 999], name="Ada")
    store.link(
        con, Edge("Analytical Engine", "tool", "developed_by", "Babbage", "person")
    )
    tool = con.execute(
        "SELECT id FROM entities WHERE name = 'Analytical Engine'"
    ).fetchone()[0]
    with pytest.raises(ValueError, match="not a person"):
        store.name_faces(con, [face], entity_id=tool)
    with pytest.raises(ValueError, match="verdict"):
        store.judge_faces(con, [face], verdict="person")
    # a person's own decision about the picture stands
    store.set_sensitivity(con, doc, None)
    assert store.name_faces(con, [face], name="Ada")["suspected"] == 0
    assert _sensitivity(con, doc) is None


def test_a_hidden_picture_cannot_be_named(con: sqlite3.Connection) -> None:
    doc, (face,) = _face(con, 80)
    store.set_sensitivity(con, doc, "personal")
    token = store.VIEWER.set(store.Viewer(name="t", domains=None, personal=False))
    try:
        with pytest.raises(ValueError, match="no such face"):
            store.name_faces(con, [face], name="Ada")
        assert store.face_answers(con, [face]) == {}
    finally:
        store.VIEWER.reset(token)


def test_one_events_faces(con: sqlite3.Connection) -> None:
    people = _grouped(con)
    lone, (stray,) = _face(con, 90)  # a face in no group
    trip = [people["a"][0][0], people["b"][1][0], lone]
    for doc in trip:
        node, ntype = store.document_node(con, doc)
        store.link(
            con,
            Edge(node, ntype, "recorded_at", "Trip to the sea", "event"),
            source_doc=doc,
            producer="human",
        )
    event = con.execute(
        "SELECT id FROM entities WHERE name = 'Trip to the sea'"
    ).fetchone()[0]
    got = store.face_groups(con, event=event)
    assert got["event"] == {"id": event, "name": "Trip to the sea"}
    assert sorted(g["open"] for g in got["items"]) == [1, 1]
    assert [f["id"] for f in got["loose"]["faces"]] == [stray]
    assert got["events"] == [{"id": event, "name": "Trip to the sea", "faces": 3}]
    with pytest.raises(KeyError):
        store.face_groups(con, event=people["a"][0][0] + 10_000)


def test_the_routes(con: sqlite3.Connection, client: TestClient) -> None:
    _, faces = _face(con, 100, n=2)
    r = client.post("/faces/name", json={"faces": faces[:1], "name": "Ada Lovelace"})
    assert r.status_code == 200 and r.json()["facts"] == 1
    assert client.post("/faces/name", json={"faces": faces}).status_code == 400
    assert (
        client.post(
            "/faces/name", json={"faces": faces, "entity_id": 99999}
        ).status_code
        == 404
    )
    j = client.post("/faces/judge", json={"faces": faces[1:], "verdict": "not_face"})
    assert j.status_code == 200 and j.json()["verdict"] == "not_face"
    assert (
        client.post("/faces/judge", json={"faces": faces, "verdict": "x"}).status_code
        == 400
    )
    assert client.post("/faces/unname", json={}).status_code == 400
    u = client.post("/faces/unname", json={"run": r.json()["run"]})
    assert u.status_code == 200 and u.json() == {"faces": 1, "ended": 1, "refusals": 0}
    assert client.post("/faces/unname", json={"run": "nope"}).status_code == 404
    assert client.get("/faces/groups", params={"event": 99999}).status_code == 404


def test_a_person_renamed_and_two_entities_one_person(
    con: sqlite3.Connection, client: TestClient
) -> None:
    """The person page's two changes: a new name, the old one kept as
    what they are also known by, and two entities that are one person,
    the faces of both shown as the survivor; each taken back by its run."""
    people = _grouped(con)
    a = [f for _, f in people["a"]]
    ada = store.name_faces(con, a[:3], name="Ada")["entity_id"]
    lovelace = store.name_faces(con, a[3:5], name="A. Lovelace")["entity_id"]
    store.name_faces(con, [f for _, f in people["b"][:2]], name="Grace")
    got = store.rename_person(con, ada, "Ada Lovelace")
    assert got["name"] == "Ada Lovelace" and got["was"] == "Ada"
    page = store.person_page(con, ada)
    assert page["name"] == "Ada Lovelace" and page["photos"] == 3
    assert "Ada" in {lab["label"] for lab in page["labels"]}
    assert store.face_answers(con, a[:1])[a[0]]["name"] == "Ada Lovelace"
    # the old name still finds her when a face is named by it
    again = store.name_faces(con, a[5:6], name="Ada")
    assert again["entity_id"] == ada
    with pytest.raises(ValueError, match="Grace"):
        store.rename_person(con, ada, "Grace")
    assert store.rename_person(con, ada, "Ada Lovelace")["run"] is None
    # one person twice: the second folds into the first, faces and all
    same = store.merge_people(con, lovelace, ada)
    assert same == {"kept": ada, "merged": lovelace, "run": same["run"]}
    answers = store.face_answers(con, a[3:5])
    assert {w["entity_id"] for w in answers.values()} == {ada}
    assert store.person_page(con, lovelace)["photos"] == 6
    doc = people["a"][3][0]
    (face,) = [f for f in store.picture_faces(con, doc) if f["id"] == a[3]]
    assert face["answer"]["name"] == "Ada Lovelace"
    with pytest.raises(ValueError):
        store.merge_people(con, lovelace, ada)
    # each undone by its run
    store.unmerge_run(con, same["run"])
    assert store.face_answers(con, a[3:4])[a[3]]["entity_id"] == lovelace
    store.unmerge_run(con, got["run"])
    assert store.person_page(con, ada)["name"] == "Ada"
    # the routes
    r = client.post(f"/people/{ada}/rename", json={"name": "Countess"})
    assert r.status_code == 200 and r.json()["name"] == "Countess"
    assert client.post(f"/people/{ada}/rename", json={"name": " "}).status_code == 400
    assert client.post("/people/999999/rename", json={"name": "x"}).status_code == 404
    s = client.post(f"/people/{ada}/same", json={"other": lovelace})
    assert s.status_code == 200 and s.json()["kept"] == ada
    paper = int(store.ingest_text(con, "On engines. " * 20, title="Notes")["doc_id"])
    store.link(
        con,
        Edge("Notes", "document", "mentions", "Analytical Engine", "tool"),
        confidence="EXTRACTED",
        source_doc=paper,
        producer="t",
        run="t",
    )
    (engine,) = con.execute(
        "SELECT id FROM entities WHERE name = 'Analytical Engine'"
    ).fetchone()
    bad = client.post(f"/people/{ada}/same", json={"other": engine})
    assert bad.status_code == 400 and "not a person" in bad.json()["detail"]
    back = client.post("/graph/unmerge", json={"run": s.json()["run"]})
    assert back.status_code == 200
