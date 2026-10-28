"""A set of pictures a page at a time, the web page's gallery
(``store.picture_set``, ``GET /pictures``): a person's, an event's, an
event candidate's, a place's with the places in it; through the wall. The
people and places are invented."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from prax import store
from prax.store import Edge
from tests.test_events import _photos
from tests.test_people import _depicts, _entity, _picture


def _place(con: sqlite3.Connection, pic: int, town: str) -> None:
    node, ntype = store.document_node(con, pic)
    store.link(
        con,
        Edge(node, ntype, "taken_in", town, "place"),
        source_doc=pic,
        confidence="INFERRED",
    )


def _set(con: sqlite3.Connection, **kw: int) -> list[int]:
    return [it["doc_id"] for it in store.picture_set(con, **kw)["items"]]


def test_a_persons_pictures_newest_first_a_page_at_a_time(
    con: sqlite3.Connection,
) -> None:
    pics = [_picture(con, n, f"2019-05-{n + 1:02d}T10:00:00") for n in range(5)]
    undated = _picture(con, 9, "")
    for pic in [*pics, undated]:
        _depicts(con, pic, "Ada Quill")
    ada = _entity(con, "Ada Quill")
    got = store.picture_set(con, depicts=ada, limit=2)
    assert (got["set"], got["id"], got["name"], got["total"]) == (
        "depicts",
        ada,
        "Ada Quill",
        6,
    )
    assert [it["doc_id"] for it in got["items"]] == [pics[4], pics[3]]
    assert got["items"][0]["taken"].startswith("2019-05-05")
    # the undated one last, and a page past the end is empty
    assert _set(con, depicts=ada, offset=4, limit=2) == [pics[0], undated]
    assert _set(con, depicts=ada, offset=6) == []
    # a merged alias's set is its survivor's
    _depicts(con, pics[0], "A. Quill")
    alias = _entity(con, "A. Quill")
    store.merge_entities(con, alias, ada)
    assert store.picture_set(con, depicts=alias)["id"] == ada
    assert store.picture_set(con, depicts=alias)["total"] == 6


def test_an_event_in_order_and_a_place_with_the_places_in_it(
    con: sqlite3.Connection,
) -> None:
    pics = [_picture(con, n, f"2019-06-{n + 1:02d}T10:00:00") for n in range(4)]
    for pic in pics:
        node, ntype = store.document_node(con, pic)
        store.link(
            con,
            Edge(node, ntype, "recorded_at", "Coast walk", "event"),
            source_doc=pic,
            producer="human",
        )
    walk = _entity(con, "Coast walk", "event")
    assert _set(con, event=walk) == pics  # in the order they were taken
    _place(con, pics[0], "Aldmouth")
    _place(con, pics[1], "Brinby")
    _place(con, pics[2], "Westshire")
    # the towns in the region, the region in the country, as a picture states
    for town, region in (("Aldmouth", "Westshire"), ("Brinby", "Westshire")):
        store.link(
            con,
            Edge(town, "place", "located_in", region, "place"),
            source_doc=pics[0],
            confidence="INFERRED",
        )
    store.link(
        con,
        Edge("Westshire", "place", "located_in", "Ardenia", "place"),
        source_doc=pics[0],
        confidence="INFERRED",
    )
    con.commit()
    region = _entity(con, "Westshire", "place")
    country = _entity(con, "Ardenia", "place")
    town = _entity(con, "Aldmouth", "place")
    assert set(_set(con, place=region)) == set(pics[:3])
    assert set(_set(con, place=country)) == set(pics[:3])
    assert _set(con, place=town) == [pics[0]]
    # newest first
    assert _set(con, place=region) == [pics[2], pics[1], pics[0]]


def test_an_event_candidates_photos(con: sqlite3.Connection) -> None:
    ids = _photos(con, "2018-05-03T09:00:00", 10, folder="summer_lake", tag="lake")
    store.find_event_candidates(con)
    (cand,) = store.event_candidates_page(con)["items"]
    got = store.picture_set(con, candidate=cand["id"], limit=4)
    assert got["total"] == 10 and [it["doc_id"] for it in got["items"]] == ids[:4]


def test_the_wall_leaves_a_hidden_picture_out_of_page_and_count(
    con: sqlite3.Connection,
) -> None:
    pics = [_picture(con, n, f"2019-05-{n + 1:02d}T10:00:00") for n in range(3)]
    for pic in pics:
        _depicts(con, pic, "Ada Quill")
        store.set_sensitivity(con, pic, None)  # released, as a person would
    store.set_sensitivity(con, pics[1], "personal")
    ada = _entity(con, "Ada Quill")
    token = store.VIEWER.set(store.Viewer(name="t", domains=None, personal=False))
    try:
        got = store.picture_set(con, depicts=ada)
        assert got["total"] == 2
        assert [it["doc_id"] for it in got["items"]] == [pics[2], pics[0]]
    finally:
        store.VIEWER.reset(token)
    assert store.picture_set(con, depicts=ada)["total"] == 3


def test_the_route(con: sqlite3.Connection, client: TestClient) -> None:
    pic = _picture(con, 1, "2019-05-03T10:00:00")
    _depicts(con, pic, "Ada Quill")
    ada = _entity(con, "Ada Quill")
    got = client.get("/pictures", params={"depicts": ada}).json()
    assert got["total"] == 1 and got["items"][0]["doc_id"] == pic
    assert client.get("/pictures").status_code == 400
    both = {"depicts": ada, "place": ada}
    assert client.get("/pictures", params=both).status_code == 400
    assert client.get("/pictures", params={"depicts": 999999}).status_code == 404
    assert client.get("/pictures", params={"candidate": 999999}).status_code == 404
    # a page is at most PICTURES_PAGE_MAX
    big = client.get("/pictures", params={"depicts": ada, "limit": 10_000}).json()
    assert big["limit"] == store.PICTURES_PAGE_MAX
