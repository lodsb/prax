"""The maths pack's side in prax (src/prax/packs/maths/tool.py): the
surfer's syntax, a formula of the library by its chunk, the door's route
and the MCP tool. The calculator itself is faked here; the end-to-end case
runs it when the maths environment is installed."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.answering.ask import Passage
from prax.packs.maths import tool

PAPER = """\
# A diode clipper

The Shockley equation gives the current through a diode as

$$i = I_s \\left( e^{v/V_T} - 1 \\right), \\quad (1)$$

where I_s is the saturation current and V_T the thermal voltage.
"""

MATHS_PYTHON = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "prax"
    / "maths-venv"
    / "Scripts"
    / "python.exe"
)


def _formula(con: sqlite3.Connection) -> tuple[int, int]:
    doc = int(store.ingest_text(con, PAPER, title="clipper")["doc_id"])
    chunks = store.list_chunks(con, doc)
    formula = next(c for c in chunks if c["kind"] == "formula")
    text = next(c for c in chunks if c["kind"] != "formula")
    return int(formula["chunk_id"]), int(text["chunk_id"])


@pytest.fixture()
def asked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The calculator replaced by a recorder that answers with a reading."""
    seen: list[dict[str, Any]] = []

    def fake(request: dict[str, Any]) -> dict[str, Any]:
        seen.append(request)
        return {
            "op": request["op"],
            "read": {"a": {"text": "read"}},
            "result": {"text": "r"},
        }

    monkeypatch.setattr(tool, "run", fake)
    return seen


def test_the_surfers_syntax() -> None:
    assert tool.parse_step("same [3] == [7]") == {"op": "same", "a": "[3]", "b": "[7]"}
    assert tool.parse_step(r"integrate x \tanh(x)") == {
        "op": "integrate",
        "a": r"\tanh(x)",
        "args": {"var": "x"},
    }
    assert tool.parse_step("evaluate [4] with R=1000, C=1e-6") == {
        "op": "evaluate",
        "a": "[4]",
        "args": {"values": {"R": "1000", "C": "1e-6"}},
    }
    assert tool.parse_step(r"code python \log(x)")["args"] == {"language": "python"}
    for bad, says in (
        ("guess x", "takes one of"),
        ("same [1]", "two formulas"),
        ("integrate \\tanh(x)", "variable first"),
        ("read", "takes a formula"),
    ):
        with pytest.raises(ValueError, match=says):
            tool.parse_step(bad)


def test_a_passage_is_the_formula_it_holds(
    con: sqlite3.Connection, asked: list[dict[str, Any]]
) -> None:
    formula, text = _formula(con)

    class Surf:
        def by_n(self, arg: str) -> Passage | None:
            ids = {"[1]": formula, "[2]": text}
            if arg not in ids:
                return None
            return Passage(1, 1, ids[arg], "clipper", [], None, None, "")

    got = tool.surf_maths(con, Surf(), "read [1]")
    assert "read a as read" in got and "result: r" in got
    assert asked[-1]["a"].startswith("i = I_s")  # the chunk's LaTeX, resolved
    assert "not a display formula" in tool.surf_maths(con, Surf(), "read [2]")
    assert "no passage [9]" in tool.surf_maths(con, Surf(), "read [9]")


def test_the_route_needs_the_pack_and_its_environment(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"op": "read", "a": "x"}
    assert client.post("/maths", json=body).status_code == 404
    monkeypatch.setenv("PRAX_PACKS", "maths")
    monkeypatch.delenv("PRAX_MATHS_PYTHON", raising=False)
    got = client.post("/maths", json=body)
    assert got.status_code == 503 and "maths.python" in got.text


def test_the_route_resolves_a_chunk(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, asked: list[dict[str, Any]]
) -> None:
    monkeypatch.setenv("PRAX_PACKS", "maths")
    formula, _ = _formula(client.app.state.con)
    got = client.post("/maths", json={"op": "read", "a": f"chunk:{formula}"})
    assert got.status_code == 200 and got.json()["read"]["a"]["text"] == "read"
    assert asked[-1]["a"].startswith("i = I_s")
    assert client.post("/maths", json={"op": "guess", "a": "x"}).status_code == 400


def test_the_mcp_tool_makes_one_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, asked: list[dict[str, Any]]
) -> None:
    from prax import mcp_server

    monkeypatch.setenv("PRAX_PACKS", "maths")
    mcp_server.configure(client=client, base_url="http://testserver", token="")
    try:
        got = mcp_server.maths(op="integrate", a=r"\tanh(x)", var="x")
    finally:
        mcp_server._door = None
    assert got["result"]["text"] == "r"
    assert asked[-1] == {
        "op": "integrate",
        "a": r"\tanh(x)",
        "notation": "latex",
        "args": {"var": "x"},
    }


@pytest.mark.skipif(not MATHS_PYTHON.exists(), reason="no maths environment")
def test_end_to_end_the_adaa_antiderivative(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_PACKS", "maths")
    monkeypatch.setenv("PRAX_MATHS_PYTHON", str(MATHS_PYTHON))
    got = client.post(
        "/maths", json={"op": "integrate", "a": r"\tanh(x)", "args": {"var": "x"}}
    ).json()
    # log(cosh(x)), which SymPy writes as x - log(tanh(x) + 1)
    assert got["result"]["text"] in ("log(cosh(x))", "x - log(tanh(x) + 1)")
    formula, _ = _formula(client.app.state.con)
    read = client.post("/maths", json={"op": "read", "a": f"chunk:{formula}"}).json()
    assert {"I_{s}", "V_{T}", "v"} <= set(read["read"]["a"]["symbols"])


def test_plain_notation_is_seen_in_a_surf_step() -> None:
    """A model writes exp(x) and ** as often as LaTeX: a formula without a
    backslash and with a * goes as plain notation."""
    got = tool.parse_step("same I / (1 + exp(x)) == I/2 * (1 - tanh(x/2))")
    assert got["notation"] == "plain"
    assert "notation" not in tool.parse_step(r"same \frac{1}{2} == [3]")


def test_a_chain_and_the_new_operations_in_a_surf_step() -> None:
    got = tool.parse_step("chain (x+1)**2 == x**2 + 2*x + 1 == x**2 + 1")
    assert got["steps"] == ["(x+1)**2", "x**2 + 2*x + 1", "x**2 + 1"]
    assert got["notation"] == "plain"
    assert tool.parse_step("apart s 1/((s+1)*(s+2))")["args"] == {"var": "s"}
    with pytest.raises(ValueError, match="steps of a derivation"):
        tool.parse_step("chain x**2")
    got = tool.shown(
        {"chain": False, "broken_at": 2, "links": [{}, {"how": "numeric"}]}
    )
    assert "link 2 (step 2 to step 3) does not hold" in got


def test_a_passage_inside_a_formula_is_its_right_side(
    con: sqlite3.Connection, asked: list[dict[str, Any]]
) -> None:
    formula, _ = _formula(con)

    class Surf:
        def by_n(self, arg: str) -> Passage | None:
            if arg != "[1]":
                return None
            return Passage(1, 1, formula, "clipper", [], None, None, "")

    tool.surf_maths(con, Surf(), r"diff v \frac{1}{2}[1]")
    a = asked[-1]["a"]
    assert a.startswith(r"\frac{1}{2}(I_s") and "i =" not in a
    assert "notation" not in asked[-1]  # the passage is LaTeX
    tool.surf_maths(con, Surf(), "chain [1] == x")
    assert asked[-1]["steps"][0].startswith("i = I_s")


def test_the_answer_is_given_what_the_tools_worked_out() -> None:
    from prax.answering import surf

    s = surf.Surf("q", "q", [], None, 5, 3, 1000, note="a word")
    assert surf.answer_note(s) == "a word"
    s.worked = [f"maths: same {i}\n  -> the same" for i in range(9)]
    note = surf.answer_note(s)
    assert note.startswith("a word\n\nWorked out with the tools")
    assert "same 8" in note and "same 2" not in note  # the latest six
