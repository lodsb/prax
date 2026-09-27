"""The lists a person decides (stage R): the likely pairs, the names of
several things, the merges worth a second look. Every decision is kept
as a signed pair, which is also the gold sample a model's confidence is
measured against (docs/PLAN.md, Q)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.store.graph import context


def _edge(con: sqlite3.Connection, e: store.Edge, doc: int | None = None) -> None:
    store.link(con, e, confidence="EXTRACTED", source_doc=doc, producer="t", run="t")


def _id(con: sqlite3.Connection, name: str, etype: str) -> int:
    return int(
        con.execute(
            "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
        ).fetchone()[0]
    )


def _decided(con: sqlite3.Connection, a: int, b: int) -> tuple[str, str] | None:
    x, y = min(a, b), max(a, b)
    row = con.execute(
        "SELECT decided, decided_by FROM entity_candidates WHERE a = ? AND b = ?",
        (x, y),
    ).fetchone()
    return (row[0], row[1]) if row else None


def _pairs(con: sqlite3.Connection) -> tuple[int, int, int, int]:
    doc = int(store.ingest_text(con, "A paper. " * 30, title="P")["doc_id"])
    for name in ("wavelet transform", "wavelet transformation", "LDR", "LDRs"):
        _edge(con, store.Edge("P", "paper", "uses", name, "method"), doc)
    _edge(con, store.Edge("P", "paper", "uses", "wavelet transform", "concept"), doc)
    w1, w2 = (
        _id(con, "wavelet transform", "method"),
        _id(con, "wavelet transformation", "method"),
    )
    l1, l2 = _id(con, "LDR", "method"), _id(con, "LDRs", "method")
    store.replace_entity_candidates(
        con, "method", [(w1, w2, 0.97), (l1, l2, 0.95)], producer="test"
    )
    return w1, w2, l1, l2


def test_a_likely_pair_decided_either_way_is_kept_and_signed(
    con: sqlite3.Connection,
) -> None:
    w1, w2, l1, l2 = _pairs(con)
    page = store.candidates_page(con)
    assert page["total"] == 2 and page["types"] == {"method": 2}
    first = page["items"][0]
    assert first["score"] == 0.97 and first["keep"]["document"] == "P"
    got = store.decide_pair(con, w1, w2, same=True)
    assert got["run"].startswith("decide-")
    assert (
        con.execute("SELECT canonical_id FROM entities WHERE id = ?", (w2,)).fetchone()[
            0
        ]
        == w1
    )
    store.decide_pair(con, l1, l2, same=False)
    assert _decided(con, w1, w2) == ("same", "human")
    assert _decided(con, l1, l2) == ("different", "human")
    assert store.candidates_page(con)["total"] == 0
    # a weekly recomputation keeps the decisions
    store.replace_entity_candidates(con, "method", [(l1, l2, 0.95)], producer="test")
    assert store.candidates_page(con)["total"] == 0
    # the merge is a run like any, and a run can be taken back
    store.unmerge_run(con, got["run"])
    assert (
        con.execute("SELECT canonical_id FROM entities WHERE id = ?", (w2,)).fetchone()[
            0
        ]
        is None
    )


def test_one_merge_taken_back_and_recorded_different(con: sqlite3.Connection) -> None:
    for name in ("preorder traversal", "postorder traversal"):
        _edge(con, store.Edge("Algorithms", "paper", "uses", name, "method"))
    pre, post = (
        _id(con, "preorder traversal", "method"),
        _id(con, "postorder traversal", "method"),
    )
    store.merge_entities(con, post, pre)  # unsigned, as the 2026-09-17 round was
    listed = store.merges_page(con)
    assert listed["total"] == 1
    item = listed["items"][0]
    assert (
        item["why"] == "one word apart"
        and item["alias"]["name"] == "postorder traversal"
    )
    got = store.unmerge_entity(con, post)
    assert got == {"entity": post, "from": pre}
    assert (
        con.execute(
            "SELECT canonical_id FROM entities WHERE id = ?", (post,)
        ).fetchone()[0]
        is None
    )
    assert not con.execute(
        "SELECT 1 FROM entity_labels WHERE entity_id = ? AND from_entity = ?",
        (pre, post),
    ).fetchone()
    assert _decided(con, pre, post) == ("different", "human")
    assert store.merges_page(con)["total"] == 0
    with pytest.raises(ValueError):
        store.unmerge_entity(con, post)  # not merged any more


def test_which_merges_are_suspect() -> None:
    assert context._suspect("Kalman smoother", "extended Kalman smoother") == "narrower"
    assert (
        context._suspect("postorder traversal", "preorder traversal")
        == "one word apart"
    )
    assert context._suspect("spatialisation", "spatialization") is None  # one word
    assert context._suspect("Gabor transform", "gabor transforms") is None  # plural
    assert context._suspect("colour model", "color model") is None  # a spelling
    # a tracker is what does the tracking: a person should look
    assert context._suspect("beat tracking", "beat trackers") == "one word apart"
    assert context._suspect("notenschrift", "notation") is None  # a translation


def test_a_split_name_is_kept_apart_or_merged_across_types(
    con: sqlite3.Connection,
) -> None:
    for n in range(3):
        _edge(con, store.Edge(f"P{n}", "paper", "uses", "SuperCollider", "tool"))
    _edge(con, store.Edge("P9", "paper", "uses", "SuperCollider", "method"))
    for n in range(2):
        _edge(con, store.Edge(f"Q{n}", "paper", "about", "Music Perception", "concept"))
    _edge(con, store.Edge("Q9", "paper", "published_in", "Music Perception", "venue"))
    names = {i["name"]: i for i in store.split_names_page(con)["items"]}
    assert set(names) == {"SuperCollider", "Music Perception"}
    tool, method = (p["id"] for p in names["SuperCollider"]["parts"])
    concept, venue = (p["id"] for p in names["Music Perception"]["parts"])
    # the journal and the concept are two things; the tool and method one
    store.decide_pair(con, concept, venue, same=False)
    with pytest.raises(ValueError):
        store.decide_pair(con, tool, method, same=True)  # across types: say so
    store.decide_pair(con, tool, method, same=True, across_types=True)
    assert store.split_names_page(con)["total"] == 0
    assert _decided(con, concept, venue) == ("different", "human")
    kinds = con.execute(
        "SELECT type FROM entity_candidates WHERE decided_by = 'human'"
    ).fetchall()
    assert {k[0] for k in kinds} == {store.SPLIT}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_the_door_serves_the_lists_and_takes_the_decisions(client: TestClient) -> None:
    con = client.app.state.con
    _, _, l1, _ = _pairs(con)
    page = client.get("/graph/candidates", params={"type": "method"}).json()
    assert page["total"] == 2
    item = page["items"][0]
    got = client.post(
        "/graph/decide",
        json={"keep": item["keep"]["id"], "other": item["other"]["id"], "same": True},
    )
    assert got.status_code == 200 and got.json()["same"] is True
    assert (
        client.post(
            "/graph/decide", json={"keep": l1, "other": l1, "same": False}
        ).status_code
        == 400
    )
    assert client.get("/graph/candidates").json()["total"] == 1
    assert client.get("/graph/split-names").json()["total"] == 1  # the wavelet concept
    merged = item["other"]["id"]
    assert (
        client.post("/graph/unmerge-entity", json={"entity": merged}).status_code == 200
    )
    assert (
        client.post("/graph/unmerge-entity", json={"entity": merged}).status_code == 400
    )
    assert client.get("/graph/merges").json()["total"] == 0


def test_a_right_merge_is_confirmed_and_a_click_can_be_taken_back(
    client: TestClient,
) -> None:
    con = client.app.state.con
    w1, w2, l1, l2 = _pairs(con)
    store.merge_entities(con, w2, w1)  # an old merge, unsigned
    # right: recorded, nothing merged again, off the list
    got = store.decide_pair(con, w1, w2, same=True)
    assert got["run"] is None and _decided(con, w1, w2) == ("same", "human")
    # a click undone: the merge the decision made goes, and its record
    done = client.post(
        "/graph/decide", json={"keep": l1, "other": l2, "same": True}
    ).json()
    back = client.post(
        "/graph/undecide", json={"keep": l1, "other": l2, "run": done["run"]}
    ).json()
    assert back == {"undone": True, "entities": 1}
    assert (
        con.execute("SELECT canonical_id FROM entities WHERE id = ?", (l2,)).fetchone()[
            0
        ]
        is None
    )
    # the worker proposed that pair: it is open again, not gone
    assert _decided(con, l1, l2) == (None, None)
    assert store.candidates_page(con)["total"] == 1
    # a pair only the decision made is gone with its undo
    assert store.decide_pair(con, w1, l1, same=False)["run"] is None
    assert store.undecide_pair(con, w1, l1) is True
    assert _decided(con, w1, l1) is None
    assert store.undecide_pair(con, w1, l1) is False
