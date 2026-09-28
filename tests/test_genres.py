"""What a document is: the genre vocabulary beside the modules, a person's
genres as the gold sample, and the sample to label (stage Z)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.graph import ontology


def test_the_genres_load_beside_the_modules() -> None:
    g = ontology.genres()
    assert g.version != "0"
    labels = g.labels()
    assert labels[0] == "informational" and "paper" in labels
    assert g.level_of("datasheet") == "instructional"
    assert g.level_of("opinion") == "opinion"  # a level is a label of its own
    assert g.level_of("symphony") is None
    assert all(lv.description for lv in g.levels)
    assert all(d for lv in g.levels for _, d in lv.genres)


def test_the_genres_are_not_a_module() -> None:
    onto = ontology.current()
    assert "genres" not in onto.modules and "genres" not in onto.version


def test_a_label_is_named_once() -> None:
    with pytest.raises(ValueError, match="twice"):
        ontology.parse_genres(
            "levels:\n  opinion: {genres: {essay: x}}\n  other: {genres: {essay: y}}"
        )


def test_check_keeps_the_vocabulary_order_and_refuses_the_unknown() -> None:
    g = ontology.genres()
    assert g.check(["blog", "tutorial", "blog"]) == ["tutorial", "blog"]
    with pytest.raises(ValueError, match="unknown genre"):
        g.check(["pamphlet"])


def _doc(con: sqlite3.Connection, text: str, **meta: object) -> int:
    doc_id = int(store.ingest_text(con, text, title=text[:20])["doc_id"])
    if meta:
        m = store.get_meta(con, doc_id)
        m.update(meta)
        store.set_meta(con, doc_id, m)
    return doc_id


def test_a_persons_genres_are_the_gold(con: sqlite3.Connection) -> None:
    d = _doc(con, "How to solder a TL072 onto a board, step by step.")
    got = store.set_genres(con, d, ["tutorial", "instructional"])
    assert got == {
        "doc_id": d,
        "genres": ["instructional", "tutorial"],
        "skipped": False,
    }
    m = store.get_meta(con, d)
    assert m["genres"] == [
        {"genre": "instructional", "p": 1.0},
        {"genre": "tutorial", "p": 1.0},
    ]
    assert m["genres_by"] == "human" and m["genres_at"]
    with pytest.raises(ValueError, match="unknown genre"):
        store.set_genres(con, d, ["pamphlet"])
    with pytest.raises(ValueError, match="skip"):
        store.set_genres(con, d, [])
    assert store.set_genres(con, d, None, skip=True)["skipped"]
    m = store.get_meta(con, d)
    assert "genres" not in m and m["genres_skip"]
    store.set_genres(con, d, None)  # taken back: open again
    assert not {"genres", "genres_skip"} & set(store.get_meta(con, d))
    with pytest.raises(KeyError):
        store.set_genres(con, 99999, ["paper"])


def test_the_sample_takes_each_source_in_turn(con: sqlite3.Connection) -> None:
    """The NAS dump must not fill the gold sample: the sources alternate,
    and the order is the same on every visit."""
    nas = [
        _doc(con, f"nas file {i}", origin={"path": f"/nas/{i}.pdf"}) for i in range(6)
    ]
    zot = [_doc(con, f"zotero paper {i}", source="zotero") for i in range(2)]
    ext = _doc(con, "a page sent from the browser", capture={"by": "extension"})
    first = store.genre_sample(con, limit=3)
    assert first["total"] == 9 and first["labelled"] == 0
    assert {it["source"] for it in first["items"]} == {"nas", "zotero", "extension"}
    assert store.genre_sample(con, limit=3) == first
    item = first["items"][0]
    assert item["opening"] and "summary" in item and item["genres"] == []
    store.set_genres(con, zot[0], ["paper"])
    store.set_genres(con, nas[0], None, skip=True)
    after = store.genre_sample(con, limit=50)
    assert after["total"] == 7 and after["labelled"] == 1 and after["skipped"] == 1
    assert zot[0] not in {it["id"] for it in after["items"]}
    done = store.genre_sample(con, state="labelled")
    assert [it["id"] for it in done["items"]] == [zot[0]]
    assert done["items"][0]["genres"] == ["paper"]
    assert ext in {it["id"] for it in after["items"]}
    with pytest.raises(ValueError):
        store.genre_sample(con, state="everything")


@pytest.fixture()
def client(data_dir: Path) -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_the_door_serves_the_genre_tab(client: TestClient) -> None:
    vocab = client.get("/genres").json()
    assert vocab["levels"][0]["name"] == "informational"
    d = client.post(
        "/ingest", json={"text": "An essay on collapse.", "title": "e"}
    ).json()
    doc_id = d["doc_id"]
    sample = client.get("/documents/genre-sample", params={"limit": 5}).json()
    assert [it["id"] for it in sample["items"]] == [doc_id]
    r = client.put(f"/doc/{doc_id}/genres", json={"genres": ["essay"]})
    assert r.status_code == 200 and r.json()["genres"] == ["essay"]
    assert (
        client.put(f"/doc/{doc_id}/genres", json={"genres": ["x"]}).status_code == 400
    )
    assert (
        client.put("/doc/99999/genres", json={"genres": ["essay"]}).status_code == 404
    )
    labelled = client.get(
        "/documents/genre-sample", params={"state": "labelled"}
    ).json()
    assert labelled["labelled"] == 1 and labelled["items"][0]["genres"] == ["essay"]
    meta = client.get(f"/get/{doc_id}", params={"max_chars": 0}).json()["meta"]
    assert json.dumps(meta["genres"]) == '[{"genre": "essay", "p": 1.0}]'
