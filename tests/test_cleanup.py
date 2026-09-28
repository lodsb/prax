"""Stage X, cleaning up in bulk: a rule picks a set, the preview says what
it would take, a run retires it in one go and a restore brings back the
documents with the facts they had."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.text import clutter
from prax.wall import auth


def test_the_rules_read_a_path_and_a_file_name() -> None:
    assert clutter.kinds("/nas/backup/Windows/System32/license.rtf") == {
        "system",
        "names",
    }
    assert clutter.kinds("/nas/home/Apps/Synth.app/Contents/Credits.rtf") == {
        "system",
        "names",
    }
    assert clutter.kinds("/nas/sc/Help/UGens/SinOsc.help.rtf") == {"help"}
    assert clutter.kinds("/nas/sc/README.md") == {"names"}
    # a title with "history" in it is a book, and only the file name counts
    assert clutter.kinds("", "Financialism: a (very) brief history") == set()
    assert clutter.kinds("/nas/papers/licensing_of_music.pdf") == set()


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _doc(con: sqlite3.Connection, path: str, text: str, title: str) -> int:
    doc = int(
        store.register(
            con,
            f"{path}\n{text}".encode(),
            mime="text/plain",
            title=title,
            meta={"origin": {"host": "nas", "path": path}},
        )["doc_id"]
    )
    store.index_text(con, doc, text)
    return doc


def _retired(con: sqlite3.Connection, doc: int) -> dict:
    meta = json.loads(
        con.execute("SELECT meta FROM documents WHERE id = ?", (doc,)).fetchone()[0]
    )
    return meta.get("retired") or {}


def test_a_set_is_shown_retired_and_restored_with_its_facts(
    client: TestClient,
) -> None:
    con = client.app.state.con
    paper = _doc(con, "/nas/papers/wavelets.pdf", "Wavelets decompose. " * 30, "P")
    eula = _doc(
        con,
        "/nas/backup/Windows/System32/license.rtf",
        "The licence terms of an operating system. " * 20,
        "Licence",
    )
    store.link(
        con,
        store.Edge(
            "Licence", "document", "published_by", "Some Vendor", "organization"
        ),
        source_doc=eula,
        producer="test",
    )
    rules = client.get("/cleanup").json()
    assert "system" in rules["rules"] and rules["runs"] == []
    seen = client.get("/cleanup/system").json()
    assert seen["total"] == 1 and seen["edges"] == 1
    assert seen["items"][0]["id"] == eula
    assert client.get("/cleanup/nothing").status_code == 400
    assert client.get("/cleanup/folder").status_code == 400  # needs a folder
    # retired in one go: out of the index, its facts ended, the paper untouched
    done = client.post("/cleanup/system", json={}).json()
    assert done["retired"] == 1 and done["edges_ended"] == 1
    assert _retired(con, eula)["run"] == done["run"] and not _retired(con, paper)
    assert (
        con.execute("SELECT count(*) FROM chunks WHERE doc_id = ?", (eula,)).fetchone()[
            0
        ]
        == 0
    )
    assert (
        con.execute(
            "SELECT count(*) FROM edges WHERE source_doc = ? AND valid_to IS NULL",
            (eula,),
        ).fetchone()[0]
        == 0
    )
    assert client.get("/cleanup/system").json()["total"] == 0
    runs = client.get("/cleanup").json()["runs"]
    assert [r["run"] for r in runs] == [done["run"]] and runs[0]["documents"] == 1
    # restored in one go: the chunks back, and the very facts it ended
    back = client.post("/cleanup-restore", json={"run": done["run"]}).json()
    assert back == {"run": done["run"], "restored": 1, "edges_reopened": 1}
    assert not _retired(con, eula)
    assert (
        con.execute("SELECT count(*) FROM chunks WHERE doc_id = ?", (eula,)).fetchone()[
            0
        ]
        > 0
    )
    assert (
        con.execute(
            "SELECT count(*) FROM edges WHERE source_doc = ? AND valid_to IS NULL",
            (eula,),
        ).fetchone()[0]
        == 1
    )
    assert client.post("/cleanup-restore", json={"run": "nope"}).status_code == 404
    # an administrator's page: a restricted token may not reach it
    for method, path in (
        ("GET", "/cleanup"),
        ("GET", "/cleanup/system"),
        ("POST", "/cleanup/system"),
        ("POST", "/cleanup-restore"),
    ):
        assert not any(
            m == method and pattern.fullmatch(path)
            for m, pattern in auth.RESTRICTED_ROUTES
        )


def test_the_same_text_twice_is_kept_as_one(client: TestClient) -> None:
    con = client.app.state.con
    text = "One paper, saved twice under two names. " * 20
    first = _doc(con, "/nas/a/paper.pdf", text, "Paper")
    second = _doc(con, "/nas/b/paper (1).pdf", text, "Paper copy")
    store.link(
        con,
        store.Edge("Paper copy", "paper", "uses", "wavelet transform", "method"),
        source_doc=second,
        producer="test",
    )
    seen = client.get("/cleanup/same-text").json()
    assert seen["total"] == 1 and seen["items"][0]["duplicate_of"] == first
    done = client.post("/cleanup/same-text", json={}).json()
    assert done["retired"] == 1 and done["edges_moved"] == 1
    assert _retired(con, second)["of"] == first
    # what the copy held is the first one's now
    assert (
        con.execute(
            "SELECT count(*) FROM edges WHERE source_doc = ? AND valid_to IS NULL",
            (first,),
        ).fetchone()[0]
        == 1
    )


def test_a_folder_of_ones_own(client: TestClient) -> None:
    con = client.app.state.con
    _doc(con, "/nas/old_users/Users/me/notes.txt", "Old notes. " * 30, "Notes")
    _doc(con, "/nas/papers/new.pdf", "A new paper. " * 30, "New")
    seen = client.get("/cleanup/folder", params={"folder": "old_users/Users"}).json()
    assert seen["total"] == 1 and seen["folder"] == "old_users/Users"
