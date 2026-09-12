"""The door as the only writer: a connection per request thread, and the
drop folder consumed by the door itself."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import config, inbox, store


def test_thread_connections_are_per_thread(con: store.sqlite3.Connection) -> None:
    seen: dict[str, int] = {}

    def grab(name: str) -> None:
        c = store.thread_connection()
        assert c is store.thread_connection()  # stable within the thread
        seen[name] = id(c)

    threads = [threading.Thread(target=grab, args=(f"t{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(seen.values())) == 3


@pytest.fixture()
def scanning_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_INBOX_SCAN", "0.2")
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_door_consumes_its_drop_folder(scanning_client: TestClient) -> None:
    folder = inbox.inbox_dir()
    (folder / "research").mkdir(parents=True, exist_ok=True)
    f = folder / "research" / "dropped.txt"
    f.write_text("a note dropped beside the door " * 20, encoding="utf-8")
    old = time.time() - 10
    os.utime(f, (old, old))
    for _ in range(50):
        time.sleep(0.1)
        if not f.exists():
            break
    assert not f.exists()
    recent = scanning_client.get("/inbox").json()["recent"]
    assert (
        recent
        and recent[0]["title"] == "dropped"
        and recent[0]["domains"] == ["research"]
    )
    assert recent[0]["source"] == "inbox"


def test_parallel_reads_do_not_collide(scanning_client: TestClient) -> None:
    scanning_client.post("/ingest", json={"text": "alpha beta " * 30, "title": "P"})
    errors: list[str] = []

    def hit() -> None:
        for _ in range(10):
            r = scanning_client.get("/changes")
            if r.status_code != 200:
                errors.append(str(r.status_code))
            r = scanning_client.get("/search", params={"q": "alpha"})
            if r.status_code != 200:
                errors.append(str(r.status_code))

    threads = [threading.Thread(target=hit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert Path(config.db_path()).exists()
