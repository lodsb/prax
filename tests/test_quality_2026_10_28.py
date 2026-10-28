"""The quality review of 2026-10-28 (``docs/eval/quality-review-2026-10-28.md``):
the fixes whose behaviour a test can hold. Numbers are the findings'
there."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from prax import config, store
from prax.store import Edge

# ------------------------- 2. what a client may send in a document's meta


def test_a_client_sends_only_the_keys_named_for_it() -> None:
    declared = set(store.DocumentMeta.__annotations__)
    assert declared >= store.CLIENT_META
    assert declared - store.CLIENT_META == store.DOOR_WRITTEN_META
    # a key a new step declares is the door's until named for a client
    assert store.door_written({"photo": {}, "summary": "x", "lang": "en"}) == [
        "photo",
        "summary",
    ]
    # an importer's own block, under its source, is the client's
    assert store.door_written({"source": "mdwiki", "mdwiki": {"key": 1}}) == []


# ------------------------------------------------ 9. one name for a run


def test_a_persons_run_is_never_another_actions() -> None:
    runs = {store.run_name("merge", unique=True) for _ in range(50)}
    assert len(runs) == 50
    assert store.run_name("cleanup", "clips").endswith("-clips")
    assert ":" in store.run_name("twins", sep=":")


# ------------------- 7, 8, 10. one test of personal, one writer of a mark


def test_a_named_face_is_personal_to_every_reader_and_says_why(
    con: sqlite3.Connection,
) -> None:
    from tests.test_face_groups import _face

    doc, (face,) = _face(con, 7)
    store.name_faces(con, [face], name="Ada")
    # marked by the rule, with the reason the Admin page shows
    meta = store.get_meta(con, doc)
    assert meta["sensitivity"] == {
        "state": "suspected",
        "by": "faces",
        "at": meta["sensitivity"]["at"],
    }
    assert meta["private"]["cues"] == ["a face in it is named"]
    # a person opens it: released, and still hidden for its named face
    store.set_sensitivity(con, doc, None)
    counts = store.sensitivity_counts(con)
    assert counts["released"] == 1 and counts["named_faces"] == 1
    assert doc in (store.scope_documents(con, personal="only") or set())
    # the rule pass, which reads the same test: not an open premise
    from prax.store.graph import rules

    (premise,) = rules._premises(con, "depicts")
    assert premise[4] == doc and premise[5] == 1
    # a person decided: no rule marks it again
    assert not store.mark_suspected(con, doc, ["again"], by="faces")


# ---------------------- 19. one policy for what a rule may stand on


def test_a_closure_stands_on_one_documents_premises_too(
    con: sqlite3.Connection,
) -> None:
    """A suspected picture says its town is in a region and the region in
    a country: the town is in the country, sourced to the picture, so the
    wall hides it with the picture."""
    doc = int(store.ingest_text(con, "a picture " * 20, title="p")["doc_id"])
    store.set_sensitivity(con, doc, "suspected")
    for a, b in (("Aldmouth", "Lower Vale"), ("Lower Vale", "Ardenia")):
        store.link(con, Edge(a, "place", "located_in", b, "place"), source_doc=doc)
    store.derive_rules(con)
    got = con.execute(
        "SELECT s.name, t.name, e.source_doc FROM edges e"
        " JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
        " WHERE e.producer = 'rule:transitive' AND e.valid_to IS NULL"
    ).fetchall()
    assert [tuple(r) for r in got] == [("Aldmouth", "Ardenia", doc)]


# ------------------------------------------ 18. a Live Photo's clip


def test_a_clip_beside_its_picture_in_another_phones_naming(
    con: sqlite3.Connection, data_dir: Path
) -> None:
    entry = {"size": 1, "mtime": 0, "sha256": "a" * 64}
    store.record_places(con, "nas", [{**entry, "path": "takeout/IMG_9.JPG"}])
    assert store.live_photo_clip(con, "nas", "takeout/IMG_9.MP4")
    assert not store.live_photo_clip(con, "nas", "takeout/IMG_8.MP4")
    (data_dir / config.CONFIG_NAME).write_text(
        "places:\n  live_clips:\n    suffixes: [.mov]\n", "utf-8"
    )
    assert not store.live_photo_clip(con, "nas", "takeout/IMG_9.MP4")


# ------------------------------------------- 21. a face group's id


def test_a_face_group_id_is_never_given_twice(con: sqlite3.Connection) -> None:
    from tests.test_face_groups import _face

    faces = [_face(con, 20 + i)[1][0] for i in range(6)]

    def group(labels: list[int]) -> set[int]:
        store.replace_face_groups(
            con,
            faces,
            labels,
            [1.0] * len(faces),
            [1.0] * 3,
            model=store.FACE_MODEL,
            run="t",
        )
        return {int(r[0]) for r in con.execute("SELECT id FROM face_groups")}

    first = group([0, 0, 0, 1, 1, 1])
    group([-1] * 6)  # every group dissolves
    again = group([0, 0, 0, 1, 1, 1])
    assert not first & again


# ------------------- 15. a reading of the preview outlives the photos step


def test_a_key_another_reading_keeps_in_the_photo_outlives_a_read_again(
    con: sqlite3.Connection,
) -> None:
    doc = int(store.ingest_text(con, "a picture " * 20, title="p")["doc_id"])
    store.apply_photo(con, doc, facts={"camera": "A"}, copies={}, version=1, by="t")
    meta = store.get_meta(con, doc)
    meta["photo"]["phash"] = {"v": 1, "hash": "abcd"}
    store.set_meta(con, doc, meta)
    store.apply_photo(con, doc, facts={"camera": "B"}, copies={}, version=2, by="t")
    photo = store.get_meta(con, doc)["photo"]
    assert photo["camera"] == "B" and photo["phash"] == {"v": 1, "hash": "abcd"}
    store.photo_failed(con, doc, error="gone", version=3, by="t")
    photo = store.get_meta(con, doc)["photo"]
    assert "camera" not in photo and photo["phash"]["hash"] == "abcd"


# ------------------- 6, 12. places and dates in the library's language


def test_a_place_is_named_in_the_librarys_languages(
    con: sqlite3.Connection, client: Any, monkeypatch: Any
) -> None:
    from prax.graph import gazetteer
    from prax.steps import photos as step_photos
    from tests import test_geocode as geo

    countries = geo.COUNTRIES.replace("XA\tXAA\t901\tXA\tArdenia\n", "")
    countries += "XA\tXAA\t901\tXA\tArdenia" + "\t" * 12 + "900\n"
    alternates = [
        "1\t1001\tde\tAltmünde\t\t\t\t",
        "2\t1001\tde\tAldmünde\t1\t\t\t",  # preferred wins over the first
        "3\t1001\tde\tAltes Maul\t\t\t1\t",  # colloquial: never
        "4\t1\tde\tUnteres Tal\t\t\t\t",  # the region, by admin1's id
        "5\t900\tde\tArdenien\t\t\t\t",  # the country, by countryInfo's id
        "6\t1001\tfr\tAldbouche\t\t\t\t",  # a language not asked for
        "7\t1001\ten\tAldmouth\t\t\t\t",  # its own name: not a label
    ]

    def gaz() -> gazetteer.Gazetteer:
        return gazetteer.Gazetteer(
            gazetteer.parse_towns(geo.TOWNS.splitlines()),
            gazetteer.parse_regions(geo.REGIONS),
            gazetteer.parse_countries(countries),
            ids={
                **gazetteer.parse_region_ids(geo.REGIONS),
                **gazetteer.parse_country_ids(countries),
            },
            alternates=lambda cc: alternates if cc == "XA" else [],
        )

    found = gaz().nearest(45.52, 10.22)
    assert found is not None
    assert gaz().names(found, ["de", "en"]) == {
        "de": {"town": "Aldmünde", "region": "Unteres Tal", "country": "Ardenien"}
    }
    # the door asks for the library's languages; a German library here
    monkeypatch.setenv("PRAX_GRAPH_LABEL_LANGUAGES", "de")
    monkeypatch.setattr(step_photos, "_gazetteer", gaz)
    one = geo._photo_with(con, "a.jpg", 45.51, 10.21, "2018-05-03T10:00:00", 1)
    assert step_photos.Geocode().run(geo._pass(client))
    labels = {
        (str(r[0]), str(r[1]), str(r[2]))
        for r in con.execute(
            "SELECT label, lang, kind FROM entity_labels WHERE producer = 'geonames'"
        )
    }
    assert labels == {
        ("Aldmünde", "de", "pref"),
        ("Unteres Tal", "de", "pref"),
        ("Ardenien", "de", "pref"),
    }
    # a German word finds the picture's town, which finds the picture
    town = store.entity_answering(con, "Aldmünde", "place")
    assert town == store.entity_answering(con, "Aldmouth", "place")
    # the town renamed into German is still the place a second picture is in
    con.execute("UPDATE entities SET name = 'Aldmünde' WHERE id = ?", (town,))
    two = geo._photo_with(con, "b.jpg", 45.51, 10.21, "2018-05-04T10:00:00", 2)
    assert step_photos.Geocode().run(geo._pass(client))
    assert geo._live(con, two)[0][2] == "Aldmünde"
    assert one != two


def test_a_month_is_spelled_in_the_librarys_language(monkeypatch: Any) -> None:
    from datetime import datetime

    from prax.store.graph import events
    from prax.text import dates

    assert dates.spelled_month(3, "de") == "März"
    assert dates.spelled_month(3, "xx") == "March"
    first, last = datetime(2018, 5, 30), datetime(2018, 6, 2)
    monkeypatch.setenv("PRAX_GRAPH_LANGUAGE", "de")
    assert events._days(first, last) == "30 Mai – 2 Juni 2018"


# ------------------------------------- 3. a host names its recogniser


def test_a_host_names_its_face_recogniser(monkeypatch: Any, data_dir: Path) -> None:
    import pytest

    from prax.ml import faces

    assert store.face_model() == "sface-2021dec"
    assert store.face_index_path().name == "vectors-face-sface-2021dec.usearch"
    monkeypatch.setenv("PRAX_FACES_MODEL", "insightface-buffalo")
    with pytest.raises(ValueError, match="not a recogniser of prax"):
        store.face_model()
    # one entry more is a model: its own file and its own length
    spec = faces.RECOGNIZERS["sface-2021dec"]
    other = faces.Recognizer(
        **{**spec.__dict__, "name": "insightface-buffalo", "dim": 512}
    )
    monkeypatch.setitem(faces.RECOGNIZERS, "insightface-buffalo", other)
    assert store.face_index_path().name == "vectors-face-insightface-buffalo.usearch"
    from prax.store.retrieval import faces as stored

    with pytest.raises(ValueError, match="512 finite numbers"):
        stored._checked_vector([0.1] * 128, "insightface-buffalo")


# ---------------------------------- 22. the people list, kept until a write


def test_the_people_list_is_kept_until_the_store_changes(
    con: sqlite3.Connection, monkeypatch: Any
) -> None:
    import importlib

    people = importlib.import_module("prax.store.graph.people")

    reads = []
    counted = people._people
    monkeypatch.setattr(people, "_people", lambda *a: reads.append(1) or counted(*a))
    doc = int(store.ingest_text(con, "a paper " * 20, title="p")["doc_id"])
    store.link(
        con, Edge("p", "paper", "authored_by", "Ada Lovelace", "person"), source_doc=doc
    )
    assert [p["name"] for p in store.people(con)] == ["Ada Lovelace"]
    assert [p["name"] for p in store.people(con)] == ["Ada Lovelace"]
    assert len(reads) == 1  # the second from the memo
    store.link(
        con, Edge("p", "paper", "authored_by", "Alan Turing", "person"), source_doc=doc
    )
    assert {p["name"] for p in store.people(con)} == {"Ada Lovelace", "Alan Turing"}
    assert len(reads) == 2
