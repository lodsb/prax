"""The equations step (AD2, step 4): the library's display formulas
read once and their chains judged, kept in the chunk's data, and shown
beside the passage."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import store, work, worker
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
            sym = {"symbols": ["x"]}
            out.append({"same": not fails, "read": {"a": sym, "b": sym}})
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
