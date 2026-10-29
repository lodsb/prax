"""The media view (plan item AQ, "A media view"): the referenced photos and
recordings grouped by folder, by month taken and by event, a group
released or kept personal in one answer, and the answer taken back."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from prax import config, store

PHOTO = b"\xff\xd8\xff\xe0 a generated picture "


def _share(data_dir: Path, tmp_path: Path) -> Path:
    share = tmp_path / "share"
    (data_dir / config.CONFIG_NAME).write_text(
        f"places:\n  roots:\n    nas: '{share.as_posix()}'\n", "utf-8"
    )
    return share


def _entry(share: Path, rel: str, n: int) -> dict[str, object]:
    data = PHOTO + str(n).encode() * 40
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


def _library(client: TestClient, data_dir: Path, tmp_path: Path) -> dict[str, int]:
    """Five photos in a share: three in Trips/2019/Lake (one of them taken
    in 2019-07, one in 2019-08), one in Trips/2020, one undated in Misc."""
    share = _share(data_dir, tmp_path)
    paths = [
        "Photos/Trips/2019/Lake/a.jpg",
        "Photos/Trips/2019/Lake/b.jpg",
        "Photos/Trips/2019/Lake/c.jpg",
        "Photos/Trips/2020/d.jpg",
        "Photos/Misc/e.jpg",
    ]
    entries = [_entry(share, p, i) for i, p in enumerate(paths)]
    got = client.post("/ingest/reference", json={"root": "nas", "entries": entries})
    assert got.status_code == 200, got.text
    con = client.app.state.con
    ids = {
        p.rsplit("/", 1)[1][0]: int(
            con.execute(
                "SELECT id FROM documents WHERE hash = ?", (e["sha256"],)
            ).fetchone()[0]
        )
        for p, e in zip(paths, entries, strict=True)
    }
    for name, taken in (
        ("a", "2019-07-03"),
        ("b", "2019-07-04"),
        ("c", "2019-08-01"),
        ("d", "2020-05-01"),
    ):
        meta = store.get_meta(con, ids[name])
        store.set_meta(con, ids[name], {**meta, "taken": {"date": taken}})
    return ids


def _groups(client: TestClient, **params: str) -> dict[str, dict[str, object]]:
    got = client.get("/media", params=params)
    assert got.status_code == 200, got.text
    return {g["key"]: g for g in got.json()["groups"]}


def test_a_share_opens_where_its_folders_part(
    client: TestClient, data_dir: Path, tmp_path: Path
) -> None:
    _library(client, data_dir, tmp_path)
    top = client.get("/media").json()
    # nas: and nas:Photos hold one folder each: the view starts below them
    assert top["under"] == "nas:Photos"
    assert top["counts"] == {"suspected": 5, "open": 0, "personal": 0}
    groups = {g["key"]: g for g in top["groups"]}
    assert {k: g["count"] for k, g in groups.items()} == {
        "nas:Photos/Misc": 1,
        "nas:Photos/Trips": 4,
    }
    assert groups["nas:Photos/Trips"]["kinds"] == {"image": 4}
    assert groups["nas:Photos/Trips"]["suspected"] == 4
    trips = _groups(client, under="nas:Photos/Trips")
    assert {k: g["count"] for k, g in trips.items()} == {
        "nas:Photos/Trips/2019": 3,
        "nas:Photos/Trips/2020": 1,
    }
    # a folder's own files are a group of their own, "(here)"
    here = _groups(client, under="nas:Photos/Trips/2020")
    assert [(g["key"], g["label"]) for g in here.values()] == [
        ("nas:Photos/Trips/2020", "(here)")
    ]


def test_by_month_and_by_event(
    client: TestClient, data_dir: Path, tmp_path: Path
) -> None:
    ids = _library(client, data_dir, tmp_path)
    years = _groups(client, by="month")
    assert {k: g["count"] for k, g in years.items()} == {
        "2019": 3,
        "2020": 1,
        "undated": 1,
    }
    months = _groups(client, by="month", under="2019")
    assert {k: g["count"] for k, g in months.items()} == {"2019-07": 2, "2019-08": 1}
    con = client.app.state.con
    for name in ("a", "b"):
        store.link(
            con,
            store.Edge(
                f"photo {name}", "document", "recorded_at", "Lake weekend", "event"
            ),
            source_doc=ids[name],
            producer="human",
        )
    events = _groups(client, by="event")
    ((key, group),) = events.items()
    assert group["label"] == "Lake weekend" and group["count"] == 2
    page = client.get("/media/pictures", params={"by": "event", "key": key}).json()
    assert [it["doc_id"] for it in page["items"]] == [ids["a"], ids["b"]]
    assert client.get("/media", params={"by": "colour"}).status_code == 400


def test_a_folder_released_at_once_and_taken_back(
    client: TestClient, data_dir: Path, tmp_path: Path
) -> None:
    ids = _library(client, data_dir, tmp_path)
    con = client.app.state.con
    # decided one by one before: personal, and stays so
    store.set_sensitivity(con, ids["c"], "personal")
    got = client.post(
        "/media/decide",
        json={"by": "folder", "key": "nas:Photos/Trips", "state": "open"},
    ).json()
    assert (got["changed"], got["left"]) == (3, 1)

    def state(name: str) -> str | None:
        row = con.execute(
            "SELECT sensitivity FROM documents WHERE id = ?", (ids[name],)
        ).fetchone()
        return row[0]  # type: ignore[no-any-return]

    assert [state(n) for n in "abcde"] == [None, None, "personal", None, "suspected"]
    page = client.get(
        "/media/pictures", params={"by": "folder", "key": "nas:Photos/Trips"}
    ).json()
    assert page["total"] == 4
    assert [it["state"] for it in page["items"]] == ["open", "open", "personal", "open"]
    back = client.post("/media/undo", json={"run": got["run"]}).json()
    assert back["restored"] == 3
    assert [state(n) for n in "abcde"] == [
        "suspected",
        "suspected",
        "personal",
        "suspected",
        "suspected",
    ]
    meta = json.loads(
        con.execute("SELECT meta FROM documents WHERE id = ?", (ids["a"],)).fetchone()[
            0
        ]
    )
    assert "run" not in (meta.get("sensitivity") or {})
    bad = client.post(
        "/media/decide", json={"by": "folder", "key": "nas:Photos", "state": "maybe"}
    )
    assert bad.status_code == 400


def test_the_media_routes_are_the_administrators(
    con: sqlite3.Connection,
) -> None:
    from prax.wall import auth

    allowed = [p for _, p in auth.READER_ROUTES + auth.CONTRIBUTOR_ROUTES]
    assert not any("media" in getattr(p, "pattern", p) for p in allowed)
