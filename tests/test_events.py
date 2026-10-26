"""Events from photos (core 6; ``docs/biography.md`` §7): groups of
pictures taken close together, found as candidates, named by a person as
an event or set aside, and undone. The photos and places are invented."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from prax import store


def _photos(
    con: sqlite3.Connection,
    start: str,
    n: int,
    *,
    every_minutes: int = 30,
    folder: str = "2018/05/03",
    town: str = "",
    country: str = "",
    tag: str = "",
) -> list[int]:
    """``n`` pictures on a share, one every ``every_minutes`` from ``start``,
    in ``folder``, each with its taken date and (when given) its place."""
    when = datetime.fromisoformat(start)
    entries = []
    for i in range(n):
        digest = hashlib.sha256(f"{start}{tag}{i}".encode()).hexdigest()
        entries.append(
            {
                "path": f"pics/{folder}/IMG_{i:04d}.JPG",
                "size": 1,
                "mtime": 1,
                "sha256": digest,
            }
        )
    got = store.register_references(con, "nas", entries)
    ids = [int(d["doc_id"]) for d in got["documents"]]
    for i, doc in enumerate(ids):
        meta = store.get_meta(con, doc)
        meta["taken"] = {
            "date": (when + timedelta(minutes=i * every_minutes)).isoformat(),
            "precision": "second",
            "by": "exif",
        }
        if town:
            meta["place"] = {
                "v": 1,
                "by": "test",
                "at": "x",
                "name": town,
                "region": "",
                "country": country,
                "cc": "XA",
                "km": 1.0,
                "id": 1,
            }
        store.set_meta(con, doc, meta)
    return ids


def _library(con: sqlite3.Connection) -> dict[str, list[int]]:
    return {
        # a day by the lake, its folder named
        "lake": _photos(
            con, "2018-05-03T09:00:00", 10, folder="summer_lake", tag="lake"
        ),
        # three days away, placed, across nights (gaps under a day)
        "away": _photos(
            con,
            "2018-06-10T08:00:00",
            12,
            every_minutes=300,
            town="Aldmouth",
            country="Ardenia",
            tag="away",
        ),
        # the next country the same afternoon: another group
        "next": _photos(
            con, "2018-06-12T18:00:00", 9, town="Corvo", country="Bellmark", tag="next"
        ),
        # too few to ask about
        "few": _photos(con, "2018-08-01T10:00:00", 3, tag="few"),
    }


def _edges(con: sqlite3.Connection, run: str) -> list[tuple[Any, ...]]:
    return [
        tuple(r)
        for r in con.execute(
            "SELECT s.name, e.rel, t.name, e.world_from, e.world_to, e.producer"
            " FROM edges e JOIN entities s ON s.id = e.src"
            " JOIN entities t ON t.id = e.dst WHERE e.run = ? AND e.valid_to IS NULL"
            " ORDER BY e.id",
            (run,),
        )
    ]


def test_photos_are_grouped_by_time_and_country(con: sqlite3.Connection) -> None:
    ids = _library(con)
    groups = store.photo_groups(con)
    assert [g["docs"] for g in groups] == [ids["lake"], ids["away"], ids["next"]]
    lake, away, nxt = groups
    assert lake["suggestion"] == "summer lake" and lake["folder"] == "summer lake"
    # a date folder says nothing: the place and the month name it
    assert away["folder"] == "" and away["suggestion"] == "Aldmouth, Ardenia, June 2018"
    assert away["place"] == "Aldmouth"
    assert nxt["suggestion"] == "Corvo, Bellmark, June 2018"
    # two groups of one month and no place: their days tell them apart
    _photos(con, "2018-08-20T10:00:00", 8, tag="late")
    _photos(con, "2018-08-25T10:00:00", 8, tag="later")
    named = [g["suggestion"] for g in store.photo_groups(con)][3:]
    assert named == ["20 August 2018", "25 August 2018"]


def test_an_event_is_named_written_joined_and_undone(con: sqlite3.Connection) -> None:
    ids = _library(con)
    assert store.find_event_candidates(con) == {"candidates": 3, "decided_before": 0}
    page = store.event_candidates_page(con)
    assert page["counts"] == {"open": 3, "event": 0, "not": 0}
    lake, away, nxt = page["items"]
    assert away["photos"] == 12 and len(away["sample"]) == 8
    got = store.decide_event(con, away["id"], event=True, name="  Ardenia  trip ")
    assert got["name"] == "Ardenia trip" and got["facts"] == 13
    facts = _edges(con, got["run"])
    first = store.media_node_name("IMG_0000.JPG", ids["away"][0])
    assert facts[0] == (
        first,
        "recorded_at",
        "Ardenia trip",
        "2018-06-10",
        None,
        "human",
    )
    assert facts[-1] == (
        "Ardenia trip",
        "located_in",
        "Aldmouth",
        "2018-06-10",
        "2018-06-12",
        "human",
    )
    # the same name joins the event: one entity, the photos of both
    joined = store.decide_event(con, nxt["id"], event=True, name="Ardenia trip")
    assert joined["entity_id"] == got["entity_id"]
    store.decide_event(con, lake["id"], event=False)
    # found again: what a person decided stays decided, and is not asked
    assert store.find_event_candidates(con) == {"candidates": 0, "decided_before": 3}
    assert store.event_candidates_page(con)["counts"] == {
        "open": 0,
        "event": 2,
        "not": 1,
    }
    # undo: the facts end, the group is open again and found again
    undone = store.undo_event(con, away["id"])
    assert undone["ended"] == 13 and _edges(con, got["run"]) == []
    assert store.find_event_candidates(con)["candidates"] == 1


def test_a_decision_needs_a_name_and_is_made_once(con: sqlite3.Connection) -> None:
    _library(con)
    store.find_event_candidates(con)
    cid = store.event_candidates_page(con)["items"][0]["id"]
    for bad in ("", "   ", None):
        try:
            store.decide_event(con, cid, event=True, name=bad)
        except ValueError:
            continue
        raise AssertionError("an event without a name")
    store.decide_event(con, cid, event=False)
    try:
        store.decide_event(con, cid, event=True, name="later")
    except ValueError as exc:
        assert "undo" in str(exc)
    else:
        raise AssertionError("decided twice")


def test_the_review_routes_and_the_nightly_pass(
    con: sqlite3.Connection, client: TestClient
) -> None:
    _library(con)
    got = store.maintain(con, only=["events"])
    assert "events" in str(got)
    page = client.get("/graph/events").json()
    assert page["total"] == 3
    cid = page["items"][0]["id"]
    r = client.post(f"/graph/events/{cid}", json={"event": True, "name": "Lake day"})
    assert r.status_code == 200 and r.json()["facts"] == 10
    assert client.post(f"/graph/events/{cid}", json={"event": False}).status_code == 400
    assert (
        client.get("/graph/events", params={"state": "event"}).json()["items"][0][
            "name"
        ]
        == "Lake day"
    )
    assert client.post(f"/graph/events/{cid}/undo").json()["state"] == "open"
    assert client.get("/graph/events", params={"state": "nope"}).status_code == 400
    assert client.post("/graph/events/999999/undo").status_code == 404
    assert client.post("/graph/events/find").json()["candidates"] == 3
