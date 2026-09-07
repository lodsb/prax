"""Schema migrations and the ontology: both must be extensible incrementally."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from prax import config, ontology, store

# ------------------------------------------------------------- migrations


def test_fresh_db_is_at_latest_version(con: sqlite3.Connection) -> None:
    latest = store.migrations()[-1][0]
    assert store.schema_version(con) == latest >= 1


def test_init_db_is_idempotent(con: sqlite3.Connection) -> None:
    v = store.init_db(con)
    assert store.init_db(con) == v == store.schema_version(con)


def test_pre_migration_db_gets_stamped(data_dir: Path) -> None:
    """A Stage 0 database (tables present, user_version 0) upgrades in place."""
    con = store.connect()
    baseline = store.migrations()[0][1].read_text(encoding="utf-8")
    con.executescript(baseline)
    assert store.schema_version(con) == 0
    # a Stage 0 row, written the way Stage 0 wrote it (no chunk columns yet)
    con.execute(
        "INSERT INTO documents (id, hash, mime, title)"
        " VALUES (1, 'h', 'text/plain', 'old')"
    )
    con.execute(
        "INSERT INTO chunks (doc_id, seq, text) VALUES (1, 0, 'pre-migration row')"
    )
    con.commit()
    assert store.init_db(con) >= 2
    hit = store.search(con, "pre-migration")[0]
    assert hit["title"] == "old" and hit["kind"] is None  # legacy row until rechunked
    con.close()


def test_new_migration_applies_once(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    for n, path in store.migrations():
        (mig / path.name).write_bytes(path.read_bytes())
    latest = store.migrations()[-1][0]
    (mig / f"{latest + 1:04}_add_notes.sql").write_text(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);"
        "INSERT INTO notes (body) VALUES ('once');"
    )
    monkeypatch.setattr(config, "MIGRATIONS_DIR", mig)
    con = store.connect()
    assert store.init_db(con) == latest + 1
    assert store.init_db(con) == latest + 1
    assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 1
    con.close()


def test_failed_migration_rolls_back(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    for _, path in store.migrations():
        (mig / path.name).write_bytes(path.read_bytes())
    latest = store.migrations()[-1][0]
    (mig / f"{latest + 1:04}_bad.sql").write_text(
        "CREATE TABLE half (id INTEGER); THIS IS NOT SQL;"
    )
    monkeypatch.setattr(config, "MIGRATIONS_DIR", mig)
    con = store.connect()
    with pytest.raises(sqlite3.OperationalError):
        store.init_db(con)
    assert store.schema_version(con) == latest
    assert (
        con.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'half'"
        ).fetchone()[0]
        == 0
    )
    con.close()


def test_migration_numbering_must_be_contiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "0001_a.sql").write_text("")
    (mig / "0003_c.sql").write_text("")
    monkeypatch.setattr(config, "MIGRATIONS_DIR", mig)
    with pytest.raises(ValueError, match="without gaps"):
        store.migrations()


# --------------------------------------------------------------- ontology


LIST_FORM = """
version: "7"
entity_types: [paper, author]
relation_types: [authored_by]
"""

MAPPING_FORM = """
version: "8"
entity_types:
  paper: {description: doc}
  author:
relation_types:
  authored_by: {domain: [paper], range: [author]}
"""


def test_parse_accepts_list_and_mapping_forms() -> None:
    a = ontology.parse(LIST_FORM)
    assert a.version == "7"
    assert a.entity_types == {"paper", "author"}
    assert a.relations["authored_by"].domain == frozenset()
    b = ontology.parse(MAPPING_FORM)
    assert b.relations["authored_by"].domain == {"paper"}
    assert b.relations["authored_by"].range == {"author"}


def test_parse_rejects_unknown_domain_types() -> None:
    with pytest.raises(ValueError, match="unknown types"):
        ontology.parse(
            "version: 1\nentity_types: [a]\nrelation_types:\n  r: {domain: [zzz]}"
        )


def test_check_edge() -> None:
    o = ontology.parse(MAPPING_FORM)
    o.check_edge("paper", "authored_by", "author")
    with pytest.raises(ValueError, match="unknown entity type"):
        o.check_edge("paper", "authored_by", "planet")
    with pytest.raises(ValueError, match="unknown relation"):
        o.check_edge("paper", "cites", "paper")
    with pytest.raises(ValueError, match="does not accept src"):
        o.check_edge("author", "authored_by", "author")


def test_repo_ontology_loads_and_link_validates(con: sqlite3.Connection) -> None:
    o = ontology.current()
    assert "authored_by" in o.relations and "paper" in o.entity_types
    with pytest.raises(ValueError, match="unknown entity type"):
        store.link(con, store.Edge("x", "planet", "authored_by", "y", "author"))
    with pytest.raises(ValueError, match="does not accept"):
        store.link(con, store.Edge("x", "author", "authored_by", "y", "paper"))
    eid = store.link(con, store.Edge("p", "paper", "authored_by", "a", "author"))
    row = con.execute(
        "SELECT ontology_version FROM edges WHERE id = ?", (eid,)
    ).fetchone()
    assert row[0] == o.version


def test_bumped_ontology_is_picked_up_and_stamps_new_edges(
    con: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.link(con, store.Edge("p", "paper", "authored_by", "a", "author"))
    path = tmp_path / "ontology.yaml"
    path.write_text(
        'version: "99"\nentity_types: [paper, author, planet]\n'
        "relation_types:\n  authored_by: {domain: [paper], range: [author]}\n"
        "  orbits:\n"
    )
    monkeypatch.setenv("PRAX_ONTOLOGY", str(path))
    assert ontology.current().version == "99"
    store.link(con, store.Edge("earth", "planet", "orbits", "sun", "planet"))
    versions = [
        r[0] for r in con.execute("SELECT ontology_version FROM edges ORDER BY id")
    ]
    assert versions == ["0", "99"]  # old edges keep the version they were written under
