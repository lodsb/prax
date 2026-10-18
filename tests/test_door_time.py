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
