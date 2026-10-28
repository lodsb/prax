"""The safety and correctness review of 2026-10-28
(``docs/eval/safety-review-2026-10-28.md``): each fix with a test that
fails on the code the review read. Numbers are the findings' there."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from prax import store

ADMIN = "admin-secret"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_TOKEN", ADMIN)
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _as(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def _token(client: TestClient, name: str, role: str) -> dict[str, str]:
    got = client.post("/tokens", json={"name": name, "role": role}, headers=_as(ADMIN))
    return _as(got.json()["secret"])


# --------------------------------- 1. a picture's copy names a host file


def test_a_copy_is_an_archive_digest_and_nothing_else(client: TestClient) -> None:
    con = client.app.state.con
    db = str(store.config.data_dir() / "prax.db")
    writer = _token(client, "w", "contributor")
    for thumb in (db, "../../prax.db", "ab/../../prax.db"):
        got = client.post(
            "/ingest",
            json={"text": "a picture " * 20, "meta": {"photo": {"thumb": thumb}}},
            headers=writer,
        )
        assert got.status_code == 400, thumb
    # a meta that holds one anyway (written before the door refused it)
    doc = int(store.ingest_text(con, "a picture " * 20, title="pic")["doc_id"])
    meta = store.get_meta(con, doc)
    meta["photo"] = {"thumb": db, "preview": "../" * 3 + "prax.db"}
    store._put_meta(con, doc, meta)
    con.commit()
    reader = _token(client, "r", "reader")
    for which in ("thumb", "preview"):
        assert store.photo_copy(con, doc, which) is None
        got = client.get(f"/doc/{doc}/{which}", headers=reader)
        assert got.status_code == 404 and b"SQLite" not in got.content
    with pytest.raises(ValueError):
        store.archive_path(db)


# ------------------------------- 2. a faces reading again leaves answers


def _reread(con: Any, doc: int, boxes: list[list[float]], version: int) -> str:
    return store.apply_faces(
        con,
        doc,
        faces=[
            {"box": b, "landmarks": [[b[0], b[1]]] * 5, "score": 0.9, "px": 60}
            for b in boxes
        ],
        version=version,
        by="test",
        width=200,
        height=150,
    )


def _depicted(con: Any, doc: int) -> list[str]:
    return sorted(
        r[0]
        for r in con.execute(
            "SELECT t.name FROM edges e JOIN entities t ON t.id = e.dst"
            " WHERE e.rel = 'depicts' AND e.valid_to IS NULL AND e.source_doc = ?",
            (doc,),
        )
    )


def test_a_faces_reading_again_carries_what_a_person_said(con: Any) -> None:
    from prax.ml import faces as faces_ml
    from tests.test_face_groups import _face

    doc, (ada, lost) = _face(con, 3, n=2)
    named = store.name_faces(con, [ada], name="Ada")
    who = int(named["entity_id"])
    store.refuse_faces(con, [lost], entity_id=who)
    later = faces_ml.VERSION + 1
    # the first face found again a little moved, the second not at all
    assert _reread(con, doc, [[0.11, 0.21, 0.2, 0.3]], later) == "found"
    (new,) = [f["id"] for f in store.faces_of(con, doc)]
    assert store.face_answers(con, [new])[new]["name"] == "Ada"
    assert _depicted(con, doc) == ["Ada"]
    # one live answer, on the new face; the old face's ended
    live = con.execute(
        "SELECT face_id FROM face_names WHERE ended_at IS NULL"
    ).fetchall()
    assert [r[0] for r in live] == [new]
    # a late post of the earlier reading changes nothing
    assert _reread(con, doc, [[0.5, 0.5, 0.1, 0.1]], later - 1) == "stale"
    assert [f["id"] for f in store.faces_of(con, doc)] == [new]
    # the action's undo still takes back both its answer and the refusal
    got = store.unname_faces(con, run=named["run"])
    assert got["faces"] == 1 and _depicted(con, doc) == []
    # an answer whose face is not found again stays, and can be undone
    store.name_faces(con, [new], name="Grace")
    assert _reread(con, doc, [[0.7, 0.6, 0.1, 0.1]], later + 1) == "found"
    assert _depicted(con, doc) == ["Grace"]
    run = con.execute(
        "SELECT run FROM face_names WHERE ended_at IS NULL AND verdict = 'person'"
    ).fetchone()[0]
    store.unname_faces(con, run=run)
    assert _depicted(con, doc) == []


# ----------------------- 3, 11-15. what a picture, a file or a step raises


def _gps_photo(lat: tuple[float, ...]) -> bytes:
    import io

    from PIL import Image, TiffImagePlugin

    exif = Image.Exif()
    gps = exif.get_ifd(0x8825)
    zero = TiffImagePlugin.IFDRational(0, 0)
    gps[1], gps[2] = "N", tuple(TiffImagePlugin.IFDRational(v) for v in lat)
    gps[3], gps[4] = "E", (zero, zero, zero)
    gps[6] = zero
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 90, 90)).save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_a_position_of_no_fix_is_no_position(con: Any) -> None:
    from prax.parsers import photos

    got = photos.read(_gps_photo((38.0, 42.0, 36.0)))
    assert "gps" not in got["facts"]
    json.dumps(got["facts"], allow_nan=False)  # what the worker posts
    doc = int(store.ingest_text(con, "a picture " * 20, title="p")["doc_id"])
    store.apply_photo(
        con,
        doc,
        facts={"gps": {"lat": float("nan"), "lon": 3.0}},
        copies={},
        version=1,
        by="t",
    )
    assert "gps" not in store.get_meta(con, doc)["photo"]


def test_a_picture_past_the_pixel_limit_is_that_pictures_trouble(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    from prax.parsers import photos

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)  # 64 x 48 is past 2x
    with pytest.raises(ValueError, match="DecompressionBombError"):
        photos.read(_gps_photo((1.0, 0.0, 0.0)))


def test_a_download_cut_short_is_not_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import urllib.request

    from prax.ml import fetch

    class Short(io.BytesIO):
        status = 200
        headers: ClassVar[dict[str, str]] = {"Content-Length": "10"}

    monkeypatch.delenv("PRAX_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Short(b"12345"))
    dest = tmp_path / "model.onnx"
    with pytest.raises(OSError, match="5 of 10"):
        fetch.download("https://example.invalid/model.onnx", dest)
    assert not dest.exists()
    assert (tmp_path / "model.onnx.part").read_bytes() == b"12345"


def test_a_step_whose_resource_fails_rests_and_the_others_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prax import worker
    from prax.steps import base

    def broken() -> None:
        raise OSError("geonames.org is down")

    line = base.ready("geocode-test", broken)
    assert line is not None and "geonames.org is down" in line
    assert base.resting("geocode-test")
    assert base.ready("geocode-test", lambda: None) is None
    assert not base.resting("geocode-test")

    ran: list[str] = []

    class Boom(base.Step):
        def run(self, p: Any) -> str | None:
            ran.append("boom")
            raise RuntimeError("one step's trouble")

    class Fine(base.Step):
        def run(self, p: Any) -> str | None:
            ran.append("fine")
            return "did its work"

    steps = {"boom": Boom(), "fine": Fine()}
    monkeypatch.setattr(worker.steps_mod, "get", steps.__getitem__)
    with pytest.raises(RuntimeError, match="one step's trouble"):
        worker.run_once(None, steps=("boom", "fine"))  # type: ignore[arg-type]
    assert ran == ["boom", "fine"]


def test_a_name_the_door_cannot_take_leaves_the_rest_of_its_batch(
    con: Any,
) -> None:
    digest = "a" * 64
    entries = [
        {"path": p, "size": 1, "mtime": 0, "sha256": digest}
        for p in ("photos/Re:", "b:evil.jpg", "a/x:stream", "ok/fine.jpg")
    ]
    got = store.record_places(con, "nas", entries)
    assert got == {
        "recorded": 1,
        "refused": ["photos/Re:", "b:evil.jpg", "a/x:stream"],
    }
    made = store.register_references(
        con,
        "nas",
        [{**entries[0], "sha256": "b" * 64}, {**entries[3], "sha256": "c" * 64}],
    )
    assert made["refused"] == ["photos/Re:"] and made["created"] == 1


# ------------------------------------- 23, 24. what a crawl says it saw


def test_a_crawl_counts_a_folder_it_could_not_list_and_a_file_that_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from tests.test_send import _sender

    send = _sender()
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.jpg").write_bytes(b"one")
    real = os.walk

    def walk(top: str, onerror: Any = None, **kw: Any) -> Any:
        onerror(PermissionError(13, "denied", str(tmp_path / "locked")))
        yield from real(top, onerror=onerror, **kw)

    monkeypatch.setattr(send.os, "walk", walk)
    stats: dict[str, int] = {}
    assert list(send.walk([str(tmp_path)], None, stats=stats))
    assert stats["unlisted"] == 1
    # a file written while it is hashed is no place yet
    state = send.State(None)
    grows = tmp_path / "a" / "one.jpg"

    def read(n: int) -> None:
        grows.write_bytes(b"one and more")

    with pytest.raises(OSError, match="changed while"):
        state.hash_with_stat(str(grows), read)
    digest, st = state.hash_with_stat(str(grows))
    assert st.st_size == len(b"one and more") and len(digest) == 64


# ------------------------------------ 3 (data). an event candidate's id


def test_an_event_candidate_keeps_its_id_and_none_is_given_twice(con: Any) -> None:
    from tests.test_events import _photos

    may = _photos(con, "2019-05-03T10:00:00", 10, folder="may", tag="m")
    store.find_event_candidates(con)
    (shown,) = [c["id"] for c in store.event_candidates_page(con)["items"]]
    # an earlier group appears: the page's candidate keeps its id
    jan = _photos(con, "2018-01-02T10:00:00", 10, folder="jan", tag="j")
    store.find_event_candidates(con)
    ids = {c["id"]: c["photos"] for c in store.event_candidates_page(con)["items"]}
    assert ids[shown] == len(may) and len(ids) == 2
    # every group gone: the page's id is still no one's
    for doc in may + jan:
        store.retire_document(con, doc, reason="t")
    store.find_event_candidates(con)
    assert store.event_candidates_page(con)["items"] == []
    _photos(con, "2020-07-01T10:00:00", 10, folder="jul", tag="u")
    store.find_event_candidates(con)
    assert shown not in {c["id"] for c in store.event_candidates_page(con)["items"]}
    with pytest.raises(KeyError):
        store.decide_event(con, shown, event=True, name="May")


# ------------------------------------------- 4, 9, 20. a town's facts


def _placed(con: Any, doc: int, town: str, region: str, country: str) -> None:
    store.apply_place(
        con,
        doc,
        place={"name": town, "region": region, "country": country, "geonameid": 7},
        version=2,
        by="t",
    )


def _inside(con: Any) -> set[tuple[str, str]]:
    return {
        (r[0], r[1])
        for r in con.execute(
            "SELECT s.name, t.name FROM edges e JOIN entities s ON s.id = e.src"
            " JOIN entities t ON t.id = e.dst WHERE e.rel = 'located_in'"
            " AND e.valid_to IS NULL"
        )
    }


def test_a_towns_facts_stand_while_one_picture_states_them(con: Any) -> None:
    from tests.test_geocode import _photo_with

    one = _photo_with(con, "a.jpg", 45.5, 10.2, "2018-05-03T10:00:00", 1)
    two = _photo_with(con, "b.jpg", 45.5, 10.2, "2019-05-03T10:00:00", 120)
    for doc in (one, two):
        _placed(con, doc, "Aldmouth", "Lower Vale", "Ardenia")
    store.retire_document(con, one, reason="t")
    assert ("Aldmouth", "Lower Vale") in _inside(con)
    # placed again near no town: the other picture's statement stands
    store.unretire_document(con, one)
    store.apply_place(con, two, place=None, version=3, by="t")
    _placed(con, one, "Aldmouth", "Lower Vale", "Ardenia")
    assert {("Aldmouth", "Lower Vale"), ("Lower Vale", "Ardenia")} <= _inside(con)


def test_two_places_of_one_name_are_two_nodes(con: Any) -> None:
    from tests.test_geocode import _photo_with

    docs = [
        _photo_with(con, f"{i}.jpg", 45.5, 10.2, "2018-05-03T10:00:00", 40 * i + 1)
        for i in range(4)
    ]
    _placed(con, docs[0], "Paris", "Ile-de-France", "France")
    _placed(con, docs[1], "Paris", "Texas", "United States")
    _placed(con, docs[2], "Atlanta", "Georgia", "United States")
    _placed(con, docs[3], "Tbilisi", "", "Georgia")
    inside = _inside(con)
    assert {
        ("Paris", "Ile-de-France"),
        ("Ile-de-France", "France"),
        ("Paris, Texas", "Texas"),
        ("Texas", "United States"),
        ("Atlanta", "Georgia"),
        ("Georgia", "United States"),
        ("Tbilisi", "Georgia (country)"),
    } == inside
    store.derive_rules(con)
    # the closure puts no place in another's country
    assert ("Tbilisi", "United States") not in _inside(con)
    assert ("Paris", "United States") not in _inside(con)


# ------------------------------- 5, 7, 18, 21. people folded and renamed


def _person(con: Any, name: str) -> int:
    doc = int(store.ingest_text(con, f"{name} wrote it. " * 20, title=name)["doc_id"])
    node = store.document_node(con, doc)
    store.link(
        con, store.Edge(node[0], node[1], "authored_by", name, "person"), source_doc=doc
    )
    return int(
        con.execute(
            "SELECT id FROM entities WHERE name = ? AND type = 'person'", (name,)
        ).fetchone()[0]
    )


def _canon(con: Any, eid: int) -> int:
    row = con.execute(
        "SELECT COALESCE(canonical_id, id) FROM entities WHERE id = ?", (eid,)
    ).fetchone()
    return int(row[0])


def test_undoing_a_merge_gives_back_what_the_folded_one_held(con: Any) -> None:
    a, b, c = (_person(con, n) for n in ("Ada", "Ada L.", "A. Lovelace"))
    store.merge_people(con, c, into=b)
    second = store.merge_people(con, b, into=a)["run"]
    assert _canon(con, c) == a
    store.unmerge_run(con, second)
    assert _canon(con, b) == b and _canon(con, c) == b


def test_offers_follow_a_merge(con: Any) -> None:
    from tests.test_face_naming import _grouped

    people = _grouped(con)
    a = [f for _, f in people["a"]]
    one = store.name_faces(con, a[:3], name="Ada")["entity_id"]
    two = store.name_faces(con, a[3:5], name="Lovelace")["entity_id"]
    store.face_matches(con)  # the cache built before the merge
    store.merge_people(con, two, into=one)
    offered = {int(item["entity_id"]) for item in store.face_matches(con)["items"]}
    assert one in offered and two not in offered


def test_a_person_renamed_back_and_to_a_folded_ones_name(con: Any) -> None:
    anna = _person(con, "Anna")
    store.rename_person(con, anna, "Anne")
    assert store.rename_person(con, anna, "Anna")["name"] == "Anna"
    other = _person(con, "Anna K.")
    store.merge_people(con, other, into=anna)
    assert store.rename_person(con, anna, "Anna K.")["name"] == "Anna K."


# ------------------------- 6. a name only a hidden document gave a person


def test_a_name_only_a_hidden_document_gave_finds_nothing(client: TestClient) -> None:
    con = client.app.state.con
    jane = _person(con, "Jane Doe")
    bank = int(
        store.ingest_text(con, "Kontoauszug of the account. " * 20, title="Bank")[
            "doc_id"
        ]
    )
    node = store.document_node(con, bank)
    store.link(
        con,
        store.Edge(node[0], node[1], "mentions", "Landlord Zebrafinch", "person"),
        source_doc=bank,
    )
    store.set_sensitivity(con, bank, "personal")
    landlord = int(
        con.execute(
            "SELECT id FROM entities WHERE name = 'Landlord Zebrafinch'"
        ).fetchone()[0]
    )
    store.merge_people(con, landlord, into=jane)
    reader = _token(client, "r", "reader")
    found = client.get("/entities", params={"q": "zebra"}, headers=reader).json()
    assert "Zebrafinch" not in json.dumps(found)
    assert client.get("/entities", params={"q": "jane"}, headers=reader).json()
    mine = client.get("/entities", params={"q": "zebra"}, headers=_as(ADMIN)).json()
    assert mine and mine[0]["as"] == "Landlord Zebrafinch"


# ------------------------- 8. pictures on a share that is not mounted


def test_pictures_out_of_reach_never_hide_the_ones_in_reach(
    con: Any, data_dir: Path, tmp_path: Path
) -> None:
    from prax import config
    from prax.parsers import photos
    from tests.test_photos import _on_share, _photo

    share = tmp_path / "share"
    (data_dir / config.CONFIG_NAME).write_text(
        f"places:\n  roots:\n    nas: '{share.as_posix()}'\n"
        f"    usb: '{(tmp_path / 'unplugged').as_posix()}'\n",
        "utf-8",
    )
    away = [
        {"path": f"p/{i}.jpg", "size": 1, "mtime": 0, "sha256": f"{i:064x}"}
        for i in range(60)
    ]
    store.register_references(con, "usb", away)
    # and twenty on the mounted share whose files were touched since
    moved = [_on_share(share, f"m/{i}.jpg", _photo(w=8 + i, h=8)) for i in range(20)]
    store.register_references(con, "nas", [{**e, "mtime": 1} for e in moved])
    here = _on_share(share, "pics/IMG_0001.JPG", _photo())
    (doc,) = store.register_references(con, "nas", [here])["documents"]
    assert store.photos_due(con, photos.VERSION, limit=5) == [doc["doc_id"]]


# ---------------------- 29. a named face, in a picture a person opened


def test_a_named_face_is_for_a_token_with_personal_only(client: TestClient) -> None:
    from tests.test_face_groups import _face

    con = client.app.state.con
    doc, (face,) = _face(con, 5)
    store.set_sensitivity(con, doc, None)  # a person opened the picture
    store.name_faces(con, [face], name="Ada")
    assert store.get_meta(con, doc)["sensitivity"]["by"] == "human"
    reader = _token(client, "r", "reader")
    friend = client.post(
        "/tokens", json={"name": "p", "personal": True}, headers=_as(ADMIN)
    ).json()["secret"]
    assert client.get(f"/graph/document/{doc}", headers=reader).status_code in (
        200,
        404,
    )
    assert "Ada" not in client.get(f"/graph/document/{doc}", headers=reader).text
    assert client.get(f"/get/{doc}", headers=reader).status_code == 404
    assert "Ada" in client.get(f"/graph/document/{doc}", headers=_as(friend)).text


def test_a_reading_that_names_the_same_town_keeps_its_facts(con: Any) -> None:
    from tests.test_geocode import _photo_with

    doc = _photo_with(con, "a.jpg", 45.5, 10.2, "2018-05-03T10:00:00", 1)
    _placed(con, doc, "Aldmouth", "Lower Vale", "Ardenia")
    live = "SELECT id FROM edges WHERE source_doc = ? AND valid_to IS NULL"
    before = {r[0] for r in con.execute(live, (doc,))}
    names = {"de": {"town": "Aldmünde", "country": "Ardenien"}}
    store.apply_place(
        con,
        doc,
        place={
            "name": "Aldmouth",
            "region": "Lower Vale",
            "country": "Ardenia",
            "geonameid": 7,
            "names": names,
        },
        version=3,
        by="t",
    )
    assert {r[0] for r in con.execute(live, (doc,))} == before
    assert store.get_meta(con, doc)["place"]["v"] == 3
    assert store.entity_answering(con, "Aldmünde", "place") == (
        store.entity_answering(con, "Aldmouth", "place")
    )
    # another town: the facts move
    _placed(con, doc, "Brinby", "Lower Vale", "Ardenia")
    assert {r[0] for r in con.execute(live, (doc,))}.isdisjoint(before)
