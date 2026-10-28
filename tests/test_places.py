"""Where an original's bytes lie (plan item AQ, stage 1): the ``places``
table, the roots a host maps, and ``store.original_file`` as the one
answer to where an original is read from."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import config, store

PDF = b"%PDF-1.4 a recording's stand-in, some bytes long\n"


def _roots(data_dir: Path, **roots: Path) -> None:
    lines = ["places:", "  roots:"] + [
        f"    {name}: '{where.as_posix()}'" for name, where in roots.items()
    ]
    (data_dir / config.CONFIG_NAME).write_text("\n".join(lines) + "\n", "utf-8")


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


def _not_held(con: sqlite3.Connection) -> tuple[int, str]:
    """A document whose archive file is gone, as a referenced one's is."""
    doc = store.register(con, PDF, mime="application/pdf", title="Creek")
    store.archive_path(doc["hash"]).unlink()
    return int(doc["doc_id"]), doc["hash"]


def test_every_document_so_far_is_held(con: sqlite3.Connection) -> None:
    doc = store.register(con, PDF, mime="application/pdf", title="Creek")
    held = con.execute(
        "SELECT held FROM documents WHERE id = ?", (doc["doc_id"],)
    ).fetchone()[0]
    assert held == 1
    assert store.original_file(con, doc["hash"]) == store.archive_path(doc["hash"])


def test_an_original_not_held_is_read_from_a_place(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    doc_id, digest = _not_held(con)
    seen = _on_share(share, "Field recordings/2019/creek.pdf", PDF)
    store.record_places(con, "nas-media", [seen])
    assert store.original_file(con, digest) == store.archive_path(digest)  # unmapped
    _roots(data_dir, **{"nas-media": share})
    assert store.original_file(con, digest) == share / "Field recordings/2019/creek.pdf"
    got = client.get(f"/doc/{doc_id}/original")
    assert got.status_code == 200 and got.content == PDF


def test_a_place_whose_file_changed_is_not_the_original(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    doc_id, digest = _not_held(con)
    seen = _on_share(share, "creek.pdf", PDF)
    store.record_places(con, "nas", [seen])
    _roots(data_dir, nas=share)
    (share / "creek.pdf").write_bytes(PDF + b"edited")  # another size
    assert not store.original_file(con, digest).exists()
    assert client.get(f"/doc/{doc_id}/original").status_code == 404
    # the same size but touched long after it was hashed is another file too
    (share / "creek.pdf").write_bytes(PDF)
    later = int(seen["mtime"]) + 3600  # type: ignore[call-overload]
    os.utime(share / "creek.pdf", (later, later))
    assert not store.original_file(con, digest).exists()


def test_a_place_stays_inside_its_root(con: sqlite3.Connection) -> None:
    digest = "a" * 64
    for bad in ("../etc/passwd", "/etc/passwd", "a//b", "a\\b", "c:/x", "./a", ""):
        got = store.record_places(
            con, "nas", [{"path": bad, "size": 1, "mtime": 0, "sha256": digest}]
        )
        assert got == {"recorded": 0, "refused": [bad]}
    got = store.record_places(
        con, "nas", [{"path": "a", "size": 1, "mtime": 0, "sha256": "f00"}]
    )
    assert got["refused"] == ["a"]
    with pytest.raises(ValueError):
        store.record_places(con, "NAS share", [])


def test_a_place_seen_again_keeps_when_it_was_first_seen_unless_its_file_changed(
    con: sqlite3.Connection,
) -> None:
    one, two = "1" * 64, "2" * 64
    entry = {"path": "a.wav", "size": 5, "mtime": 10, "sha256": one}
    store.record_places(con, "nas", [entry])
    con.execute("UPDATE places SET first_seen = '2020-01-01T00:00:00Z'")
    con.execute("UPDATE places SET gone = '2020-02-01T00:00:00Z'")
    con.commit()
    store.record_places(con, "nas", [entry])
    row = con.execute("SELECT first_seen, gone FROM places").fetchone()
    assert row[0] == "2020-01-01T00:00:00Z" and row[1] is None
    store.record_places(con, "nas", [{**entry, "sha256": two}])
    row = con.execute("SELECT first_seen, sha256 FROM places").fetchone()
    assert row[0] != "2020-01-01T00:00:00Z" and row[1] == two


def test_a_root_needs_a_name_and_a_directory(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text(
        "places:\n  roots:\n    'My NAS': /mnt\n", "utf-8"
    )
    with pytest.raises(config.ConfigError):
        store.place_roots()


def _crawl(con: sqlite3.Connection, root: str, seen: list[dict[str, object]]) -> None:
    """One crawl of a root: begun, what it saw, ended."""
    since = store.now()
    store.record_places(con, root, seen)
    store.end_crawl(con, root, since)


def _entry(path: str, data: bytes, mtime: int = 100) -> dict[str, object]:
    return {
        "path": path,
        "size": len(data),
        "mtime": mtime,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _age(con: sqlite3.Connection) -> None:
    """Every place last seen a while ago, as by a crawl of last night."""
    con.execute(
        "UPDATE places SET first_seen = '2026-01-01T00:00:00Z',"
        " last_seen = '2026-01-01T00:00:00Z'"
    )
    con.commit()


def test_a_crawl_marks_what_it_did_not_see_gone_and_says_where_it_moved(
    con: sqlite3.Connection,
) -> None:
    a, b = b"a field recording", b"a photo of a creek"
    _crawl(con, "nas", [_entry("2019/creek.wav", a), _entry("photos/creek.jpg", b)])
    _age(con)
    # the recording moved into another folder; the photo was deleted
    _crawl(con, "nas", [_entry("archive/2019/creek.wav", a)])
    found = store.places_of(con, digest=hashlib.sha256(a).hexdigest())
    assert found is not None and found["copies"] == 1 and not found["held"]
    live, gone = found["places"]
    assert live["path"] == "archive/2019/creek.wav" and "gone" not in live
    assert gone["path"] == "2019/creek.wav" and gone["gone"]
    assert gone["moved_to"] == "nas/archive/2019/creek.wav"
    photo = store.places_of(con, root="nas", path="photos/creek.jpg")
    assert photo is not None and photo["copies"] == 0
    assert "moved_to" not in photo["places"][0]
    assert store.places_of(con, root="nas", path="nowhere.jpg") is None


def test_a_crawl_of_one_folder_leaves_the_rest_of_its_root(
    con: sqlite3.Connection,
) -> None:
    _crawl(
        con,
        "nas",
        [_entry("photos/icloud/a.heic", b"a"), _entry("papers/x.pdf", b"x")],
    )
    _age(con)
    since = store.now()
    store.record_places(con, "nas", [_entry("photos/icloud/b.heic", b"b")])
    assert store.end_crawl(con, "nas", since, prefix="photos/icloud") == {"gone": 1}
    gone = dict(con.execute("SELECT path, gone IS NOT NULL FROM places").fetchall())
    assert gone == {
        "photos/icloud/a.heic": 1,
        "photos/icloud/b.heic": 0,
        "papers/x.pdf": 0,
    }
    with pytest.raises(ValueError):
        store.end_crawl(con, "nas", since, prefix="../photos")


def test_copies_and_files_that_lie_only_once(con: sqlite3.Connection) -> None:
    big, small, once = b"B" * 5000, b"s" * 10, b"only here" * 100
    held = store.register(con, b"%PDF-1.4 held " * 20, mime="application/pdf")
    pdf = b"%PDF-1.4 held " * 20
    _crawl(
        con,
        "nas",
        [
            _entry("a/big.mov", big),
            _entry("b/big.mov", big),
            _entry("c/big copy.mov", big),
            _entry("a/s.txt", small),
            _entry("b/s.txt", small),
            _entry("once.wav", once),
            _entry("paper.pdf", pdf),
        ],
    )
    dups = store.duplicate_places(con)
    assert dups["groups"] == 2 and dups["extra_bytes"] == 2 * 5000 + 10
    first = dups["largest"][0]
    assert first["copies"] == 3 and first["size"] == 5000 and len(first["places"]) == 3
    assert store.duplicate_places(con, min_size=100)["groups"] == 1
    assert store.duplicate_places(con, root="elsewhere")["groups"] == 0
    # the paper lies once on the NAS, and prax's archive holds it too
    single = store.single_copies(con)
    assert single["files"] == 1 and single["largest"][0]["path"] == "once.wav"
    assert single["bytes"] == len(once)
    found = store.places_of(con, doc_id=int(held["doc_id"]))
    assert found is not None and found["held"] and found["copies"] == 1


def test_the_door_records_and_answers_where(client: TestClient) -> None:
    data = b"a voice memo"
    since = client.post("/places/start", json={"root": "nas"}).json()["since"]
    entry = _entry("memo.m4a", data)
    assert client.post("/places", json={"root": "nas", "entries": [entry]}).json() == {
        "recorded": 1,
        "refused": [],
    }
    done = client.post("/places/done", json={"root": "nas", "since": since})
    assert done.json() == {"gone": 0}
    got = client.get("/places", params={"hash": entry["sha256"]}).json()
    assert got["copies"] == 1 and got["places"][0]["path"] == "memo.m4a"
    assert not got["places"][0]["mapped"]
    assert client.get("/places", params={"hash": "f" * 64}).status_code == 404
    assert client.get("/places").status_code == 400
    bad = {"root": "nas", "entries": [{**entry, "path": "../x"}]}
    assert client.post("/places", json=bad).json()["refused"] == ["../x"]
    many = {"root": "nas", "entries": [entry] * (store.KNOWN_BATCH + 1)}
    assert client.post("/places", json=many).status_code == 413
    assert client.post("/places/start", json={"root": "My NAS"}).status_code == 400
    assert client.get("/places/single").json()["files"] == 1
    assert client.get("/places/duplicates").json()["groups"] == 0


def test_where_files_lie_is_the_administrators_alone() -> None:
    from prax.wall import auth

    for method, url in (
        ("GET", "/places"),
        ("GET", "/places/single"),
        ("GET", "/places/duplicates"),
        ("POST", "/places"),
        ("POST", "/places/start"),
        ("POST", "/places/done"),
    ):
        assert not any(
            m == method and p.fullmatch(url) for m, p in auth.RESTRICTED_ROUTES
        )
