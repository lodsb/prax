"""What a document's ``meta`` may hold: ``store.DocumentMeta`` is the
catalogue, ``docs/meta.md`` its reference, and a key the code writes that
neither names fails (``store.check_meta``, strict in every test)."""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import pytest

from prax import store

ROOT = Path(__file__).resolve().parents[1]
DECLARED = set(store.DocumentMeta.__annotations__)


def test_the_reference_names_the_declared_keys() -> None:
    doc = (ROOT / "docs" / "meta.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"(?m)^\| `([a-z_]+)` \|", doc))
    assert listed == DECLARED, (
        f"in docs/meta.md only: {sorted(listed - DECLARED)};"
        f" declared only: {sorted(DECLARED - listed)}"
    )


def test_a_key_written_in_sql_is_declared() -> None:
    """``json_set(meta, '$.key', …)`` passes no Python check: its keys are
    read off the source instead."""
    written = set()
    for f in (ROOT / "src" / "prax").rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for call in re.finditer(
            r"json_set\(\s*COALESCE\(meta,[^)]*\),(.{0,200})", text, re.DOTALL
        ):
            written |= set(re.findall(r"'\$\.([a-z_]+)'", call.group(1)))
    assert written, "the scan found no json_set at all: it is broken"
    assert written <= DECLARED, sorted(written - DECLARED)


def test_an_undeclared_key_is_refused_in_a_test_and_logged_on_a_host(
    con: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    doc = store.ingest_text(con, "a text " * 20)["doc_id"]
    meta = store.get_meta(con, doc)
    meta["colour"] = "teal"
    with pytest.raises(ValueError, match="colour"):
        store.set_meta(con, doc, meta)
    with pytest.raises(ValueError, match="flavour"):
        store.register(
            con, b"bytes", mime="text/plain", meta={"source": "t", "flavour": "x"}
        )
    monkeypatch.setattr(store.checks, "strict_meta", False)
    with caplog.at_level(logging.WARNING, logger="prax.store"):
        store.set_meta(con, doc, meta)
    assert store.get_meta(con, doc)["colour"] == "teal"  # a host keeps it
    assert "'colour' is undeclared" in caplog.text
    assert store.undeclared_meta({"source": "x", "colour": 1}) == ["colour"]


def test_update_meta_changes_a_document_in_one_write(con: sqlite3.Connection) -> None:
    """``store.update_meta`` reads, changes and writes under the one lock,
    so a key another writer put in between is never lost (structure review
    findings 19, 26); a change that changes nothing writes nothing."""
    doc = store.ingest_text(con, "a text " * 20, meta={"tags": ["a"]})["doc_id"]

    def tag(meta: store.DocumentMeta) -> None:
        meta["tags"] = [*meta.get("tags", []), "b"]

    got = store.update_meta(con, doc, tag, keys={"previous_capture": 7})
    assert got["tags"] == ["a", "b"] and got["previous_capture"] == 7
    assert store.get_meta(con, doc) == got
    replaced = store.update_meta(con, doc, lambda m: {"tags": m["tags"][:1]})
    assert store.get_meta(con, doc) == replaced == {"tags": ["a"]}

    before = con.total_changes
    store.update_meta(con, doc, lambda m: None, keys={"tags": ["a"]})
    assert con.total_changes == before
    with pytest.raises(ValueError, match="colour"):
        store.update_meta(con, doc, keys={"colour": "teal"})
    assert "colour" not in store.get_meta(con, doc)
    with pytest.raises(store.NotFound):
        store.update_meta(con, 10_000, keys={"tags": []})


def test_a_meta_write_sets_only_the_columns_it_may(con: sqlite3.Connection) -> None:
    from prax.store.documents import meta as part

    doc = store.ingest_text(con, "a text " * 20)["doc_id"]
    part._put_meta(con, doc, {"source": "t"}, sensitivity="personal")
    row = con.execute(
        "SELECT sensitivity, meta FROM documents WHERE id = ?", (doc,)
    ).fetchone()
    assert row["sensitivity"] == "personal"
    assert store.meta_of(row["meta"]) == {"source": "t"}
    with pytest.raises(ValueError, match="hash"):
        part._put_meta(con, doc, {}, hash="x")
