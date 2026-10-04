"""Syncing a project (``POST /projects/sync``, ``prax.capture.projects``):
the plan as data, a dry run that writes nothing, documents keyed by the
remote and the path, the manifest in prax, and the project page."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import store

REMOTE = "example.org/someone/synth"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_EMBED", "hash")
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _file(path: str, text: str) -> dict[str, Any]:
    raw = text.encode("utf-8")
    return {
        "path": path,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "modified": "2026-10-03T12:00:00Z",
        "text": text,
    }


def _sync(client: TestClient, files: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    body = {"remote": REMOTE, "prefix": "fw", "root_name": "fw", "files": files, **kw}
    r = client.post("/projects/sync", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _actions(out: dict[str, Any]) -> dict[str, str]:
    return {p["path"]: p["action"] for p in out["plan"]}


NOTES = "# Firmware notes\n\nThe voice board talks I2C.\n"
DESIGN = "# Design\n\nTwo oscillators and a filter.\n"


def test_a_dry_run_is_the_plan_and_writes_nothing(client: TestClient) -> None:
    con = client.app.state.con
    before = store.list_documents(con, limit=5)["total"]
    out = _sync(
        client,
        [
            _file("notes.md", NOTES),
            _file("main.c", "int main;"),
            _file("docs/d.md", DESIGN),
        ],
        name="synth-fw",
        skipped={"a build or vendored folder": {"count": 90, "examples": ["_deps/x"]}},
    )
    assert out["dry_run"] is True and out["name"] == "synth-fw"
    assert _actions(out) == {"notes.md": "add", "main.c": "skip", "docs/d.md": "add"}
    assert out["counts"] == {"add": 2, "skip": 91, "gone": 0}
    assert store.list_documents(con, limit=5)["total"] == before
    assert store.project_named(con, "synth-fw") is None


def test_a_sync_keys_by_remote_and_path_and_keeps_the_manifest(
    client: TestClient,
) -> None:
    con = client.app.state.con
    files = [_file("notes.md", NOTES), _file("docs/d.md", DESIGN)]
    out = _sync(client, files, name="synth-fw", domains=["research"], dry_run=False)
    assert out["counts"]["add"] == 2 and out["auto_sync"] is False
    doc = next(p["doc_id"] for p in out["plan"] if p["path"] == "notes.md")
    meta = store.get_meta(con, doc)
    assert meta["project"]["key"] == f"{REMOTE}:fw/notes.md"
    assert meta["project"]["path"] == "fw/notes.md"
    assert "project:synth-fw" in meta["tags"]
    assert store.get_document(con, doc)["title"] == "Firmware notes (synth-fw)"
    # the manifest: kept in prax, found by the working copy
    got = client.get("/projects", params={"remote": REMOTE, "prefix": "fw"}).json()
    assert got["project"]["name"] == "synth-fw"
    assert got["project"]["settings"]["domains"] == ["research"]
    assert got["project"]["last"]["add"] == 2
    # the page, and its members
    page = store.get_page(con, "project-synth-fw")
    assert page is not None and "project:synth-fw" in page["text"]
    ctx = client.get(f"/doc/{page['doc_id']}/context").json()
    assert {m["title"] for m in ctx["members"]} >= {"Firmware notes (synth-fw)"}
    # synced notes are members, not papers queued for the paid pass
    assert "promote" not in meta

    # again: unchanged; the manifest's settings hold without being resent
    again = _sync(client, files, dry_run=False)
    assert again["name"] == "synth-fw" and set(_actions(again).values()) == {
        "unchanged"
    }
    # a rewritten note replaces itself; a renamed one is moved, not added
    files2 = [
        _file("notes.md", NOTES + "\nAnd SPI.\n"),
        _file("docs/design.md", DESIGN),
    ]
    out2 = _sync(client, files2, dry_run=False)
    assert _actions(out2) == {"notes.md": "refresh", "docs/design.md": "moved"}
    assert out2["counts"]["gone"] == 0
    new = next(p["doc_id"] for p in out2["plan"] if p["path"] == "notes.md")
    assert new != doc and store.get_meta(con, doc)["retired"]["of"] == new
    moved = next(p for p in out2["plan"] if p["path"] == "docs/design.md")
    assert moved["was"] == "fw/docs/d.md"
    assert (
        store.get_meta(con, moved["doc_id"])["project"]["path"] == "fw/docs/design.md"
    )
    # a file that no longer comes is gone: reported, never retired by a sync
    out3 = _sync(client, files2[:1], dry_run=False)
    assert out3["gone"] == [{"path": "fw/docs/design.md", "doc_id": moved["doc_id"]}]
    assert "retired" not in store.get_meta(con, moved["doc_id"])


def test_a_document_the_importer_wrote_is_adopted(client: TestClient) -> None:
    con = client.app.state.con
    old = store.ingest_text(
        con,
        NOTES,
        title="Firmware notes (fw)",
        meta={
            "source": "project",
            "project": {
                "key": "fw/notes.md",
                "name": "fw",
                "path": "notes.md",
                "version": hashlib.sha256(NOTES.encode()).hexdigest()[:16],
            },
            "tags": ["project:fw"],
        },
    )["doc_id"]
    out = _sync(client, [_file("notes.md", NOTES)], dry_run=False)
    assert out["name"] == "fw"  # the folder's name, as the importer used
    assert _actions(out) == {"notes.md": "unchanged"}
    assert store.get_meta(con, old)["project"]["key"] == f"{REMOTE}:fw/notes.md"


def test_one_working_copy_is_one_project(client: TestClient) -> None:
    _sync(client, [_file("notes.md", NOTES)], name="a", dry_run=False)
    r = client.post(
        "/projects/sync",
        json={
            "remote": REMOTE,
            "prefix": "fw",
            "name": "b",
            "files": [],
            "dry_run": False,
        },
    )
    assert r.status_code == 400 and "project 'a'" in r.json()["detail"]
    # auto_sync only when asked, and kept when not resent
    on = _sync(client, [_file("notes.md", NOTES)], auto_sync=True, dry_run=False)
    assert on["auto_sync"] is True
    kept = _sync(client, [_file("notes.md", NOTES)], dry_run=False)
    assert kept["auto_sync"] is True


def test_refusals_come_before_anything_is_written(client: TestClient) -> None:
    con = client.app.state.con
    r = client.post(
        "/projects/sync",
        json={
            "remote": REMOTE,
            "name": "x",
            "domains": ["nonesuch"],
            "files": [_file("a.md", NOTES)],
            "dry_run": False,
        },
    )
    assert r.status_code == 400 and "nonesuch" in r.json()["detail"]
    bare = _file("a.md", DESIGN)
    del bare["text"]
    r = client.post(
        "/projects/sync",
        json={"remote": REMOTE, "name": "x", "files": [bare], "dry_run": False},
    )
    assert r.status_code == 400 and "no text" in r.json()["detail"]
    assert store.project_named(con, "x") is None


def test_the_notes_links_to_each_other_are_edges_kept_in_step(
    client: TestClient,
) -> None:
    """A Markdown link, a backticked path and a bare mention that match
    another document of the project are ``links_to`` edges (AL step 4);
    a path that matches nothing is counted, not kept, and found later."""
    notes = (
        "# Firmware notes\n\nThe voice is in [the design](docs/d.md); the"
        " numbers wait in `docs/numbers.md`.\n"
    )
    design = "# Design\n\nAs fw/notes.md says, two oscillators.\n"
    out = _sync(
        client,
        [_file("notes.md", notes), _file("docs/d.md", design)],
        name="synth-fw",
        dry_run=False,
    )
    assert out["links"] == {"added": 2, "kept": 0, "ended": 0, "unmatched": 1}
    edges = client.get("/traverse", params={"entity": "Design (synth-fw)"}).json()[
        "edges"
    ]
    said = {(e["src"], e["rel"], e["dst"]): e for e in edges}
    there = said[("Firmware notes (synth-fw)", "links_to", "Design (synth-fw)")]
    assert (
        there["evidence"] == "[the design](docs/d.md)" and there["producer"] == "sync"
    )
    back = said[("Design (synth-fw)", "links_to", "Firmware notes (synth-fw)")]
    assert back["evidence"] == "fw/notes.md"
    # the missing file arrives: the next sync finds what the note named
    numbers = "# Numbers\n\nTwelve.\n"
    out2 = _sync(
        client,
        [
            _file("notes.md", notes),
            _file("docs/d.md", design),
            _file("docs/numbers.md", numbers),
        ],
        dry_run=False,
    )
    assert out2["links"] == {"added": 1, "kept": 2, "ended": 0, "unmatched": 0}
    # a link taken out of the text is ended, not deleted
    plain = "# Firmware notes\n\nNo links any more.\n"
    out3 = _sync(
        client,
        [
            _file("notes.md", plain),
            _file("docs/d.md", design),
            _file("docs/numbers.md", numbers),
        ],
        dry_run=False,
    )
    assert out3["links"]["ended"] == 2 and out3["links"]["kept"] == 1
    left = client.get("/traverse", params={"entity": "Numbers (synth-fw)"}).json()[
        "edges"
    ]
    assert not [e for e in left if e["rel"] == "links_to"]
