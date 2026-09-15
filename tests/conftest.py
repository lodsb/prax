"""Shared fixtures. Every test runs against a temporary data directory."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from prax import store


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point PRAX_DATA_DIR at tmp_path before any store, API or MCP call.

    Every other ``PRAX_*`` variable is cleared first: a developer's own
    machine has a token, a door address and a model choice in its
    environment, and a test that inherited them would run against
    another host's settings (a door asking for a token, a step pointed
    at a model server). A test that wants one sets it itself.
    """
    for name in [k for k in os.environ if k.startswith("PRAX_")]:
        monkeypatch.delenv(name, raising=False)
    d = tmp_path / "data"
    monkeypatch.setenv("PRAX_DATA_DIR", str(d))
    # the process-wide cache of opened vector indexes is keyed by path: views
    # of an earlier test's files must not linger into this one
    for idx in list(store._indexes.values()):
        with contextlib.suppress(Exception):
            idx.close()
    store._indexes.clear()
    return d


@pytest.fixture()
def con(data_dir: Path) -> Iterator[store.sqlite3.Connection]:
    c = store.connect()
    store.init_db(c)
    yield c
    c.close()
