"""Originals by reference (plan item AQ, stage 3;
``docs/media-by-reference.md``): a recording, a song or a photo becomes a
document without its bytes, is served from its place with ranges, is
counted by the backup and named by heal when out of reach."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from prax import config, store
from prax.wall import auth

CLIP = b"\x00\x00\x00\x18ftypmp42" + b"a creek at dusk " * 64


def _config(data_dir: Path, *, roots: dict[str, Path], private: str = "") -> None:
    lines = ["places:", "  roots:"] + [
        f"    {name}: '{where.as_posix()}'" for name, where in roots.items()
    ]
    if private:
        lines += ["private:", f"  paths: ['{private}']"]
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


def _reference(
    client: TestClient, root: str, *entries: dict[str, object]
) -> dict[str, object]:
    got = client.post(
        "/ingest/reference", json={"root": root, "entries": list(entries)}
    )
    assert got.status_code == 200, got.text
    out: dict[str, object] = got.json()
    return out


def _doc(con: sqlite3.Connection, digest: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT id, held, title, mime, meta FROM documents WHERE hash = ?", (digest,)
    ).fetchone()
    assert row is not None
    return row


def test_a_recording_becomes_a_document_without_its_bytes(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _config(data_dir, roots={"nas-media": share})
    clip = _on_share(share, "Field recordings/2019/creek.mp4", CLIP)
    out = _reference(client, "nas-media", clip)
    assert out["created"] == 1 and out["known"] == 0
    row = _doc(con, str(clip["sha256"]))
    assert row["held"] == 0 and row["mime"] == "video/mp4"
    assert row["title"] == "creek.mp4"
    assert not store.archive_path(str(clip["sha256"])).exists()
    assert store.places_of(con, doc_id=row["id"])["copies"] == 1
    # found by its name and its folders before anything has read it
    field = store.document_field(con, row["id"]) or ""
    assert "creek.mp4" in field and "video" in field and "Field recordings" in field
    # sent again: the same document
    assert _reference(client, "nas-media", clip)["known"] == 1


def test_only_recordings_and_pictures_are_referenced(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _config(data_dir, roots={"nas": share})
    paper = _on_share(share, "papers/a.pdf", b"%PDF-1.4 a paper")
    song = _on_share(share, "music/song.flac", b"fLaC a song " * 20)
    photo = _on_share(share, "photos/IMG_0001.HEIC", b"heic a photo " * 20)
    out = _reference(client, "nas", paper, song, photo)
    assert out["created"] == 2 and out["held_kinds"] == 1
    assert _doc(con, str(song["sha256"]))["mime"] == "audio/flac"
    assert _doc(con, str(photo["sha256"]))["mime"] == "image/heic"
    assert (
        con.execute(
            "SELECT COUNT(*) FROM documents WHERE hash = ?", (paper["sha256"],)
        ).fetchone()[0]
        == 0
    )
    # every file is still a place, document or not
    assert store.places_of(con, digest=str(paper["sha256"]))["copies"] == 1


def test_a_live_photos_clip_is_a_place_of_its_own_and_not_a_document(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _config(data_dir, roots={"nas": share})
    photo = _on_share(share, "icloud/IMG_0002.HEIC", b"heic " * 50)
    clip = _on_share(share, "icloud/IMG_0002.MOV", b"mov " * 50)
    film = _on_share(share, "icloud/IMG_0003.MOV", b"a film " * 50)
    out = _reference(client, "nas", photo, clip, film)
    assert out["created"] == 2 and out["clips"] == 1
    assert store.places_of(con, digest=str(clip["sha256"]))["copies"] == 1
    assert (
        con.execute(
            "SELECT COUNT(*) FROM documents WHERE hash = ?", (clip["sha256"],)
        ).fetchone()[0]
        == 0
    )


def test_a_known_file_stays_held_and_bytes_sent_later_hold_a_referenced_one(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _config(data_dir, roots={"nas": share})
    held = store.register(con, CLIP, mime="video/mp4", title="creek")
    _reference(client, "nas", _on_share(share, "creek.mp4", CLIP))
    assert _doc(con, held["hash"])["held"] == 1
    song = b"ID3 a song " * 40
    entry = _on_share(share, "song.mp3", song)
    _reference(client, "nas", entry)
    assert _doc(con, str(entry["sha256"]))["held"] == 0
    again = store.register(con, song, mime="audio/mpeg")
    assert again["created"] is False
    assert _doc(con, str(entry["sha256"]))["held"] == 1
    assert store.archive_path(str(entry["sha256"])).read_bytes() == song


def test_a_referenced_recording_is_served_from_its_place_with_ranges(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _config(data_dir, roots={"nas-media": share})
    clip = _on_share(share, "creek.mp4", CLIP)
    _reference(client, "nas-media", clip)
    doc_id = _doc(con, str(clip["sha256"]))["id"]
    whole = client.get(f"/doc/{doc_id}/original")
    assert whole.status_code == 200 and whole.content == CLIP
    assert whole.headers["accept-ranges"] == "bytes"
    part = client.get(f"/doc/{doc_id}/original", headers={"Range": "bytes=4-11"})
    assert part.status_code == 206 and part.content == CLIP[4:12]


def test_an_original_out_of_reach_says_where_it_lies(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _config(data_dir, roots={"nas-media": share})
    clip = _on_share(share, "creek.mp4", CLIP)
    _reference(client, "nas-media", clip)
    doc_id = _doc(con, str(clip["sha256"]))["id"]
    _config(data_dir, roots={})  # this host does not mount the share
    got = client.get(f"/doc/{doc_id}/original")
    assert got.status_code == 404
    assert "out of reach" in got.json()["detail"]
    assert "nas-media" in got.json()["detail"]


def test_get_says_whether_prax_holds_it_and_where_it_lies(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _config(data_dir, roots={"nas-media": share})
    clip = _on_share(share, "Field recordings/creek.mp4", CLIP)
    _reference(client, "nas-media", clip)
    doc_id = _doc(con, str(clip["sha256"]))["id"]
    doc = client.get(f"/get/{doc_id}", params={"max_chars": 0}).json()
    assert doc["held"] == 0
    assert doc["places"] == {
        "copies": 1,
        "reachable": True,
        "places": [{"root": "nas-media", "path": "Field recordings/creek.mp4"}],
    }
    brief = client.get(f"/get/{doc_id}", params={"max_chars": 0, "brief": True}).json()
    assert brief["places"]["copies"] == 1
    held = store.register(con, b"%PDF-1.4 held", mime="application/pdf")
    assert "places" not in client.get(f"/get/{held['doc_id']}").json()


def test_the_backup_says_what_it_cannot_bring_back(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _config(data_dir, roots={"nas": share})
    _reference(client, "nas", _on_share(share, "creek.mp4", CLIP))
    store.register(con, b"%PDF-1.4 held", mime="application/pdf")
    assert store.referenced_originals(con) == {"documents": 1, "bytes": len(CLIP)}
    report = store.backup(con, tmp_path / "backup", archive=False)
    assert report["referenced"] == {"documents": 1, "bytes": len(CLIP)}


def test_heal_names_the_originals_out_of_reach_and_nothing_else_about_them(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _config(data_dir, roots={"nas-media": share})
    clip = _on_share(share, "creek.mp4", CLIP)
    song = _on_share(share, "song.flac", b"fLaC " * 40)
    _reference(client, "nas-media", clip, song)

    def found(name: str) -> dict[str, object]:
        report = store.health(con, only=[name])["ailments"][0]
        assert isinstance(report, dict)
        return report

    assert found("unreachable-originals")["count"] == 0
    (share / "song.flac").unlink()
    gone = found("unreachable-originals")
    assert gone["count"] == 1
    assert gone["examples"][0]["roots"] == ["nas-media"]  # type: ignore[index]
    _config(data_dir, roots={})
    assert found("unreachable-originals")["count"] == 2
    # a recording without text waits for its transcription; it is no
    # unparsable type and no file to open for a header
    assert found("documents-without-an-extractor")["count"] == 0
    assert found("not-documents")["count"] == 0


def test_the_private_rules_read_where_a_referenced_file_lies(
    con: sqlite3.Connection, data_dir: Path, tmp_path: Path, client: TestClient
) -> None:
    share = tmp_path / "share"
    _config(data_dir, roots={"nas": share}, private="photos/family/")
    photo = _on_share(share, "photos/family/IMG_0004.HEIC", b"heic family " * 20)
    view = _on_share(share, "photos/hills/IMG_0005.HEIC", b"heic hills " * 20)
    _reference(client, "nas", photo, view)
    row = con.execute(
        "SELECT sensitivity FROM documents WHERE hash = ?", (photo["sha256"],)
    ).fetchone()
    assert row["sensitivity"] == "suspected"
    other = con.execute(
        "SELECT sensitivity FROM documents WHERE hash = ?", (view["sha256"],)
    ).fetchone()
    assert other["sensitivity"] is None


def test_a_reference_is_refused_a_bad_root_path_or_hash(client: TestClient) -> None:
    good = {"path": "a.mp4", "size": 3, "mtime": 0, "sha256": "a" * 64}
    for root, entry in (
        ("NAS", good),
        ("nas", {**good, "path": "../a.mp4"}),
        ("nas", {**good, "sha256": "xyz"}),
    ):
        got = client.post("/ingest/reference", json={"root": root, "entries": [entry]})
        assert got.status_code == 400, (root, entry)
    many = [{**good, "path": f"{i}.mp4"} for i in range(store.KNOWN_BATCH + 1)]
    got = client.post("/ingest/reference", json={"root": "nas", "entries": many})
    assert got.status_code == 413


def test_referencing_is_the_administrators_alone() -> None:
    assert not any(
        m == "POST" and p.fullmatch("/ingest/reference")
        for m, p in auth.RESTRICTED_ROUTES
    )
