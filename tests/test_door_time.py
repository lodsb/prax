"""The door's time for a worker's hand-outs (the web UI performance work
of 2026-10-18): a request from the page waits while the door's Python
builds a batch, so a hand-out that finds nothing must cost little."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from prax import models, store, work
from prax.graph import resolution
from prax.parsers import office
from prax.steps import base, graph
from prax.steps.writing import Titles

REAL_MODEL_DOWN = base.model_down


def _edge(con: sqlite3.Connection, e: store.Edge, doc: int) -> None:
    store.link(con, e, confidence="EXTRACTED", source_doc=doc, producer="t", run="t")


def _id(con: sqlite3.Connection, name: str, etype: str) -> int:
    return int(
        con.execute(
            "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
        ).fetchone()[0]
    )


def test_the_titles_hand_out_reads_only_the_keys_a_title_is_judged_by(
    con: sqlite3.Connection,
) -> None:
    big = {"references": {"links": list(range(500))}, "title_source": "zotero"}
    a = int(store.ingest_text(con, "text " * 20, title="a.pdf", meta=big)["doc_id"])
    tried = {"titles_tried": {"run": "titles-1", "why": "filename"}}
    b = int(store.ingest_text(con, "more " * 20, title="b.pdf", meta=tried)["doc_id"])
    gone = {"retired": {"by": "t", "for": a}}
    store.ingest_text(con, "gone " * 20, title="c.pdf", meta=gone)
    rows = {r["id"]: r for r in store.title_rows(con)}
    assert set(rows) == {a, b}  # the retired one is left out
    assert rows[a]["meta"] == {"title_source": "zotero"}
    assert rows[b]["meta"] == tried and rows[b]["title"] == "b.pdf"
    assert store.title_rows(con, [b])[0]["id"] == b


def test_the_adjudicate_hand_out_keeps_its_plan_while_nothing_it_reads_moves(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = models.ModelSpec(name="local", kind="openai", model="m")
    monkeypatch.setattr(
        models, "resolve", lambda s: spec if s == "adjudicate" else None
    )
    plans = []
    real_plan = resolution.plan

    def counted(c: Any, **kw: Any) -> resolution.Plan:
        plans.append(1)
        return real_plan(c, **kw)

    monkeypatch.setattr(resolution, "plan", counted)
    doc = int(store.ingest_text(con, "A paper. " * 30, title="P")["doc_id"])
    for name in ("wavelet transform", "wavelet transformation", "LDR", "LDRs"):
        _edge(con, store.Edge("P", "paper", "uses", name, "method"), doc)
    w1 = _id(con, "wavelet transform", "method")
    w2 = _id(con, "wavelet transformation", "method")
    store.replace_entity_candidates(con, "method", [(w1, w2, 0.97)], producer="t")

    def ask() -> list[dict[str, Any]]:
        got = work.hand_out(con, "adjudicate", limit=10, worker="w")
        return list(got["items"])

    first = ask()
    assert [(i["keep"], i["drop"]) for i in first] in ([(w1, w2)], [(w2, w1)])
    assert ask() == [] and len(plans) == 1  # leased: nothing, and no new plan
    # a new pair moves the stamp, and the plan is made again
    l1, l2 = _id(con, "LDR", "method"), _id(con, "LDRs", "method")
    store.replace_entity_candidates(
        con, "method", [(w1, w2, 0.97), (l1, l2, 0.95)], producer="t"
    )
    assert {i["drop"] for i in ask()} & {l1, l2} and len(plans) == 2
    # and a plan past its age is made again whatever the stamp says
    monkeypatch.setattr(graph, "PLAN_HELD_SECONDS", 0.0)
    ask()
    assert len(plans) == 3


def test_a_worker_asks_for_nothing_while_the_steps_model_cannot_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "model_down", REAL_MODEL_DOWN)
    spec = models.ModelSpec(
        name="local", kind="openai", model="m", base_url="http://127.0.0.1:9"
    )
    monkeypatch.setattr(models, "resolve", lambda s: spec)
    verdict = {"ok": False, "why": "http://127.0.0.1:9 is not answering"}
    monkeypatch.setattr(models, "ready", lambda s, **kw: dict(verdict))

    class Door:
        def __init__(self) -> None:
            self.asked: list[str] = []

        def get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
            self.asked.append(path)
            return {"items": []}

    door = Door()
    p = base.Pass(door)
    assert Titles().run(p) is None and graph.ADJUDICATE.run(p) is None
    assert door.asked == []
    verdict["ok"] = True
    Titles().run(p)
    assert door.asked == ["/work/titles"]


def test_libreoffice_is_looked_for_once(monkeypatch: pytest.MonkeyPatch) -> None:
    looked = []

    def which(name: str) -> str | None:
        looked.append(name)
        return None

    office.soffice_path.cache_clear()
    monkeypatch.setattr(office.shutil, "which", which)
    try:
        first = office.soffice_path()
        n = len(looked)
        assert office.soffice_path() == first and len(looked) == n
    finally:
        office.soffice_path.cache_clear()


def test_a_documents_links_are_found_through_its_aliases(
    con: sqlite3.Connection,
) -> None:
    from prax.store.graph import context

    doc = int(store.ingest_text(con, "text " * 20, title="Notes")["doc_id"])
    _edge(con, store.Edge("Notes", "paper", "links_to", "Other", "paper"), doc)
    _edge(con, store.Edge("Notes v1", "paper", "supersedes", "Older", "paper"), doc)
    _edge(con, store.Edge("Elsewhere", "paper", "links_to", "Third", "paper"), doc)
    # an alias of the document carries its facts; an entity of the same
    # name merged into another thing is that thing's
    store.merge_entities(con, _id(con, "Notes v1", "paper"), _id(con, "Notes", "paper"))
    _edge(con, store.Edge("Notes", "page", "links_to", "Fourth", "paper"), doc)
    store.merge_entities(
        con,
        _id(con, "Notes", "page"),
        _id(con, "Elsewhere", "paper"),
        across_types=True,
    )
    got = context._linked_documents(con, "Notes", limit=10)
    assert sorted((x["rel"], x["title"]) for x in got["out"]) == [
        ("links_to", "Other"),
        ("supersedes", "Older"),
    ]


def test_the_graph_views_hubs_are_counted_again_only_when_the_graph_moved(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prax.store.graph import views

    doc = int(store.ingest_text(con, "text " * 20, title="P")["doc_id"])
    _edge(con, store.Edge("P", "paper", "uses", "wavelets", "method"), doc)
    counted = []
    con.set_trace_callback(lambda sql: counted.append(1) if "deg(cid" in sql else None)
    names = lambda: {n["name"] for n in store.hub_graph(con)["nodes"]}
    assert "wavelets" in names() and len(counted) == 1
    names()
    assert len(counted) == 1  # nothing moved: the hubs held
    _edge(con, store.Edge("P", "paper", "uses", "filters", "method"), doc)
    assert "filters" in names() and len(counted) == 2
    # on a host, a minute's grace after the graph moved
    monkeypatch.setattr(views, "HUB_REFRESH", 60.0)
    _edge(con, store.Edge("P", "paper", "uses", "chirps", "method"), doc)
    assert "chirps" not in names() and len(counted) == 2
    con.set_trace_callback(None)


def test_changes_counts_each_side_once(con: sqlite3.Connection) -> None:
    # the quality review of 2026-10-18 (23): the corrections were counted
    # by a second pass over the same edges
    doc = int(store.ingest_text(con, "text " * 20, title="P")["doc_id"])
    _edge(con, store.Edge("P", "paper", "uses", "wavelets", "method"), doc)
    counted: list[str] = []
    con.set_trace_callback(
        lambda sql: (
            counted.append(sql) if "GROUP BY e.rel, e.source_doc" in sql else None
        )
    )
    got = store.changes(con, "2000-01-01")
    con.set_trace_callback(None)
    assert got["added"]["count"] == 1
    assert len(counted) == len([k for k in got if k in ("added", "ended")])
