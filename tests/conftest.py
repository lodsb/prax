"""Shared fixtures. Every test runs against a temporary data directory."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import store, work


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
    # the door's leases and its record of who asked are process globals too,
    # keyed by (step, document id): a hand-out in one test's database would
    # otherwise hide the next test's document, which has the same id
    work._leases.clear()
    work._asked.clear()
    # and where the embed hand-out is in its walk, and what it has not saved
    from prax.steps import embed

    embed.forget()
    return d


@pytest.fixture()
def con(data_dir: Path) -> Iterator[store.sqlite3.Connection]:
    c = store.connect()
    store.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def client(data_dir: Path) -> Iterator[TestClient]:
    """The door as a test client, on this test's data directory. A file
    that needs the door otherwise (a token, the stub answerer) defines its
    own ``client``, which takes precedence."""
    from prax.api import app

    with TestClient(app) as c:
        yield c
