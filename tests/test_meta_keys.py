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
