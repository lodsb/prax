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
        with pytest.raises(ValueError):
            store.record_places(
                con, "nas", [{"path": bad, "size": 1, "mtime": 0, "sha256": digest}]
            )
    with pytest.raises(ValueError):
        store.record_places(
            con, "nas", [{"path": "a", "size": 1, "mtime": 0, "sha256": "f00"}]
        )
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
