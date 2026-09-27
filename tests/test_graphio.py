"""A piece of the graph as a file and back (``prax.graphio``): export
from one library, import into another, import again."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from prax import graphio, store

PAPER = "Wave digital filters. " * 40


def _library(con: sqlite3.Connection) -> int:
    """A project with one paper, a page with two revisions linking it,
    and an entity with a German label."""
    doc = int(store.ingest_text(con, PAPER, title="WDF paper")["doc_id"])
    meta = store.get_meta(con, doc)
    store.set_meta(con, doc, {**meta, "tags": ["project:synth"]})
    for rel, dst, dtype in (
        ("uses", "wave digital filter", "method"),
        ("uses", "circuit simulation", "concept"),
    ):
        store.link(
            con,
            store.Edge("WDF paper", "paper", rel, dst, dtype),
            confidence="EXTRACTED",
            source_doc=doc,
            evidence="section 2",
            producer="qwen",
            run="r1",
        )
    wdf = int(
        con.execute(
            "SELECT id FROM entities WHERE name = 'wave digital filter'"
        ).fetchone()[0]
    )
    store.add_label(con, wdf, "Wellendigitalfilter", lang="de", producer="t")
    store.write_page(con, "project-synth", "# Synth\n\nFirst notes.", kind="project")
    store.write_page(
        con, "project-synth", f"# Synth\n\nSee [WDF paper](#doc/{doc}).", kind="project"
    )
    return doc


def _export(con: sqlite3.Connection) -> list[str]:
    return list(graphio.export(con, graphio.Seed(project="synth")))


def test_an_export_is_stable_and_complete(con: sqlite3.Connection) -> None:
    doc = _library(con)
    lines = _export(con)
    head = json.loads(lines[0])
    assert head["kind"] == "header" and head["format"] == graphio.FORMAT
    assert head["seed"] == {"project": "synth", "hops": 1}
    assert "research" in head["modules"] and head["ontology"]
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds.count("edge") == 2 and kinds.count("page") == 1
    edge = next(json.loads(x) for x in lines if '"kind": "edge"' in x)
    assert edge["producer"] == "qwen" and edge["evidence"] == "section 2"
    assert edge["source_hash"] and "id" not in edge
    entity = next(json.loads(x) for x in lines if '"name": "wave digital filter"' in x)
    assert entity["labels"] == [
        {"label": "Wellendigitalfilter", "lang": "de", "kind": "alt"}
    ]
    page = next(json.loads(x) for x in lines if '"kind": "page"' in x)
    assert [r["revision"] for r in page["revisions"]] == [1, 2]
    assert f"(#doc/{doc})" in page["revisions"][1]["text"]
    # the same library, the same file, but for the moment it was written
    again = _export(con)
    assert again[1:] == lines[1:]
    with pytest.raises(ValueError):
        list(graphio.export(con, graphio.Seed()))


def _other(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> sqlite3.Connection:
    monkeypatch.setenv("PRAX_DATA_DIR", str(tmp_path / "other"))
    other = store.connect()
    store.init_db(other)
    return other


def test_an_import_links_everything_and_a_second_one_converges(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _library(con)
    lines = _export(con)
    other = _other(monkeypatch, tmp_path)
    held = int(store.ingest_text(other, PAPER, title="WDF paper")["doc_id"])
    dry = graphio.import_lines(other, lines, source="laptop", dry_run=True)
    assert (dry.linked, dry.pages_new) == (2, 1)
    assert other.execute("SELECT count(*) FROM edges").fetchone()[0] == 0
    rep = graphio.import_lines(other, lines, source="laptop")
    # the paper, and the page by identity (a page travels as a page line)
    assert (rep.documents, rep.documents_held, rep.linked, rep.queued) == (2, 1, 2, 0)
    rows = other.execute(
        "SELECT source_doc, producer, evidence FROM edges WHERE valid_to IS NULL"
        " AND producer LIKE 'import:%'"
    ).fetchall()
    assert {r["source_doc"] for r in rows} == {held}
    assert {r["producer"] for r in rows} == {"import:laptop"}
    assert all("[imported: qwen/r1]" in r["evidence"] for r in rows)
    # the German name came along
    walk = store.traverse(other, "Wellendigitalfilter", 1)
    assert walk, "the label leads to the entity"
    # the page's link points at this library's copy of the paper
    page = store.get_page(other, "project-synth")
    assert page is not None and page["revision"] == 2
    assert f"(#doc/{held})" in page["text"]
    # the same source again: the last import's edges retired, the new linked
    again = graphio.import_lines(other, lines, source="laptop")
    assert again.retired == 2 and again.linked == 2 and again.pages_same == 1
    live = other.execute(
        "SELECT count(*) FROM edges WHERE valid_to IS NULL"
        " AND producer = 'import:laptop'"
    ).fetchone()[0]
    assert live == 2
    # and the page made its own edge from its link, once
    assert store.find_edges(
        other,
        store.Edge("Project synth", "project", "synthesizes", "WDF paper", "paper"),
    )


def test_what_does_not_fit_is_queued_and_a_changed_page_gets_a_revision(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _library(con)
    lines = _export(con)
    # an edge the ontology here refuses, and a paper not held here
    bad = json.loads(next(x for x in lines if '"kind": "edge"' in x))
    bad["rel"] = "sings_to"
    lines.append(json.dumps(bad) + "\n")
    other = _other(monkeypatch, tmp_path)
    store.write_page(other, "project-synth", "# Synth\n\nMy own notes.", kind="project")
    rep = graphio.import_lines(other, lines, source="laptop")
    assert rep.queued == 1 and rep.documents_held == 0
    evidence = other.execute(
        "SELECT evidence FROM edges WHERE valid_to IS NULL LIMIT 1"
    ).fetchone()[0]
    assert "“WDF paper”" in evidence  # the paper named, as it is not here
    assert store.count_review(other) == 1
    page = store.get_page(other, "project-synth")
    assert page is not None and page["revision"] == 2 and rep.pages_revised == 1
    assert "this library's page differed" in page["revisions"][-1]["note"]
    # the link to a paper not held here keeps its title only
    assert "See [WDF paper]." in page["text"]


def test_a_file_that_is_not_an_export_is_refused(con: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="no header"):
        graphio.import_lines(con, ['{"kind": "edge"}'], source="x")


def test_the_door_exports_and_imports() -> None:
    from fastapi.testclient import TestClient

    from prax.api import app

    with TestClient(app) as client:
        _library(client.app.state.con)
        got = client.get("/graph/export", params={"project": "synth"})
        assert got.status_code == 200
        assert got.headers["content-type"].startswith("application/x-ndjson")
        assert 'filename="synth.graph.jsonl"' in got.headers["content-disposition"]
        body = got.content
        assert client.get("/graph/export").status_code == 400  # no seed
        dry = client.post(
            "/graph/import",
            params={"source": "self", "dry_run": "true"},
            content=body,
            headers={"Content-Type": "application/x-ndjson"},
        ).json()
        assert dry["linked"] == 2 and dry["documents_held"] == 2
        refused = client.post(
            "/graph/import", params={"source": "x"}, content=b'{"kind": "edge"}\n'
        )
        assert refused.status_code == 400
