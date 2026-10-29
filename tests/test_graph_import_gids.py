"""An import of ``prax-graph/2`` (plan item AP, ``docs/global-ids.md``,
"The import"): another library's entities met by their global ids, a
likely one held for a person's answer, a file that comes back landing on
its own rows, a page found by its gid and never by an equal slug."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.graph import graphio

PAPER = "Wave digital filters. " * 40


def _theirs(con: sqlite3.Connection) -> list[str]:
    """A friend's library: a paper that uses a method, and a page."""
    doc = int(store.ingest_text(con, PAPER, title="WDF paper")["doc_id"])
    meta = store.get_meta(con, doc)
    store.set_meta(con, doc, {**meta, "tags": ["project:synth"]})
    for dst, dtype in (("wave digital filter", "method"), ("adaptor", "concept")):
        store.link(
            con,
            store.Edge("WDF paper", "paper", "uses", dst, dtype),
            source_doc=doc,
            evidence="section 2",
            producer="qwen",
            run="r1",
        )
    store.write_page(con, "project-synth", "# Synth\n\nNotes.", kind="project")
    return list(graphio.export(con, graphio.Seed(project="synth")))


def _other(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> sqlite3.Connection:
    monkeypatch.setenv("PRAX_DATA_DIR", str(tmp_path / "other"))
    other = store.connect()
    store.init_db(other)
    return other


def _ours(other: sqlite3.Connection) -> int:
    """This library already knows a "wave digital filter", from a paper of
    its own: their method's name answers to it."""
    mine = int(store.ingest_text(other, "Filters. " * 40, title="My paper")["doc_id"])
    store.link(
        other,
        store.Edge("My paper", "paper", "uses", "wave digital filter", "method"),
        source_doc=mine,
    )
    return int(
        other.execute(
            "SELECT id FROM entities WHERE name = 'wave digital filter'"
        ).fetchone()[0]
    )


def _lines(lines: list[str], kind: str) -> list[dict]:
    return [json.loads(x) for x in lines if json.loads(x)["kind"] == kind]


def test_an_export_names_every_entity_edge_and_page_by_its_gid(
    con: sqlite3.Connection,
) -> None:
    lines = _theirs(con)
    assert json.loads(lines[0])["format"] == "prax-graph/2"
    entities = {e["name"]: e for e in _lines(lines, "entity")}
    gids = dict(con.execute("SELECT name, gid FROM entities").fetchall())
    assert {n: e["gid"] for n, e in entities.items()} == {n: gids[n] for n in entities}
    for edge in _lines(lines, "edge"):
        assert edge["src_gid"] == gids[edge["src"]]
        assert edge["dst_gid"] == gids[edge["dst"]]
        assert edge["gid"] == graphio.edge_gid(edge) and edge["gid"].startswith("c:")
        assert "premised" not in edge
    (page,) = _lines(lines, "page")
    assert page["gid"] == con.execute("SELECT gid FROM pages").fetchone()[0]


def test_a_twin_folded_into_an_entity_is_its_alias(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO entities (name, type) VALUES ('WDF', 'method')")
    lines = _theirs(con)
    keep, twin = (
        con.execute("SELECT id, gid FROM entities WHERE name = ?", (n,)).fetchone()
        for n in ("wave digital filter", "WDF")
    )
    store.merge_entities(con, twin["id"], keep["id"], producer="t", run="m1")
    lines = list(graphio.export(con, graphio.Seed(project="synth")))
    (wdf,) = [e for e in _lines(lines, "entity") if e["gid"] == keep["gid"]]
    assert wdf["aliases"] == [twin["gid"]]


def test_a_rule_derived_edge_is_left_out(con: sqlite3.Connection) -> None:
    _theirs(con)
    one, two = (r[0] for r in con.execute("SELECT id FROM edges ORDER BY id"))
    con.execute(
        "INSERT INTO edge_premises (edge_id, premise_id) VALUES (?, ?)", (one, two)
    )
    lines = list(graphio.export(con, graphio.Seed(project="synth")))
    assert [e["dst"] for e in _lines(lines, "edge")] == ["adaptor"]


def test_a_likely_entity_waits_with_its_edges_and_a_person_decides(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lines = _theirs(con)
    other = _other(monkeypatch, tmp_path)
    wdf = _ours(other)
    dry = graphio.import_lines(other, lines, source="laptop", dry_run=True)
    assert dry.landed == {"new": 2, "likely": 1}
    assert other.execute("SELECT count(*) FROM foreign_entities").fetchone()[0] == 0
    rep = graphio.import_lines(other, lines, source="laptop")
    assert rep.landed == {"new": 2, "likely": 1}
    assert (rep.linked, rep.held) == (1, 1)
    # nothing of theirs reached our method yet
    assert not other.execute(
        "SELECT 1 FROM edges WHERE dst = ? AND producer = 'import:laptop'", (wdf,)
    ).fetchone()
    waiting = store.foreign_likely(other)
    assert waiting["total"] == 1
    (item,) = waiting["items"]
    assert (item["name"], item["held"], item["candidate"]["id"]) == (
        "wave digital filter",
        1,
        wdf,
    )
    done = store.decide_foreign(other, "laptop", item["gid"], "same")
    assert (done["state"], done["entity_id"], done["linked"]) == ("same", wdf, 1)
    edge = other.execute(
        "SELECT run, evidence FROM edges WHERE dst = ? AND producer = 'import:laptop'",
        (wdf,),
    ).fetchone()
    assert edge["run"] == rep.run
    assert "[imported: qwen/r1, from “WDF paper”" in edge["evidence"]
    assert store.foreign_likely(other)["total"] == 0
    # the same file again: every end decided, every edge there, nothing written
    again = graphio.import_lines(other, lines, source="laptop")
    assert again.landed == {"new": 2, "same": 1}
    assert (again.linked, again.held, again.same, again.retired) == (0, 0, 2, 0)


def test_a_new_answer_for_a_name_ours_holds_is_a_thing_of_its_own(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lines = _theirs(con)
    other = _other(monkeypatch, tmp_path)
    wdf = _ours(other)
    graphio.import_lines(other, lines, source="laptop")
    (item,) = store.foreign_likely(other)["items"]
    done = store.decide_foreign(other, "laptop", item["gid"], "new")
    assert done["entity_id"] != wdf and done["linked"] == 1
    name = other.execute(
        "SELECT name FROM entities WHERE id = ?", (done["entity_id"],)
    ).fetchone()[0]
    assert name == "wave digital filter (laptop)"


def test_an_entity_left_out_takes_its_held_edges_with_it(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lines = _theirs(con)
    other = _other(monkeypatch, tmp_path)
    _ours(other)
    graphio.import_lines(other, lines, source="laptop")
    (item,) = store.foreign_likely(other)["items"]
    done = store.decide_foreign(other, "laptop", item["gid"], "left_out")
    assert (done["entity_id"], done["left_out"], done["linked"]) == (None, 1, 0)
    waiting = "SELECT count(*) FROM import_held WHERE edge_id IS NULL"
    assert other.execute(waiting).fetchone()[0] == 0
    again = graphio.import_lines(other, lines, source="laptop")
    assert (again.left_out, again.held, again.linked) == (1, 0, 0)
    with pytest.raises(ValueError):
        store.decide_foreign(other, "laptop", item["gid"], "maybe")
    with pytest.raises(KeyError):
        store.decide_foreign(other, "laptop", "e:NOTHERE", "same")


def test_an_answer_taken_back_ends_what_it_wrote_and_waits_again(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lines = _theirs(con)
    other = _other(monkeypatch, tmp_path)
    wdf = _ours(other)
    graphio.import_lines(other, lines, source="laptop")
    (item,) = store.foreign_likely(other)["items"]
    store.decide_foreign(other, "laptop", item["gid"], "same")
    with pytest.raises(ValueError, match="undo it"):
        store.decide_foreign(other, "laptop", item["gid"], "new")
    back = store.undo_foreign(other, "laptop", item["gid"])
    assert back["state"] == "likely" and back["ended"] == 1
    theirs_on_ours = (
        "SELECT count(*) FROM edges WHERE dst = ? AND producer = 'import:laptop'"
        " AND valid_to IS NULL"
    )
    assert other.execute(theirs_on_ours, (wdf,)).fetchone()[0] == 0
    (again,) = store.foreign_likely(other)["items"]
    assert again["held"] == 1
    done = store.decide_foreign(other, "laptop", item["gid"], "new")
    assert done["linked"] == 1 and done["entity_id"] != wdf
    # the undo's ending is a run like any, which restore_run takes back
    assert store.restore_run(other, back["run"])
    with pytest.raises(ValueError, match="no answer"):
        store.undo_foreign(other, "laptop", _new_gid(lines))


def _new_gid(lines: list[str]) -> str:
    """The gid of their entity nothing here answered to."""
    return next(e["gid"] for e in _lines(lines, "entity") if e["name"] == "adaptor")


def test_a_newer_file_ends_what_it_no_longer_says(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lines = _theirs(con)
    other = _other(monkeypatch, tmp_path)
    graphio.import_lines(other, lines, source="laptop")
    newer = [x for x in lines if '"dst": "adaptor"' not in x]
    rep = graphio.import_lines(other, newer, source="laptop")
    assert (rep.retired, rep.same, rep.linked) == (1, 1, 0)
    live = other.execute(
        "SELECT t.name FROM edges x JOIN entities t ON t.id = x.dst"
        " WHERE x.producer = 'import:laptop' AND x.valid_to IS NULL"
    ).fetchall()
    assert [r[0] for r in live] == ["wave digital filter"]


def test_a_file_that_comes_back_lands_on_its_own_rows(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lines = _theirs(con)
    # our own export, read back: every entity ours, every claim here already
    rep = graphio.import_lines(con, lines, source="mirror")
    assert rep.landed == {"own": 3}
    assert (rep.linked, rep.same, rep.retired) == (0, 2, 0)
    # through a friend's library and back: their entities that came from
    # here name our gids as aliases, so they land on our rows
    data = tmp_path / "data"
    other = _other(monkeypatch, tmp_path)
    graphio.import_lines(other, lines, source="me")
    back = list(graphio.export(other, graphio.Seed(entity="WDF paper")))
    ours = {r[0] for r in con.execute("SELECT gid FROM entities")}
    for e in _lines(back, "entity"):
        assert e["gid"] not in ours and set(e["aliases"]) & ours
    monkeypatch.setenv("PRAX_DATA_DIR", str(data))
    rep = graphio.import_lines(con, back, source="friend")
    assert rep.landed == {"own": 3} and rep.held == 0


def test_a_page_is_found_by_its_gid_and_never_by_an_equal_slug(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lines = _theirs(con)
    (theirs,) = _lines(lines, "page")
    other = _other(monkeypatch, tmp_path)
    store.write_page(other, "project-synth", "# Synth\n\nMine.", kind="project")
    rep = graphio.import_lines(other, lines, source="laptop")
    assert rep.pages_new == 1
    mine, copy = (
        store.get_page(other, "project-synth"),
        store.get_page(other, "project-synth-2"),
    )
    assert mine is not None and "Mine." in mine["text"] and mine["revision"] == 1
    assert copy is not None and copy["gid"] == theirs["gid"]
    again = graphio.import_lines(other, lines, source="laptop")
    assert (again.pages_new, again.pages_same) == (0, 1)


def test_the_door_lists_and_answers_the_likely_ones(client: TestClient) -> None:
    lines = _theirs(client.app.state.con)
    # a friend's copy of this file, in which the method is a thing of theirs:
    # its name answers to ours, so it is likely here
    theirs = "e:" + "F" * 26
    friend = []
    for r in (json.loads(x) for x in lines):
        if r["kind"] == "entity" and r["name"] == "wave digital filter":
            r = {**r, "gid": theirs}
        elif r.get("dst") == "wave digital filter":
            r = {**r, "dst_gid": theirs}
        friend.append(json.dumps(r))
    got = client.post(
        "/graph/import",
        params={"source": "friend"},
        content="\n".join(friend).encode("utf-8"),
    ).json()
    assert got["landed"] == {"own": 2, "likely": 1} and got["held"] == 1
    likely = client.get("/graph/imports/likely").json()
    assert likely["total"] == 1 and likely["items"][0]["gid"] == theirs
    answer = {"source": "friend", "gid": theirs}
    bad = client.post("/graph/imports/decide", json={**answer, "verdict": "perhaps"})
    assert bad.status_code == 400
    done = client.post(
        "/graph/imports/decide", json={**answer, "verdict": "same"}
    ).json()
    assert done["state"] == "same" and done["same"] == 1  # the claim was ours
    assert client.get("/graph/imports/likely").json()["total"] == 0
    undone = client.post("/graph/imports/undo", json=answer).json()
    assert undone["state"] == "likely"
    assert client.get("/graph/imports/likely").json()["total"] == 1
    assert client.post("/graph/imports/undo", json=answer).status_code == 400


def test_an_entity_line_without_its_gid_is_refused(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lines = _theirs(con)
    broken = [
        json.dumps({k: v for k, v in json.loads(x).items() if k != "gid"})
        if json.loads(x)["kind"] == "entity"
        else x.strip()
        for x in lines
    ]
    other = _other(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="no gid"):
        graphio.import_lines(other, broken, source="laptop")
