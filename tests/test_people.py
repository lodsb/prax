"""A person's page (plan item AT step 0, ``docs/biography.md`` §7): the
facts, the documents, what the pictures that depict them say, all through
the wall; and the people list. The people and places are invented."""

from __future__ import annotations

import hashlib
import sqlite3

from fastapi.testclient import TestClient

from prax import store
from prax.store import Edge


def _doc(con: sqlite3.Connection, text: str) -> int:
    return int(store.ingest_text(con, (text + " ") * 30)["doc_id"])


def _entity(con: sqlite3.Connection, name: str, etype: str = "person") -> int:
    return int(
        con.execute(
            "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
        ).fetchone()[0]
    )


def _picture(con: sqlite3.Connection, n: int, taken: str) -> int:
    digest = hashlib.sha256(f"pic{n}".encode()).hexdigest()
    got = store.register_references(
        con,
        "nas",
        [{"path": f"pics/IMG_{n:04d}.JPG", "size": 1, "mtime": 1, "sha256": digest}],
    )
    doc = int(got["documents"][0]["doc_id"])
    meta = store.get_meta(con, doc)
    meta["taken"] = {"date": taken, "precision": "second", "by": "exif"}
    store.set_meta(con, doc, meta)
    return doc


def _depicts(con: sqlite3.Connection, picture: int, person: str) -> None:
    # what naming a face writes (core 7, store.name_faces)
    node, ntype = store.document_node(con, picture)
    store.link(
        con,
        store.Edge(node, ntype, "depicts", person, "person"),
        source_doc=picture,
        producer="human",
    )
    con.commit()


def _library(con: sqlite3.Connection) -> dict[str, int]:
    paper = _doc(con, "Ada Quill and Bram Tove on tidal clocks")
    letter = _doc(con, "a letter Ada Quill wrote home")
    store.set_sensitivity(con, letter, "personal")
    for who in ("Ada Quill", "Bram Tove"):
        store.link(
            con,
            Edge("Tidal clocks", "document", "authored_by", who, "person"),
            source_doc=paper,
        )
    store.link(
        con,
        Edge(
            "Ada Quill",
            "person",
            "affiliated_with",
            "Harbour Institute",
            "organization",
        ),
        source_doc=paper,
        world_from="2011",
        world_to="2015",
    )
    # only the personal letter says this
    store.link(
        con,
        Edge("Ada Quill", "person", "affiliated_with", "Night School", "organization"),
        source_doc=letter,
    )
    # a second spelling, merged into her
    store.link(
        con,
        Edge("Notes on clocks", "document", "authored_by", "A. Quill", "person"),
        source_doc=paper,
    )
    store.merge_entities(con, _entity(con, "A. Quill"), _entity(con, "Ada Quill"))
    one = _picture(con, 1, "2019-05-03T10:00:00")
    two = _picture(con, 2, "2019-05-04T11:00:00")
    for pic in (one, two):
        node, ntype = store.document_node(con, pic)
        store.link(
            con,
            Edge(node, ntype, "recorded_at", "Coast walk", "event"),
            source_doc=pic,
            producer="human",
            world_from="2019-05-03",
        )
        store.link(
            con,
            Edge(node, ntype, "taken_in", "Aldmouth", "place"),
            source_doc=pic,
            confidence="INFERRED",
        )
        _depicts(con, pic, "Ada Quill")
    _depicts(con, one, "Bram Tove")
    return {"paper": paper, "letter": letter, "one": one, "two": two}


def test_a_person_page_gathers_every_source(con: sqlite3.Connection) -> None:
    docs = _library(con)
    ada = _entity(con, "Ada Quill")
    page = store.person_page(con, ada)
    assert page["name"] == "Ada Quill" and page["type"] == "person"
    assert [lab["label"] for lab in page["labels"]] == ["A. Quill"]
    # the dated fact in the order it holds, the rest by relation
    (dated,) = page["timeline"]
    assert (dated["rel"], dated["other"], dated["world_from"]) == (
        "affiliated_with",
        "Harbour Institute",
        "2011",
    )
    rels = {(r["rel"], r["dir"]): r["count"] for r in page["relations"]}
    assert rels == {("authored_by", "in"): 2, ("affiliated_with", "out"): 1}
    assert {d["id"] for d in page["documents"]} == {docs["paper"], docs["letter"]}
    # the pictures: both, their event, their town, who else is in them
    assert page["photos"] == 2 and page["sample"] == [docs["two"], docs["one"]]
    (event,) = page["events"]
    assert (event["name"], event["photos"], event["first"]) == (
        "Coast walk",
        2,
        "2019-05-03",
    )
    assert page["places"] == [{"name": "Aldmouth", "photos": 2}]
    bram = _entity(con, "Bram Tove")
    assert page["with"] == [{"id": bram, "name": "Bram Tove", "photos": 1}]
    # her alias's page is hers
    assert store.person_page(con, _entity(con, "A. Quill"))["id"] == ada


def test_the_wall_hides_what_only_a_hidden_document_says(
    con: sqlite3.Connection,
) -> None:
    docs = _library(con)
    # a referenced picture is suspected until a person releases it
    store.set_sensitivity(con, docs["two"], None)
    store.link(
        con,
        Edge("Cora Vell", "person", "affiliated_with", "Night School", "organization"),
        source_doc=docs["letter"],
    )
    cora = _entity(con, "Cora Vell")
    token = store.VIEWER.set(store.Viewer(name="t", domains=None, personal=False))
    try:
        page = store.person_page(con, _entity(con, "Ada Quill"))
        others = {f["other"] for r in page["relations"] for f in r["facts"]}
        assert "Night School" not in others
        assert docs["letter"] not in {d["id"] for d in page["documents"]}
        assert page["photos"] == 1 and page["with"] == []
        # a person only a hidden document speaks of is not there
        try:
            store.person_page(con, cora)
        except KeyError:
            pass
        else:
            raise AssertionError("a hidden person's page")
        assert cora not in {p["id"] for p in store.people(con)}
    finally:
        store.VIEWER.reset(token)


def test_the_people_list_and_the_routes(
    con: sqlite3.Connection, client: TestClient
) -> None:
    _library(con)
    ada, bram = _entity(con, "Ada Quill"), _entity(con, "Bram Tove")
    listed = client.get("/people").json()
    # the most photographed first; a merged alias is not a row of its own
    assert [p["id"] for p in listed] == [ada, bram]
    assert listed[0]["photos"] == 2 and listed[1]["photos"] == 1
    # its facts as the page counts them: a picture is not a fact of hers
    assert listed[0]["facts"] == store.person_page(con, ada)["facts"] == 4
    assert [p["id"] for p in client.get("/people", params={"q": "tove"}).json()] == [
        bram
    ]
    assert client.get(f"/people/{ada}").json()["name"] == "Ada Quill"
    place = _entity(con, "Aldmouth", "place")
    assert client.get(f"/people/{place}").status_code == 400
    assert client.get("/people/999999").status_code == 404
