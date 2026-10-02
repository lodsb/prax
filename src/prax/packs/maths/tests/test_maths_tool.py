"""The maths pack's side in prax (src/prax/packs/maths/tool.py): the
surfer's JSON step, a formula of the library as LaTeX beside a placeholder,
the door's route and the MCP tool. The calculator is faked here; the
end-to-end cases run it when the maths environment is installed."""

from __future__ import annotations

import os
import re
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

MATHS_PYTHON = Path(
    os.environ.get("PRAX_MATHS_PYTHON")
    or Path(os.environ.get("LOCALAPPDATA", ""))
    / "prax"
    / "maths-venv"
    / "Scripts"
    / "python.exe"
)
needs_runtime = pytest.mark.skipif(
    not MATHS_PYTHON.exists(), reason="no maths environment"
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

    def fake(request: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        seen.append(request)
        return {
            "op": request["op"],
            "read": {"a": {"text": "read"}},
            "result": {"text": "r"},
        }

    monkeypatch.setattr(tool, "run", fake)
    return seen


class _Surf:
    """A surf with passage [1] the paper's formula and [2] its prose."""

    def __init__(self, formula: int, text: int) -> None:
        self.ids = {"[1]": formula, "[2]": text}

    def by_n(self, arg: str) -> Passage | None:
        if arg not in self.ids:
            return None
        return Passage(1, 1, self.ids[arg], "clipper", [], None, None, "")


def test_a_json_step_is_held_to_its_shape() -> None:
    """A model without the grammar may write anything; the step's keys and
    their types are checked, and what is wrong is said (the review of
    2026-10-02: "values": [1, 2] crashed the ask)."""
    ok = tool.request_of({"op": "solve", "formula": "I = I1*(1 + exp(u))", "var": "I1"})
    assert ok == {
        "op": "solve",
        "a": "I = I1*(1 + exp(u))",
        "notation": "plain",
        "args": {"var": "I1"},
    }
    chain = tool.request_of({"op": "chain", "formula": "a", "then": ["b", "c"]})
    assert chain["steps"] == ["a", "b", "c"]
    series = tool.request_of(
        {"op": "series", "formula": "tanh(x)", "var": "x", "at": "0"}
    )
    assert series["args"] == {"var": "x", "at": "0"}
    for bad, says in (
        ({"op": "evaluate", "formula": "x", "values": [1, 2]}, '"values" is a dict'),
        ({"op": "chain", "formula": "a", "then": "bc"}, '"then" is a list'),
        ({"op": "chain", "formula": "a", "then": [1]}, "a list of formulas"),
        ({"op": "same", "formula": "x"}, '"other"'),
        ({"op": "solve", "formula": "x = 1"}, '"var"'),
        ({"op": "guess", "formula": "x"}, "op is one of"),
        ({"op": "read", "formula": "x", "colour": "red"}, "has no 'colour'"),
    ):
        with pytest.raises(ValueError, match=re.escape(says)):
            tool.request_of(bad)


def test_a_step_that_is_not_json_says_how_to_write_it(
    con: sqlite3.Connection, asked: list[dict[str, Any]]
) -> None:
    formula, text = _formula(con)
    got = tool.surf_maths(con, _Surf(formula, text), "same [1] == x")
    assert got.startswith("maths: write the step as one JSON object")
    got = tool.surf_maths(
        con, _Surf(formula, text), '{"op": "evaluate", "values": [1]}'
    )
    assert '"values" is a dict' in got and asked == []  # no crash, nothing asked


def test_a_passage_goes_as_latex_beside_its_placeholder(
    con: sqlite3.Connection, asked: list[dict[str, Any]]
) -> None:
    """[n] is never turned into plain notation and read back: the runtime
    gets a placeholder and the formula's LaTeX."""
    formula, text = _formula(con)
    surf = _Surf(formula, text)
    step = '{"op": "evaluate", "formula": "[1]*2", "values": {"I_s": "2.52n"}}'
    got = tool.surf_maths(con, surf, step)
    assert "result: r" in got
    sent = asked[-1]
    name = tool.placeholder(formula)
    assert sent["a"] == f"{name}*2" and sent["notation"] == "plain"
    assert sent["passages"][name].startswith("i = I_s")
    assert "not a display formula" in tool.surf_maths(
        con, surf, '{"op": "read", "formula": "[2]"}'
    )
    assert "no passage [9]" in tool.surf_maths(
        con, surf, '{"op": "read", "formula": "[9]"}'
    )


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
    """In LaTeX notation a formula that is only a chunk is its LaTeX."""
    monkeypatch.setenv("PRAX_PACKS", "maths")
    formula, _ = _formula(client.app.state.con)
    got = client.post("/maths", json={"op": "read", "a": f"chunk:{formula}"})
    assert got.status_code == 200 and got.json()["read"]["a"]["text"] == "read"
    assert asked[-1]["a"].startswith("i = I_s") and "passages" not in asked[-1]
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


@needs_runtime
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


@needs_runtime
def test_end_to_end_a_library_formula_inside_a_plain_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The diode current through the paper's own formula: the placeholder
    takes the formula's right side, and the values' plain names reach the
    LaTeX reading's symbols."""
    monkeypatch.setenv("PRAX_PACKS", "maths")
    monkeypatch.setenv("PRAX_MATHS_PYTHON", str(MATHS_PYTHON))
    formula, _ = _formula(client.app.state.con)
    body = {
        "op": "evaluate",
        "a": f"chunk:{formula}",
        "notation": "plain",
        "args": {"values": {"I_s": "2.52n", "v": "0.4", "V_T": "25.85m"}},
    }
    got = client.post("/maths", json=body).json()
    # the whole equation, i = 0.01323...: the placeholder was all of a
    value = re.findall(r"[-+]?\d+\.\d+(?:e[-+]?\d+)?", got["result"]["text"])[-1]
    assert float(value) == pytest.approx(0.0132320, rel=1e-5)


def test_the_answer_is_given_what_the_tools_worked_out() -> None:
    from prax.answering import ask, surf
    from prax.packs import maths

    s = surf.Surf("q", "q", [], None, 5, 3, 1000, note="a word")
    assert surf.results_of(s) == []
    s.worked = [f"maths: same {i}\n  -> the same" for i in range(9)]
    results = surf.results_of(s)
    assert results[0].startswith("maths: same 3") and len(results) == 6  # the latest
    with_results = ask.Bundle(
        "q", note="a word", results=results, results_prompt=maths.ANSWER_PROMPT
    )
    message = with_results.as_message()
    assert "Note: a word" in message and "Worked out with the tools" in message
    assert "computed by a calculator" in with_results.system
    # no results, no words about a calculator: a tools-off answer invented
    # a calculator note when every prompt had them (2026-10-02)
    without = ask.Bundle("q", note="a word", results_prompt=maths.ANSWER_PROMPT)
    assert "calculator" not in without.system
    assert "Worked out" not in without.as_message()


def test_the_json_steps_grammar_names_every_operation() -> None:
    """The manifest holds data only, so its grammar spells the operations
    out; they are the tool's. A formula string holds no backslash: its
    character class leaves the backslash out."""
    from prax.packs import maths

    rules = ("m-check", "m-algebra", "m-calculus")
    lines = [x for x in maths.GRAMMAR.splitlines() if x.startswith(rules)]
    names = [n for x in lines for n in re.findall(r'"(\w+)"', x)]
    assert sorted(names) == sorted(tool.OPERATIONS)
    ch = next(x for x in maths.GRAMMAR.splitlines() if x.startswith("m-ch ::="))
    assert ch.startswith('m-ch ::= [^"' + "\\\\")


def test_the_surf_grammar_takes_a_tools_own_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prax.answering import surf
    from prax.packs import maths

    monkeypatch.setattr(surf, "PACK_TOOLS", ("maths",))
    monkeypatch.setattr(surf, "PACK_GRAMMAR", {"maths": maths.GRAMMAR.strip()})
    s = surf.Surf("q", "q", [], None, 5, 3, 1000)
    g = surf.grammar(s)
    assert 'maths ::= "maths: {" m-op' in g and "m-str ::=" in g
    assert 'maths ::= "maths: " char' not in g
    s.tools = False
    assert "m-op" not in surf.grammar(s)
