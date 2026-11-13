"""Stage X, cleaning up in bulk: a rule picks a set, the preview says what
it would take, a run retires it in one go and a restore brings back the
documents with the facts they had."""

from __future__ import annotations

import json
import sqlite3

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
    ended = con.execute(
        "SELECT id, valid_to FROM edges WHERE source_doc = ?", (eula,)
    ).fetchone()
    back = client.post("/cleanup-restore", json={"run": done["run"]}).json()
    assert back == {"run": done["run"], "restored": 1, "edges_restated": 1}
    assert not _retired(con, eula)
    # stated again as a new edge: the ended one keeps its ending, so what
    # prax held while the set was retired stays history (invariant 8)
    assert (
        con.execute("SELECT valid_to FROM edges WHERE id = ?", (ended[0],)).fetchone()[
            0
        ]
        == ended[1]
    )
    assert [
        r[0]
        for r in con.execute(
            "SELECT edge_id FROM edge_endings WHERE run = ?", (done["run"],)
        )
    ] == [ended[0]]
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


def test_a_clean_up_from_before_the_endings_is_restored(
    con: sqlite3.Connection,
) -> None:
    # until 2026-11-07 a clean-up kept the edges it ended in its meta and
    # reopened them in place; one made then is restored the new way
    eula = _doc(con, "/nas/Windows/System32/license.rtf", "Terms. " * 60, "Licence")
    store.link(
        con,
        store.Edge("Licence", "document", "published_by", "Vendor", "organization"),
        source_doc=eula,
    )
    edge = con.execute("SELECT id FROM edges WHERE source_doc = ?", (eula,)).fetchone()[
        0
    ]
    store.retire_document(con, eula, reason="clean-up")
    meta = json.loads(
        con.execute("SELECT meta FROM documents WHERE id = ?", (eula,)).fetchone()[0]
    )
    meta["retired"].update(run="cleanup-old", ended=[edge])
    con.execute("UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), eula))
    con.commit()
    back = store.restore_set(con, "cleanup-old")
    assert back["restored"] == 1 and back["edges_restated"] == 1
    assert (
        con.execute(
            "SELECT count(*) FROM edges WHERE source_doc = ? AND valid_to IS NULL",
            (eula,),
        ).fetchone()[0]
        == 1
    )
    assert con.execute("SELECT valid_to FROM edges WHERE id = ?", (edge,)).fetchone()[0]


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


def test_one_document_of_an_old_clean_up_is_taken_back_with_its_facts(
    con: sqlite3.Connection,
) -> None:
    """Finding 4 of the 2026-11-11 quality review: taking back one document
    of a clean-up from before 2026-11-07 states its ended facts again; the
    list in its meta was dropped and nothing came back."""
    eula = _doc(con, "/nas/Windows/System32/eula.rtf", "Terms. " * 60, "Eula")
    store.link(
        con,
        store.Edge("Eula", "document", "published_by", "Vendor", "organization"),
        source_doc=eula,
    )
    edge = con.execute("SELECT id FROM edges WHERE source_doc = ?", (eula,)).fetchone()[
        0
    ]
    store.retire_document(con, eula, reason="clean-up")
    meta = json.loads(
        con.execute("SELECT meta FROM documents WHERE id = ?", (eula,)).fetchone()[0]
    )
    meta["retired"].update(run="cleanup-older", ended=[edge])
    con.execute("UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), eula))
    con.commit()
    assert store.unretire_document(con, eula)["edges"] == 1
    assert (
        con.execute(
            "SELECT count(*) FROM edges WHERE source_doc = ? AND valid_to IS NULL",
            (eula,),
        ).fetchone()[0]
        == 1
    )
