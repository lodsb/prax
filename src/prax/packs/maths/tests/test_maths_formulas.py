"""The equations step (AD2, step 4): the library's display formulas
read once and their chains judged, kept in the chunk's data, and shown
beside the passage."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import packs, store, work, worker
from prax.answering import ask
from prax.packs.maths import formulas, tool
from prax.steps.base import Pass

PAPER = """\
# A typo

The paper writes

$$\\tanh x = \\frac{e^{xx}-e^{-x}}{e^x+e^{-x}} = \\frac{e^{2x}-1}{e^{2x}+1} \\quad (3)$$

and then

$$y = x^2 \\quad (4)$$

which is all.
"""


def _calculator(
    request: dict[str, Any], timeout: float | None = None
) -> dict[str, Any]:
    """The runtime's answers for the paper's two formulas: (3) reads, and
    its first link fails in the one variable x."""
    out = []
    for r in request["batch"]:
        if r["op"] == "read":
            out.append({"read": {"a": {"symbols": ["x"]}}})
        else:
            fails = "xx" in r["a"] or "xx" in r["b"]
            out.append({"verdict": "does not hold" if fails else "holds"})
    return {"batch": out}


def test_formulas_are_checked_through_the_door(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    doc = int(store.ingest_text(con, PAPER, title="typo")["doc_id"])
    chunks = [c for c in store.list_chunks(con, doc) if c["kind"] == "formula"]
    assert len(chunks) == 2
    monkeypatch.setattr(tool, "run", _calculator)
    monkeypatch.setattr(tool, "python", lambda: "python")
    handed = client.get("/work/equations").json()["items"]
    assert {i["chunk_id"] for i in handed} == {c["chunk_id"] for c in chunks}
    work._leases.clear()  # the look above leased them
    door = worker.Door("http://testserver", client=client, name="test-worker")
    said = formulas.REGISTERED["equations"].run(Pass(door=door))
    assert said is not None and "2 formulas checked, 1 with a link" in said
    typo = store.get_chunk(con, chunks[0]["chunk_id"])
    assert typo is not None
    check = typo["data"]["check"]
    assert check["v"] == formulas.CHECK_VERSION and check["judged"] == 2
    assert len(check["broken"]) == 2  # both links hold the typo
    assert check["broken"][0] == r"\tanh x = \frac{e^{xx}-e^{-x}}{e^x+e^{-x}}"
    assert client.get("/work/equations").json()["items"] == []  # all checked
    near = store.equations_near(con, chunks[0]["chunk_id"])
    p = ask.Passage(
        1, doc, chunks[0]["chunk_id"], "typo", [], None, "formula", "", nearby=near
    )
    assert "does not hold" not in p.nearby_line()  # a host without the pack
    monkeypatch.setattr(ask, "MARK_NOTES", packs.mark_notes(["maths"]))
    assert "does not hold, by the calculator" in p.nearby_line()
    assert p.nearby_line().count("does not hold") == 1  # (4) is not marked


def test_a_worker_without_the_maths_environment_asks_for_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def none() -> str:
        raise tool.MathsUnavailable("no")

    monkeypatch.setattr(tool, "python", none)

    class Door:
        def get_json(self, *a: Any, **k: Any) -> dict[str, Any]:
            raise AssertionError("asked the door")

    assert formulas.REGISTERED["equations"].run(Pass(door=Door())) is None


def test_a_check_never_lands_on_a_formula_that_changed(
    client: TestClient,
) -> None:
    """Chunk ids are reused after a re-chunk: a check made of other LaTeX
    than the chunk now holds is not kept (the review of 2026-10-02)."""
    con = client.app.state.con
    doc = int(store.ingest_text(con, PAPER, title="typo")["doc_id"])
    chunk = next(c for c in store.list_chunks(con, doc) if c["kind"] == "formula")
    stale = {
        "v": formulas.CHECK_VERSION,
        "sha": formulas.latex_sha("other"),
        "reads": True,
    }

    def keep(mark: dict[str, Any]) -> int:
        marks = [{"chunk_id": chunk["chunk_id"], "mark": mark}]
        return store.set_chunk_marks(con, marks, **formulas.MARK)

    assert keep(stale) == 0
    latex = str(chunk["data"]["latex"])
    assert keep({**stale, "sha": formulas.latex_sha(latex)}) == 1
    # only a key a pack declared is written, or asked for
    with pytest.raises(ValueError, match="no pack marks"):
        store.set_chunk_marks(con, [], kind="formula", key="title", field="latex")
    with pytest.raises(ValueError, match="no pack marks"):
        store.chunks_to_mark(con, kind="formula", key="meta", field="latex", version=1)


def test_a_batch_the_calculator_fails_on_is_judged_one_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One formula that breaks the calculator does not keep the rest of its
    batch from ever being checked: they are judged alone, and it is kept as
    unread so it is not handed out again."""
    calls: list[int] = []

    def calculator(
        request: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        calls.append(len(request["batch"]))
        if any("bad" in r["a"] for r in request["batch"]):
            return {"error": "the calculator took longer than 20 s and was stopped"}
        return {"batch": [{"read": {}} for _ in request["batch"]]}

    monkeypatch.setattr(tool, "run", calculator)
    items = [
        {"chunk_id": 1, "latex": "x + 1"},
        {"chunk_id": 2, "latex": "bad"},
        {"chunk_id": 3, "latex": "y"},
    ]
    got = {r["chunk_id"]: r["check"] for r in formulas.checks_of(items)}
    assert got[1]["reads"] and got[3]["reads"]
    assert not got[2]["reads"] and got[2]["v"] == formulas.CHECK_VERSION
    assert calls[0] == 3 and len(calls) == 4  # the batch, then one at a time
