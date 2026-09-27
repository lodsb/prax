"""A tree on another machine sent to the door, only what it lacks
(``clients/send/prax_send.py``; niggles.txt: "walk a tree, ask prax via
network if a file exists, then send the file over")."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from prax import config, store

SENDER = config.REPO_ROOT / "clients" / "send" / "prax_send.py"


def _sender() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prax_send", SENDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_the_door_says_which_hashes_it_holds(client: TestClient) -> None:
    got = client.post(
        "/ingest/file", files={"file": ("a.txt", b"alpha " * 50, "text/plain")}
    ).json()
    held = hashlib.sha256(b"alpha " * 50).hexdigest()
    other = hashlib.sha256(b"beta").hexdigest()
    answer = client.post("/known", json={"hashes": [held, other, held.upper()]})
    assert answer.json() == {"known": [held]}
    assert client.post("/known", json={"hashes": ["nope"]}).status_code == 400
    many = [f"{i:064x}" for i in range(store.KNOWN_BATCH + 1)]
    assert client.post("/known", json={"hashes": many}).status_code == 413
    assert got["created"] is True


def test_a_sent_file_keeps_where_it_came_from(client: TestClient) -> None:
    origin = {"host": "nas", "path": "/volume1/papers/a.txt", "junk": "x"}
    got = client.post(
        "/ingest/file",
        files={"file": ("a.txt", b"alpha " * 50, "text/plain")},
        data={"origin": json.dumps(origin), "by": "send"},
    ).json()
    meta = store.get_meta(client.app.state.con, got["doc_id"])
    assert meta["origin"] == {"host": "nas", "path": "/volume1/papers/a.txt"}
    bad = client.post(
        "/ingest/file",
        files={"file": ("b.txt", b"beta " * 50, "text/plain")},
        data={"origin": "{not json"},
    )
    assert bad.status_code == 400


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def door(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    """The door on a port of its own, with a token, as a NAS would see it."""
    import uvicorn

    monkeypatch.setenv("PRAX_TOKEN", "s3cret")
    from prax.api import app

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_the_sender_sends_what_the_door_lacks_and_nothing_twice(
    door: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = tmp_path / "share"
    (tree / "papers").mkdir(parents=True)
    (tree / "papers" / "one.txt").write_text("the first paper " * 40)
    (tree / "papers" / "two.md").write_text("# Two\n\n" + "the second " * 40)
    (tree / "papers" / "copy.txt").write_text("the first paper " * 40)  # same bytes
    (tree / "papers" / "song.mp3").write_bytes(b"ID3" + b"\0" * 64)  # not sent
    (tree / ".hidden.txt").write_text("not sent " * 40)
    (tree / "@eaDir").mkdir()
    (tree / "@eaDir" / "thumb.txt").write_text("the NAS's own " * 40)
    token = tmp_path / "token"
    token.write_text("s3cret\n")
    state = tmp_path / "state.json"
    sender = _sender()
    args = [str(tree), "--door", door, "--token-file", str(token)]
    args += ["--state", str(state), "--host", "nas", "--tags", "from:nas"]

    assert sender.run([*args, "--dry-run", "--quiet"]) == 0
    assert "2 to send" in capsys.readouterr().out

    assert sender.run([*args, "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "3 looked at, 1 already there, 2 sent" in out
    con = store.connect()
    rows = con.execute("SELECT title, meta FROM documents ORDER BY id").fetchall()
    assert [r["title"] for r in rows] == ["copy.txt", "two.md"]
    first = json.loads(rows[0]["meta"])
    assert first["origin"]["host"] == "nas"
    assert first["origin"]["path"].endswith("copy.txt")
    assert "from:nas" in first.get("tags", [])
    # a second run hashes nothing again and sends nothing
    assert json.loads(state.read_text())["files"]
    assert sender.run([*args, "--quiet"]) == 0
    assert "3 looked at, 3 already there, 0 sent" in capsys.readouterr().out
    # a wrong token stops at once, and says why
    token.write_text("wrong")
    (tree / "papers" / "three.txt").write_text("a third " * 40)
    assert sender.run([*args, "--quiet"]) == 2
    assert "HTTP 401" in capsys.readouterr().err
