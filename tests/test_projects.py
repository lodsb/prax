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


def test_what_a_note_says_of_itself_is_its_status_and_an_edge(
    client: TestClient,
) -> None:
    """ "Superseded 2026-10-02 by the new plan" near a note's top is its
    status (by sync) and a supersedes edge from the new plan, dated in the
    world; when the line goes, so do both (AL step 5)."""
    con = client.app.state.con
    old = (
        "# Plan v1\n\n> **Status:** superseded 2026-10-02 by"
        " [the new plan](plan-v2.md)\n\nThe old numbers.\n"
    )
    new = "# Plan v2\n\nThe numbers that hold.\n"
    out = _sync(
        client,
        [_file("docs/plan-v1.md", old), _file("docs/plan-v2.md", new)],
        name="synth-fw",
        dry_run=False,
    )
    assert out["status"] == {"added": 1, "kept": 0, "ended": 0, "stale": 1}
    v1 = next(p["doc_id"] for p in out["plan"] if p["path"] == "docs/plan-v1.md")
    said = store.get_meta(con, v1)["status"]
    assert (said["state"], said["since"], said["by"]) == (
        "superseded",
        "2026-10-02",
        "sync",
    )
    edges = client.get("/traverse", params={"entity": f"doc:{v1}"}).json()["edges"]
    sup = next(e for e in edges if e["rel"] == "supersedes")
    assert (sup["src"], sup["dst"]) == ("Plan v2 (synth-fw)", "Plan v1 (synth-fw)")
    assert sup["world_from"] == "2026-10-02" and sup["source_doc"] == v1
    # the line is taken out: the status and the edge go with it
    plain = "# Plan v1\n\nThe old numbers.\n"
    out2 = _sync(
        client,
        [_file("docs/plan-v1.md", plain), _file("docs/plan-v2.md", new)],
        dry_run=False,
    )
    assert out2["status"]["ended"] == 1 and out2["status"]["stale"] == 0
    v1b = next(p["doc_id"] for p in out2["plan"] if p["path"] == "docs/plan-v1.md")
    assert "status" not in store.get_meta(con, v1b)


def test_a_write_may_say_personal(client: TestClient) -> None:
    """A colleague's notes can live in prax behind the wall (AL step 7):
    ingest, write_page and the sync take ``sensitivity: personal``; any
    other value on a write is refused."""
    con = client.app.state.con
    r = client.post(
        "/ingest",
        json={
            "text": "a review of a colleague's code " * 10,
            "sensitivity": "personal",
        },
    )
    assert r.status_code == 200
    assert store.get_document(con, r.json()["doc_id"])["sensitivity"] == "personal"
    bad = client.post("/ingest", json={"text": "x " * 50, "sensitivity": "open"})
    assert bad.status_code == 400
    page = client.put(
        "/page/review-notes",
        json={
            "text": "# Review\n\nNotes.\n",
            "author": "agent",
            "sensitivity": "personal",
        },
    )
    assert page.status_code == 200
    assert store.get_document(con, page.json()["doc_id"])["sensitivity"] == "personal"
    out = _sync(
        client,
        [_file("notes.md", NOTES)],
        name="colleague",
        sensitivity="personal",
        dry_run=False,
    )
    assert out["settings"]["sensitivity"] == "personal"
    doc = out["plan"][0]["doc_id"]
    assert store.get_document(con, doc)["sensitivity"] == "personal"
    # kept with the manifest: the next sync needs not say it again
    out2 = _sync(
        client, [_file("notes.md", NOTES), _file("b.md", DESIGN)], dry_run=False
    )
    added = next(p["doc_id"] for p in out2["plan"] if p["path"] == "b.md")
    assert store.get_document(con, added)["sensitivity"] == "personal"


def test_a_link_to_a_note_two_syncs_old_lands_on_the_current_one(
    client: TestClient,
) -> None:
    con = client.app.state.con
    ids = []
    for extra in ("", "\nAnd SPI.\n", "\nAnd SPI and UART.\n"):
        out = _sync(client, [_file("notes.md", NOTES + extra)], dry_run=False)
        ids.append(next(p["doc_id"] for p in out["plan"] if p["path"] == "notes.md"))
    first, second, third = ids
    # each sync retired the one before into the next: the chain ends at the third
    assert store.get_meta(con, first)["retired"]["of"] == second
    got = client.get(f"/get/{first}", params={"brief": True, "max_chars": 0}).json()
    assert got["current"] == {
        "doc_id": third,
        "title": "Firmware notes (fw)",
        "hops": 2,
    }
    assert "current" not in client.get(f"/get/{third}", params={"max_chars": 0}).json()

    # the chain stops before what the viewer may not see
    store.set_sensitivity(con, third, "personal")
    token = store.VIEWER.set(store.Viewer("guest"))
    try:
        assert store.current_of(con, first) == {
            "doc_id": second,
            "title": "Firmware notes (fw)",
            "hops": 1,
        }
    finally:
        store.VIEWER.reset(token)


def test_a_loop_of_replacements_ends(client: TestClient) -> None:
    con = client.app.state.con
    a = int(store.ingest_text(con, "One copy of a note. " * 5, title="A")["doc_id"])
    b = int(store.ingest_text(con, "Another copy of it. " * 5, title="B")["doc_id"])
    c = int(store.ingest_text(con, "A third copy of it. " * 5, title="C")["doc_id"])
    store.retire_document(con, a, reason="test", duplicate_of=b)
    store.retire_document(con, b, reason="test", duplicate_of=c)
    # retiring the end of the chain into its start is refused, by hand too
    with pytest.raises(ValueError):
        store.retire_document(con, c, reason="test", duplicate_of=a)
    r = client.post(f"/doc/{c}/retire", json={"reason": "t", "duplicate_of": a})
    assert r.status_code == 400
    assert store.current_of(con, a) == {"doc_id": c, "title": "C", "hops": 2}
    # a loop written before the refusal: no current copy, from anywhere on it
    meta = store.get_meta(con, c)
    meta["retired"] = {"at": store.now(), "reason": "old", "of": a, "by": "human"}
    store.set_meta(con, c, meta)
    assert store.current_of(con, a) is None
    assert store.current_of(con, b) is None
    assert store.current_of(con, 999_999) is None


def test_a_changed_note_is_read_from_its_own_text(client: TestClient) -> None:
    """A sync retires a changed note's old text: the new text is read again,
    and the old one's facts end with it rather than moving onto the new one
    (the quality review of 2026-11-11, finding 1: the new text inherited
    the old extraction stamp and summary, so a correction never reached
    the graph)."""
    from prax.graph import extraction, ontology

    con = client.app.state.con
    out = _sync(client, [_file("notes.md", NOTES)], dry_run=False)
    old = next(p["doc_id"] for p in out["plan"] if p["path"] == "notes.md")
    extraction.apply(
        con, old, extraction.Extraction(summary="The old reading."), extractor="stub"
    )
    fact = store.Edge("Voice board", "device", "uses", "I2C", "concept")
    store.link(con, fact, source_doc=old, producer="stub", run="t")
    onto = ontology.current()
    assert old not in store.select_for_extraction(
        con, ontology_version=onto.version, onto=onto
    )

    fixed = NOTES + "\nCORRECTION: the voice board talks SPI.\n"
    out = _sync(client, [_file("notes.md", fixed)], dry_run=False)
    new = next(p["doc_id"] for p in out["plan"] if p["path"] == "notes.md")
    assert new != old and store.get_meta(con, old)["retired"]["of"] == new

    meta = store.get_meta(con, new)
    assert "extraction" not in meta and meta.get("summary") != "The old reading."
    assert new in store.select_for_extraction(
        con, ontology_version=onto.version, onto=onto
    )
    live = con.execute(
        "SELECT source_doc FROM edges WHERE source_doc IN (?, ?)"
        " AND producer = 'stub' AND valid_to IS NULL",
        (old, new),
    ).fetchall()
    assert live == []
    # the old text's facts come back with it
    store.unretire_document(con, old)
    assert (
        con.execute(
            "SELECT count(*) FROM edges WHERE source_doc = ? AND producer = 'stub'"
            " AND valid_to IS NULL",
            (old,),
        ).fetchone()[0]
        >= 1
    )


JOURNAL_HEAD = "# Bring-up journal\n\nSee [the notes](notes.md).\n\n"


def _journal(*entries: str) -> str:
    return JOURNAL_HEAD + "".join(entries)


DAY1 = "## 2026-11-08\n\nThe sensor reads zero.\n\n"
DAY2 = "## 2026-11-09\n\nThe pull-up was missing; see [the notes](notes.md).\n\n"
DAY3 = "## 2026-11-10b (late)\n\nFixed, period index steady.\n\n"
DAY4 = "## 2026-11-11\n\nA fourth day.\n\n"


def _entries(out: dict[str, Any]) -> dict[str, tuple[str, int]]:
    return {
        p["entry"]: (p["action"], p.get("doc_id", 0))
        for p in out["plan"]
        if "entry" in p
    }


def test_a_journal_is_its_entries_and_a_new_entry_is_one_document(
    client: TestClient,
) -> None:
    """A journal (AU step 2) is a document for its head and one for each
    dated entry: the next day's sync adds that day, and the rest of the
    file is unchanged rather than a new document of the whole."""
    con = client.app.state.con
    files = [_file("notes.md", NOTES), _file("log.md", _journal(DAY1, DAY2, DAY3))]
    out = _sync(client, files, name="synth-fw", dry_run=False)
    assert out["counts"]["add"] == 5  # the note, the head, three entries
    got = _entries(out)
    assert sorted(got) == ["2026-11-08", "2026-11-09", "2026-11-10b (late)"]
    entry = store.get_document(con, got["2026-11-09"][1])
    assert entry["title"] == "2026-11-09 · log.md (synth-fw)"
    assert entry["text"].startswith("## 2026-11-09")
    assert store.get_meta(con, got["2026-11-09"][1])["project"]["entry"] == (
        "2026-11-09"
    )
    # an entry's link is its own; a link to the journal lands on its head
    assert out["links"]["added"] == 2

    files[1] = _file("log.md", _journal(DAY1, DAY2, DAY3, DAY4))
    out = _sync(client, files, dry_run=False)
    assert out["counts"].get("add") == 1 and out["counts"]["unchanged"] == 5
    assert _entries(out)["2026-11-11"][0] == "add"


def test_an_entry_edited_after_its_day_is_a_version(client: TestClient) -> None:
    """An entry edited later (a correction added) is a new version of a
    work: the earlier text stays live and a search folds it under the
    newest; it keeps no key, so the next sync finds the newest."""
    con = client.app.state.con
    out = _sync(
        client, [_file("log.md", _journal(DAY1, DAY2, DAY3))], name="j", dry_run=False
    )
    old = _entries(out)["2026-11-09"][1]
    fixed = DAY2 + "CORRECTION, same session: the pull-up was there.\n\n"
    out = _sync(client, [_file("log.md", _journal(DAY1, fixed, DAY3))], dry_run=False)
    action, new = _entries(out)["2026-11-09"]
    assert action == "refresh" and new != old
    assert "retired" not in store.get_meta(con, old)
    assert store.get_meta(con, old)["project"]["later"] == new
    assert store.later_versions(con, [old]) == {old: [new]}
    work = store.versions_of(con, new)
    assert work is not None and work["latest"] == new
    assert work["work"]["name"] == "2026-11-09 · log.md (j)"
    # the next sync with nothing changed changes nothing
    out = _sync(client, [_file("log.md", _journal(DAY1, fixed, DAY3))], dry_run=False)
    assert set(out["counts"]) == {"unchanged", "gone"} and out["counts"]["gone"] == 0


def test_a_note_that_becomes_a_journal_is_split(client: TestClient) -> None:
    """A file synced whole before it held entries is retired into its head
    when the sync splits it; with nothing before its first entry it is
    retired as split, and no head is made."""
    con = client.app.state.con
    out = _sync(
        client, [_file("log.md", "# Log\n\nOne day.\n")], name="a", dry_run=False
    )
    whole = out["plan"][0]["doc_id"]
    out = _sync(client, [_file("log.md", _journal(DAY1, DAY2, DAY3))], dry_run=False)
    head = next(p for p in out["plan"] if "entry" not in p)
    assert head["action"] == "refresh"
    assert store.get_meta(con, whole)["retired"]["of"] == head["doc_id"]

    out = _sync(
        client,
        [_file("other.md", "# Other\n\nOne day.\n")],
        name="b",
        prefix="b",
        dry_run=False,
    )
    whole = out["plan"][0]["doc_id"]
    out = _sync(
        client, [_file("other.md", DAY1 + DAY2 + DAY3)], prefix="b", dry_run=False
    )
    assert [p["action"] for p in out["plan"] if "entry" not in p] == ["split"]
    assert "split into its 3 entries" in store.get_meta(con, whole)["retired"]["reason"]
    out = _sync(
        client, [_file("other.md", DAY1 + DAY2 + DAY3)], prefix="b", dry_run=False
    )
    assert [p for p in out["plan"] if "entry" not in p] == []
    assert out["counts"] == {"unchanged": 3, "gone": 0}
