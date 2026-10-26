"""The photos step (``docs/biography.md`` §7): a picture's EXIF into
``meta.photo`` and ``meta.taken``, a thumbnail and a preview filed in the
archive, served when the share its original lies on is offline, and no
hand-out for a picture the door cannot reach."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from prax import config, store, worker
from prax.parsers import photos
from prax.steps import base
from prax.steps import photos as step_photos

needs_heif = pytest.mark.skipif(
    not photos.available(), reason="pillow-heif not installed"
)


def _photo(
    *, w: int = 800, h: int = 600, orientation: int = 1, fmt: str = "JPEG"
) -> bytes:
    """An invented phone photo: a gradient, with an EXIF block of a taken
    date and offset, a camera, a lens, a GPS position and an orientation."""
    img = Image.new("RGB", (w, h))
    img.putdata(
        [((x * 255) // w, (y * 255) // h, 90) for y in range(h) for x in range(w)]
    )
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 12"
    exif[0x0112] = orientation
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = "2019:05:03 14:22:10"
    sub[0x9011] = "+01:00"
    sub[0xA434] = "iPhone 12 back camera 4.2mm f/1.6"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (38.0, 42.0, 36.0)
    gps[3], gps[4] = "W", (9.0, 8.0, 24.0)
    buf = io.BytesIO()
    img.save(buf, fmt, exif=exif)
    return buf.getvalue()


def test_the_reader_takes_the_exif_and_makes_two_copies_without_it() -> None:
    got = photos.read(_photo(w=2400, h=1800, orientation=6))
    f = got["facts"]
    assert f["taken"] == "2019-05-03T14:22:10+01:00"
    assert f["camera"] == "Apple iPhone 12"
    assert f["lens"].startswith("iPhone 12 back camera")
    assert f["gps"] == {"lat": 38.71, "lon": -9.14}
    # turned a quarter by the orientation tag: shown upright, taller than wide
    assert (f["width"], f["height"]) == (1800, 2400)
    for key, side in (("thumb", photos.THUMB_PX), ("preview", photos.PREVIEW_PX)):
        copy = Image.open(io.BytesIO(got[key]))
        assert copy.format == "JPEG" and max(copy.size) == side
        assert copy.size[1] > copy.size[0]
        # where it was taken stays in meta, behind the wall: never in a copy
        assert not copy.getexif()


def test_a_picture_without_exif_and_bytes_that_are_none() -> None:
    img = Image.new("RGB", (40, 30), "white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    got = photos.read(buf.getvalue())
    assert got["facts"] == {"width": 40, "height": 30}
    with pytest.raises(ValueError):
        photos.read(b"not a picture at all")


@needs_heif
def test_a_phones_heic_is_read() -> None:
    import pillow_heif

    pillow_heif.register_heif_opener()
    img = Image.open(io.BytesIO(_photo()))
    buf = io.BytesIO()
    img.save(buf, "HEIF", exif=img.getexif())
    got = photos.read(buf.getvalue())
    assert got["facts"]["taken"].startswith("2019-05-03")
    assert Image.open(io.BytesIO(got["preview"])).format == "JPEG"


def _share(data_dir: Path, share: Path) -> None:
    (data_dir / config.CONFIG_NAME).write_text(
        f"places:\n  roots:\n    nas: '{share.as_posix()}'\n", "utf-8"
    )


def _on_share(share: Path, rel: str, data: bytes) -> dict[str, object]:
    file = share.joinpath(*rel.split("/"))
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data)
    st = file.stat()
    return {
        "path": rel,
        "size": st.st_size,
        "mtime": int(st.st_mtime),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _pass(client: TestClient) -> base.Pass:
    return base.Pass(
        worker.Door("http://testserver", client=client, name="test-worker")
    )


@needs_heif
def test_the_step_reads_a_referenced_photo_and_its_page_shows_it_offline(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _share(data_dir, share)
    entry = _on_share(share, "pics/malta_2018/IMG_0001.JPG", _photo())
    got = client.post("/ingest/reference", json={"root": "nas", "entries": [entry]})
    doc_id = got.json()["documents"][0]["doc_id"]
    line = step_photos.Photos().run(_pass(client))
    assert line is not None and "1 read" in line
    meta = json.loads(
        con.execute("SELECT meta FROM documents WHERE id = ?", (doc_id,)).fetchone()[0]
    )
    assert meta["taken"] == {
        "date": "2019-05-03T14:22:10+01:00",
        "precision": "second",
        "by": "exif",
    }
    assert meta["photo"]["v"] == photos.VERSION and meta["photo"]["camera"]
    for key in store.PHOTO_COPIES:
        assert store.archive_path(meta["photo"][key]).exists()
    # found by the month it was taken in and its camera
    field = store.document_field(con, doc_id) or ""
    assert "2019" in field and "may" in field and "iPhone" in field
    # read once: nothing more is handed out
    assert client.get("/work/photos").json()["items"] == []
    # the share goes offline: the preview is still served, the original not
    share.rename(tmp_path / "unmounted")
    assert client.get(f"/doc/{doc_id}/original").status_code == 404
    preview = client.get(f"/doc/{doc_id}/preview")
    assert (
        preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    )
    assert client.get(f"/doc/{doc_id}/thumb").status_code == 200


def test_a_picture_out_of_reach_is_not_handed_out_until_it_is_back(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _share(data_dir, share)
    entry = _on_share(share, "pics/IMG_0002.JPG", _photo(w=64, h=48))
    client.post("/ingest/reference", json={"root": "nas", "entries": [entry]})
    share.rename(tmp_path / "away")
    assert client.get("/work/photos").json()["items"] == []
    (tmp_path / "away").rename(share)
    assert len(client.get("/work/photos").json()["items"]) == 1


def test_an_unreadable_picture_is_marked_and_not_asked_again(
    con: sqlite3.Connection, client: TestClient
) -> None:
    doc = store.register(con, b"\x89PNG broken", mime="image/png", title="broken.png")
    item = client.get("/work/photos").json()["items"][0]
    assert item["doc_id"] == doc["doc_id"]
    rep = client.post(
        "/work/photos",
        json={
            "results": [
                {"doc_id": doc["doc_id"], "v": photos.VERSION, "unreadable": "no"}
            ]
        },
    ).json()
    assert rep["actions"] == {"failed": 1}
    assert client.get("/work/photos").json()["items"] == []
    assert store.photo_counts(con, photos.VERSION)["failed"] == 1
    # a fetch that failed is released and kept for later
    other = store.register(con, b"GIF89a later", mime="image/gif", title="later.gif")
    client.get("/work/photos")
    client.post(
        "/work/photos",
        json={"results": [{"doc_id": other["doc_id"], "error": "fetch: offline"}]},
    )
    assert [i["doc_id"] for i in client.get("/work/photos").json()["items"]] == [
        other["doc_id"]
    ]


def test_a_persons_date_is_kept_and_scans_are_not_photos(
    con: sqlite3.Connection, client: TestClient
) -> None:
    doc = store.register(con, _photo(w=32, h=24), mime="image/jpeg", title="a.jpg")
    store.set_meta(
        con,
        doc["doc_id"],
        {"taken": {"date": "1987", "precision": "year", "by": "human"}},
    )
    store.apply_photo(
        con,
        doc["doc_id"],
        facts={"taken": "2019-05-03T14:22:10"},
        copies={},
        version=photos.VERSION,
        by="test",
    )
    assert store.get_meta(con, doc["doc_id"])["taken"]["date"] == "1987"
    store.register(con, b"AT&TFORM djvu", mime="image/vnd.djvu", title="scan.djvu")
    assert store.photo_counts(con, photos.VERSION)["waiting"] == 0


def test_the_copies_are_behind_the_wall(con: sqlite3.Connection) -> None:
    doc = store.register(con, _photo(w=32, h=24), mime="image/jpeg", title="b.jpg")
    store.apply_photo(
        con,
        doc["doc_id"],
        facts={},
        copies={"thumb": b"\xff\xd8 thumb"},
        version=photos.VERSION,
        by="test",
    )
    assert store.photo_copy(con, doc["doc_id"], "thumb") is not None
    store.set_sensitivity(con, doc["doc_id"], "personal")
    token = store.VIEWER.set(store.Viewer(name="t", domains=None, personal=False))
    try:
        assert store.photo_copy(con, doc["doc_id"], "thumb") is None
    finally:
        store.VIEWER.reset(token)
