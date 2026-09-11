"""Shared fixtures. Every test runs against a temporary data directory."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from prax import store


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point PRAX_DATA_DIR at tmp_path before any store, API or MCP call."""
    d = tmp_path / "data"
    monkeypatch.setenv("PRAX_DATA_DIR", str(d))
    return d


@pytest.fixture()
def con(data_dir: Path) -> Iterator[store.sqlite3.Connection]:
    c = store.connect()
    store.init_db(c)
    yield c
    c.close()
