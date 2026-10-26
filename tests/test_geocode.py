"""Where a picture was taken, as facts of the graph (core 5, the geocode
step; ``docs/biography.md`` §7), and the clean-up of what the first crawl
of a photo library brought that is not a photo. The towns are invented."""

from __future__ import annotations

import io
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from prax import store, worker
from prax.graph import gazetteer
from prax.ml import fetch
from prax.parsers import photos
from prax.steps import base
from prax.steps import photos as step_photos

TOWNS = "\n".join(
    "\t".join(row)
    for row in (
        # id, name, ascii, alternates, lat, lon, class, code, cc, cc2, admin1
        (
            "1001",
            "Aldmouth",
            "Aldmouth",
            "",
            "45.5000",
            "10.2000",
            "P",
            "PPL",
            "XA",
            "",
            "07",
        ),
        (
            "1002",
            "Brindle",
            "Brindle",
            "",
            "45.6000",
            "10.4000",
            "P",
            "PPL",
            "XA",
            "",
            "07",
        ),
        (
            "1003",
            "Corvo",
            "Corvo",
            "",
            "-33.0000",
            "151.0000",
            "P",
            "PPL",
            "XB",
            "",
            "01",
        ),
        (
            "1004",
            "Dunholm",
            "Dunholm",
            "",
            "1.3000",
            "103.8000",
            "P",
            "PPLC",
            "XC",
            "",
            "00",
        ),
        # a quarter of Aldmouth, nearer its centre than the town's own point
        (
            "1005",
            "Oldgate",
            "Oldgate",
            "",
            "45.5100",
            "10.2100",
            "P",
            "PPLX",
            "XA",
            "",
            "07",
        ),
    )
)
REGIONS = "XA.07\tLower Vale\tLower Vale\t1\nXB.01\tSouth Reach\tSouth Reach\t2\n"
COUNTRIES = (
    "#ISO\tISO3\tnum\tfips\tCountry\n"
    "XA\tXAA\t901\tXA\tArdenia\n"
    "XB\tXBB\t902\tXB\tBellmark\n"
    "XC\tXCC\t903\tXC\tDunholm\n"
)


def _gaz() -> gazetteer.Gazetteer:
    return gazetteer.Gazetteer(
        gazetteer.parse_towns(TOWNS.splitlines()),
        gazetteer.parse_regions(REGIONS),
        gazetteer.parse_countries(COUNTRIES),
    )


def test_the_nearest_town_within_reach_and_none_past_it() -> None:
    gaz = _gaz()
    got = gaz.nearest(45.52, 10.22)
    assert got is not None
    assert (got.name, got.region, got.country, got.cc) == (
        "Aldmouth",
        "Lower Vale",
        "Ardenia",
        "XA",
    )
    assert 0 < got.km < 5
    # nearer Brindle than Aldmouth
    near = gaz.nearest(45.59, 10.39)
    assert near is not None and near.name == "Brindle"
    # at sea: a hundred kilometres from every town is nowhere
    assert gaz.nearest(44.5, 10.2) is None
    # a town on the other side of a cell's edge is still found
    edge = gaz.nearest(-32.99, 150.99)
    assert edge is not None and edge.name == "Corvo"


def _photo_with(
    con: sqlite3.Connection, title: str, lat: float, lon: float, taken: str, seed: int
) -> int:
    img = Image.new("RGB", (16, 12), (seed % 255, 40, 90))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    doc = store.register(con, buf.getvalue(), mime="image/jpeg", title=title)
    store.apply_photo(
        con,
        doc["doc_id"],
        facts={"taken": taken, "gps": {"lat": lat, "lon": lon}},
        copies={},
        version=photos.VERSION,
        by="test",
    )
    return int(doc["doc_id"])


def _pass(client: TestClient) -> base.Pass:
    return base.Pass(
        worker.Door("http://testserver", client=client, name="test-worker")
    )


def _live(con: sqlite3.Connection, doc_id: int) -> list[tuple[str, str, str, Any]]:
    return [
        (r[0], r[1], r[2], r[3])
        for r in con.execute(
            "SELECT s.name, e.rel, t.name, e.world_from FROM edges e"
            " JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
            " WHERE e.source_doc = ? AND e.valid_to IS NULL ORDER BY e.id",
            (doc_id,),
        )
    ]


def test_the_step_writes_where_each_photo_was_taken(
    con: sqlite3.Connection, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(step_photos, "_gazetteer", _gaz)
    # two cameras' first photo: one file name, two photos
    one = _photo_with(con, "IMG_0001.JPG", 45.51, 10.21, "2018-05-03T10:00:00", 1)
    two = _photo_with(con, "IMG_0001.JPG", 45.505, 10.205, "2019-07-01T09:00:00", 2)
    sea = _photo_with(con, "IMG_0002.JPG", 40.0, 0.0, "2019-07-02T09:00:00", 3)
    line = step_photos.Geocode().run(_pass(client))
    assert line is not None and "2 placed" in line and "1 near no town" in line
    first = _live(con, one)
    assert first == [
        (f"IMG_0001.JPG (#{one})", "taken_in", "Aldmouth", "2018-05-03"),
        ("Aldmouth", "located_in", "Lower Vale", None),
        ("Lower Vale", "located_in", "Ardenia", None),
    ]
    # the second photo is its own node, and finds the town's facts standing
    assert _live(con, two) == [
        (f"IMG_0001.JPG (#{two})", "taken_in", "Aldmouth", "2019-07-01")
    ]
    assert _live(con, sea) == []
    meta = store.get_meta(con, one)
    assert meta["place"]["name"] == "Aldmouth" and meta["place"]["country"] == "Ardenia"
    assert store.get_meta(con, sea)["place"]["none"] is True
    field = store.document_field(con, one) or ""
    assert "Aldmouth" in field and "Ardenia" in field
    assert store.place_counts(con, gazetteer.VERSION) == {
        "placed": 2,
        "nowhere": 1,
        "waiting": 0,
    }
    # nothing more to hand out; placed again, a photo ends what it said
    assert client.get("/work/geocode").json()["items"] == []
    store.apply_place(
        con,
        one,
        place={
            "name": "Brindle",
            "geonameid": 1002,
            "region": "Lower Vale",
            "country": "Ardenia",
            "cc": "XA",
            "km": 1.0,
        },
        version=gazetteer.VERSION + 1,
        by="test",
    )
    assert [e[:3] for e in _live(con, one)] == [
        (f"IMG_0001.JPG (#{one})", "taken_in", "Brindle"),
        ("Brindle", "located_in", "Lower Vale"),
        ("Lower Vale", "located_in", "Ardenia"),
    ]


def test_a_town_named_as_its_country_is_not_inside_itself(
    con: sqlite3.Connection,
) -> None:
    doc = _photo_with(con, "a.jpg", 1.3, 103.8, "2020-01-01T00:00:00", 4)
    found = _gaz().nearest(1.3, 103.8)
    assert found is not None and found.region == ""
    store.apply_place(
        con,
        doc,
        place={**found.__dict__},
        version=gazetteer.VERSION,
        by="test",
    )
    assert [e[1] for e in _live(con, doc)] == ["taken_in"]


def test_a_hidden_photo_hides_where_it_was_taken(con: sqlite3.Connection) -> None:
    doc = _photo_with(con, "b.jpg", 45.51, 10.21, "2018-05-03T10:00:00", 5)
    store.apply_place(
        con,
        doc,
        place={
            "name": "Aldmouth",
            "geonameid": 1001,
            "region": "Lower Vale",
            "country": "Ardenia",
            "cc": "XA",
            "km": 1.0,
        },
        version=gazetteer.VERSION,
        by="test",
    )
    assert store.find_entities(con, "Aldmouth")
    store.set_sensitivity(con, doc, "personal")
    token = store.VIEWER.set(store.Viewer(name="t", domains=None, personal=False))
    try:
        assert store.find_entities(con, "Aldmouth") == []
    finally:
        store.VIEWER.reset(token)


def test_core_five_moves_the_core_four_stamps(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "a document read under core four " * 20)["doc_id"]
    meta = store.get_meta(con, doc)
    meta["extraction"] = {"ontology_version": "core4+research9"}
    store.set_meta(con, doc, meta)
    con.executescript(
        resources.files("prax.migrations")
        .joinpath("0050_core5.sql")
        .read_text(encoding="utf-8")
    )
    assert (
        store.get_meta(con, doc)["extraction"]["ontology_version"] == "core5+research9"
    )


def test_clean_up_takes_unreadable_pictures_and_live_photo_clips(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path
) -> None:
    broken = store.register(
        con, b"\x37\x00\x00\x10 nokia", mime="image/jpeg", title="a~1.jpg"
    )["doc_id"]
    store.photo_failed(con, broken, error="not a picture", version=1, by="test")
    pic, clip = "a" * 64, "b" * 64
    store.register_references(
        con,
        "nas",
        [
            {"path": "pics/IMG_1.HEIC", "size": 1, "mtime": 1, "sha256": pic},
            {"path": "pics/IMG_1_HEVC.MOV", "size": 1, "mtime": 1, "sha256": clip},
        ],
    )
    # a clip registered as a document before its name was known
    con.execute(
        "INSERT INTO documents (hash, mime, title, meta, held)"
        " VALUES (?, 'video/quicktime', 'IMG_1_HEVC.MOV', '{}', 0)",
        (clip,),
    )
    con.commit()
    clip_id = con.execute(
        "SELECT id FROM documents WHERE hash = ?", (clip,)
    ).fetchone()[0]
    assert [i for i, _ in store.cleanup_set(con, "unreadable")] == [broken]
    assert [i for i, _ in store.cleanup_set(con, "clips")] == [clip_id]
    got = store.retire_set(con, "clips")
    assert got["retired"] == 1
    assert store.cleanup_set(con, "clips") == []


def test_the_hubs_token_goes_to_the_hub_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, str]] = []

    class Resp(io.BytesIO):
        status = 200
        headers: ClassVar[dict[str, str]] = {}

    def urlopen(req: Any, timeout: int = 0) -> Resp:
        sent.append(dict(req.header_items()))
        return Resp(b"x")

    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    monkeypatch.delenv("PRAX_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setattr(fetch.urllib.request, "urlopen", urlopen)
    fetch.download(gazetteer.BASE + gazetteer.ADMIN1, tmp_path / "a.txt")
    fetch.download(fetch.hub_url("org/repo", "f.bin"), tmp_path / "f.bin")
    assert "Authorization" not in sent[0]
    assert sent[1]["Authorization"] == "Bearer hf_secret"
