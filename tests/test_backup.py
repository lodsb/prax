"""A backup is a store somewhere else: the database as one snapshot, the
index files, the config, and only the archive files the copy lacks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import config, store
from tests.conftest import wait_job


def _fill(con: Any, n: int = 3) -> list[int]:
    return [
        store.ingest_text(
            con, f"document {i} about granular synthesis " * 20, title=f"D{i}"
        )["doc_id"]
        for i in range(n)
    ]


def _open(copy: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("PRAX_DATA_DIR", str(copy))
    store._indexes.clear()
    con = store.connect()
    store.init_db(con)
    return con


def test_the_copy_is_a_store_and_the_second_run_copies_only_the_new(
    con: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _fill(con)
    (config.data_dir() / "prax.yaml").write_text("steps: {}\n", encoding="utf-8")
    dest = tmp_path / "copy"
    first = store.backup(con, dest)
    assert first["archive"]["copied"] == first["archive"]["files"] > 0
    assert first["database_bytes"] > 0
    assert (dest / "prax.db").is_file() and (dest / "prax.yaml").is_file()
    assert not (dest / "prax.db.part").exists()
    manifest = json.loads((dest / store.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["dest"] == str(dest)
    # a day later: one more document, and only its files are copied
    new = store.ingest_text(con, "a new arrival about wavetables " * 20, title="N")
    second = store.backup(con, dest)
    # plain text: its original and its text artifact are the same bytes
    assert second["archive"]["copied"] == 1
    assert second["archive"]["files"] == first["archive"]["files"] + 1
    con.close()
    # the copy answers as a store
    copy = _open(dest, monkeypatch)
    try:
        assert {r["id"] for r in copy.execute("SELECT id FROM documents")} == {
            *ids,
            new["doc_id"],
        }
        hits = store.search(copy, "wavetables", limit=5)
        assert [h["doc_id"] for h in hits] == [new["doc_id"]]
        assert store.get_document(copy, ids[0])["text"].startswith("document 0")
    finally:
        copy.close()


def test_the_target_must_be_absolute_and_outside_the_store(
    con: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="absolute"):
        store.backup_target("copies/here")
    with pytest.raises(ValueError, match="outside"):
        store.backup_target(config.data_dir() / "backup")
    with pytest.raises(ValueError, match="no backup directory"):
        store.backup_target(None)
    monkeypatch.setenv("PRAX_BACKUP", str(tmp_path / "nightly"))
    assert store.backup_target(None) == tmp_path / "nightly"
    assert store.backup_target(tmp_path / "given") == tmp_path / "given"


def test_the_door_runs_it_as_a_job(tmp_path: Path) -> None:
    from prax.api import app

    with TestClient(app) as door:
        door.post("/ingest", json={"text": "x " * 100, "title": "X"})
        bad = door.post("/backup", json={"dest": "relative"})
        assert bad.status_code == 400
        started = door.post("/backup", json={"dest": str(tmp_path / "copy")}).json()
        row = wait_job(door, started["job"])
        assert row["status"] == "done", row
        assert row["note"].startswith("done:")
        assert (tmp_path / "copy" / "prax.db").is_file()
        assert door.get("/jobs/999999").status_code == 404


def test_without_the_archive_the_copy_is_what_cannot_be_rebuilt(
    con: Any, tmp_path: Path
) -> None:
    """A disk too small for the originals still gets the database, the
    indexes and the config every night; the manifest says the archive
    was left out, and a later full run adds it."""
    _fill(con)
    dest = tmp_path / "small"
    report = store.backup(con, dest, archive=False)
    assert report["archive"] == {"files": 0, "copied": 0, "bytes": 0, "skipped": True}
    assert (dest / "prax.db").is_file() and not (dest / "archive").exists()
    manifest = json.loads((dest / store.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["archive"]["skipped"] is True
    full = store.backup(con, dest)
    assert full["archive"]["copied"] == full["archive"]["files"] > 0
    assert (dest / "archive").is_dir()


def test_the_trained_labeller_is_copied_and_fetched_models_are_not(
    con: Any, tmp_path: Path
) -> None:
    """A model trained on this library cannot be fetched again: its runs
    go with the database, and only what changed the next time. A model
    ``prax models fetch`` gets is left out."""
    from prax.ml import fetch

    models = fetch.models_dir()
    run = models / "labeller" / "run-1"
    run.mkdir(parents=True)
    (run / "meta.json").write_text("{}", encoding="utf-8")
    (models / "labeller" / "CURRENT").write_text("run-1", encoding="utf-8")
    (models / "bge-small").mkdir()
    (models / "bge-small" / "model.onnx").write_bytes(b"fetched")
    dest = tmp_path / "copy"
    first = store.backup(con, dest, archive=False)
    assert first["trained"] == {"files": 2, "copied": 2}
    assert (dest / "models" / "labeller" / "run-1" / "meta.json").is_file()
    assert not (dest / "models" / "bge-small").exists()
    assert store.backup(con, dest, archive=False)["trained"] == {
        "files": 2,
        "copied": 0,
    }
