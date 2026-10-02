"""The check of an answer after it is written, and the calculator's judge
of one link (AD2; the review of 2026-10-02, docs/symbolic-maths.md)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from prax.packs.maths import check, tool

MATHS_PYTHON = Path(
    os.environ.get("PRAX_MATHS_PYTHON")
    or Path(os.environ.get("LOCALAPPDATA", ""))
    / "prax"
    / "maths-venv"
    / "Scripts"
    / "python.exe"
)
RUNTIME = Path(tool.__file__).with_name("runtime.py")
needs_runtime = pytest.mark.skipif(
    not MATHS_PYTHON.exists(), reason="no maths environment"
)


def judge(a: str, b: str) -> dict[str, Any]:
    out = subprocess.run(
        [str(MATHS_PYTHON), str(RUNTIME)],
        input=json.dumps({"op": "judge", "a": a, "b": b}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=True,
    )
    got: dict[str, Any] = json.loads(out.stdout)
    return got


def test_a_display_is_split_at_its_top_level_equals_signs() -> None:
    assert check.sides(r"\tanh x = \frac{e^{2x} - 1}{e^{2x} + 1}, \quad (3)") == [
        r"\tanh x",
        r"\frac{e^{2x} - 1}{e^{2x} + 1}",
    ]
    # the (2) of \log(2) is an argument, not an equation number
    assert check.sides(r"\log(a/2) = \log a - \log(2)")[1] == r"\log a - \log(2)"
    assert check.sides(r"5,296,136.52 - 1 = 5,296,135.52") == [
        "5296136.52 - 1",
        "5296135.52",
    ]
    for not_a_chain in (
        r"a \leq b",
        r"\begin{aligned} a &= b \\ c &= d \end{aligned}",
        r"\frac{1}{\sqrt{2}} \quad g(0) = -g(1)",
        r"A = a + 2RI_s, \quad B = -1",
        r"B = -1, C = -1/(2V_T)",
        r"f(x = 1)",
        r"\sqrt{2}^5 : 1 = 8 : 5^{\frac{5}{4}}",
    ):
        assert check.sides(not_a_chain) == [], not_a_chain


@needs_runtime
@pytest.mark.parametrize(
    ("a", "b", "verdict"),
    [
        (r"x^2 - 1", r"(x-1)(x+1)", "holds"),  # a name's ^ swallowed it before
        (r"\sin^2 x + \cos^2 x", r"1", "holds"),
        (r"\tanh x", r"\frac{e^{xx} - e^{-x}}{e^x + e^{-x}}", "does not hold"),
        (r"\frac{0.4}{0.02585}", r"15.47814319", "does not hold"),
        (r"\frac{31}{53} \times 1200", r"701.887", "holds"),  # to its digits
        (r"3x + 2", r"x + 6", "not judged"),  # a solving step is a condition
        (r"2x", r"x + 2", "not judged"),
        (r"e^x", r"2", "not judged"),  # a value of x, not an identity
        (r"I_1", r"\frac{I}{1+e^x}", "not judged"),  # a definition
        (r"H_n(z)", r"\frac{1+A(z)}{2}", "not judged"),
        (r"\frac{\omega_c}{2Q}", r"\frac{\Delta\omega}{2}", "not judged"),
        (r"2 \cdot 2^{(n-1)/2}", r"2^{\lceil n/2 \rceil}", "not judged"),
        (r"3 \times 7 \frac{1}{51} - 12", r"9 \frac{3}{51}", "not judged"),
        (r"36/25 \div 25/18", r"648/625", "not judged"),
        (r"1 \text{ \mu F}", r"1 \times 10^{-6} \text{ F}", "not judged"),
    ],
)
def test_the_judge_decides_on_the_parsed_expressions(
    a: str, b: str, verdict: str
) -> None:
    assert judge(a, b)["verdict"] == verdict


def test_numbers_are_held_to_the_results_and_the_sources() -> None:
    question = "the current at v = 0.4 V with I_s = 2.52 nA and V_T = 25.85 mV"
    answer = (
        "With V_T = 25.85 mV the current is 13.28 mA, or 0.01323 A. "
        "In 2017 [3] the paper wrote 19.2 per volt; beta is 0.9366."
    )
    found = check.check_numbers(
        answer, question + "\nthe factor 19.2 per volt", [0.0132319712753]
    )
    verdicts = {c["number"]: c["verdict"] for c in found}
    assert verdicts == {
        "25.85": "quoted",
        "13.28": "differs",
        "0.01323": "computed",
        "19.2": "quoted",
        "0.9366": "not checked",
    }
    differs = next(c for c in found if c["verdict"] == "differs")
    assert differs["result"] == "13.23"
    assert answer[differs["start"] : differs["end"]] == "13.28"


def test_numbers_in_maths_code_links_and_versions_are_left_alone() -> None:
    answer = (
        "Python 3.11 runs it; see https://doi.org/10.1109/5.771073 and"
        " arXiv:2305.12345 and $$V = 0.4/0.02585 = 15.478$$ and `1.2345`"
        " and $2.718$; it weighs 1,234.56 kg."
    )
    assert check.numbers(answer) == []


def test_the_answer_is_left_as_written_and_the_checks_carry_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(request: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        return {"batch": [{"verdict": "does not hold", "why": "arithmetic"}]}

    monkeypatch.setattr(tool, "run", fake)
    answer = "So $$\\frac{0.4}{0.02585} = 15.478$$ and the current is 13.28 mA."
    out, found = check.check_answer(
        answer,
        question="v = 0.4 V",
        passages=[],
        worked=["maths: {...}\n  -> read a as x | result: 0.0132319712753"],
    )
    assert out == answer
    link = next(c for c in found if c["kind"] == "link")
    assert link["verdict"] == "does not hold"
    assert answer[link["start"] : link["end"]].startswith("$$")
    number = next(c for c in found if c["kind"] == "number")
    assert (number["number"], number["verdict"]) == ("13.28", "differs")
    assert [c for c in found if c.get("number") == "15.478"] == []  # in the display


def test_an_answer_without_maths_is_not_number_checked() -> None:
    _, found = check.check_answer(
        "Use 1.25 cups of flour and bake at 180.5 degrees.",
        question="a cake",
        passages=[],
        worked=[],
    )
    assert found == []


def test_the_surf_runs_the_packs_answer_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """With tools on the answer goes through the checks; off, untouched."""
    from prax.answering import ask, surf

    def fake(text: str, **kw: Any) -> tuple[str, list[dict[str, Any]]]:
        return text, [{"kind": "number", "number": "1.23", "verdict": "computed"}]

    monkeypatch.setattr(surf, "ANSWER_CHECKS", [fake])
    s = surf.Surf("q", "q", [], None, 5, 3, 1000)
    answerer = ask.StubAnswerer()
    out = surf._result(s, answerer, "the answer", 0.0)
    assert out["answer"] == "the answer"
    assert out["checks"] == [
        {"kind": "number", "number": "1.23", "verdict": "computed"}
    ]
    s.tools = False
    out = surf._result(s, answerer, "the answer", 0.0)
    assert (out["answer"], out["checks"]) == ("the answer", [])


def test_a_batch_is_one_process(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake(request: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        seen.append(request)
        return {"batch": [{"verdict": "holds"}, {"verdict": "does not hold"}]}

    monkeypatch.setattr(tool, "run", fake)
    found = check.check_links("$$a + a = 2 a = 3 a$$")
    assert len(seen) == 1 and [r["op"] for r in seen[0]["batch"]] == ["judge"] * 2
    assert [c["verdict"] for c in found] == ["holds", "does not hold"]
