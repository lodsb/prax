"""The check of an answer after it is written (AD2, step 2,
docs/symbolic-maths.md)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from prax.packs.maths import check, tool

MATHS_PYTHON = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "prax"
    / "maths-venv"
    / "Scripts"
    / "python.exe"
)

DIODE_RESULT = (
    "evaluate: {...}\n  -> read a as I_s*(exp(v/V_T) - 1) | result: 0.0132319712753"
)


def test_a_display_is_split_at_its_top_level_equals_signs() -> None:
    assert check.sides(r"\tanh x = \frac{e^{2x} - 1}{e^{2x} + 1}, \quad (3)") == [
        r"\tanh x",
        r"\frac{e^{2x} - 1}{e^{2x} + 1}",
    ]
    assert check.sides(r"a \leq b") == []
    assert check.sides(r"\begin{aligned} a &= b \\ c &= d \end{aligned}") == []
    assert check.sides(r"f(x = 1)") == []  # inside parentheses


def test_a_definition_is_not_a_claim() -> None:
    """``I_1 = …`` names what it defines; only links between two
    expressions are checked."""
    answer = (
        "So $$I_1 = \\frac{I}{1 + e^{x}} = \\frac{I}{2}(1 - \\tanh(x/2))$$ and"
        " $$H_n(z) = \\frac{1 + A(z)}{2}$$ follow."
    )
    found = check.links(answer)
    assert [(left, right) for _, left, right in found] == [
        ("\\frac{I}{1 + e^{x}}", "\\frac{I}{2}(1 - \\tanh(x/2))")
    ]


def test_numbers_are_held_to_the_results_and_the_sources() -> None:
    question = "the current at v = 0.4 V with I_s = 2.52 nA and V_T = 25.85 mV"
    answer = (
        "With V_T = 25.85 mV the current is 13.28 mA, or 0.01323 A. "
        "In 2017 [3] the paper wrote 19.2 per volt; beta is 0.9366."
    )
    marked, found = check.check_numbers(
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
    assert "13.28 (the calculator gives 13.23) mA" in marked
    assert "0.9366 (not checked)" in marked
    sources = question + "\nthe factor 19.2 per volt"
    again, _ = check.check_numbers(marked, sources, [0.0132319712753])
    assert again.count("(not checked)") == 1  # not marked twice


@pytest.mark.skipif(not MATHS_PYTHON.exists(), reason="no maths environment")
def test_a_link_that_does_not_hold_is_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAX_MATHS_PYTHON", str(MATHS_PYTHON))
    answer = (
        "$$\\frac{1}{1 + e^{x}} = \\frac{1}{2}\\left(1 - \\tanh\\frac{x}{2}\\right)$$\n"
        "and $$\\frac{d}{dx}\\log(\\cosh x) = x \\tanh x$$\nso done."
    )
    marked, found = check.check_links(answer)
    assert [c["verdict"] for c in found] == ["holds", "does not hold"]
    assert marked.count("does not hold") == 1
    assert marked.index("does not hold") > marked.index("x \\tanh x$$")


def test_the_surf_runs_the_packs_answer_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """With tools on the answer goes through the checks; off, untouched."""
    from prax.answering import ask, surf

    def fake(text: str, **kw: Any) -> tuple[str, list[dict[str, Any]]]:
        return text + " [checked]", [{"number": "1.23", "verdict": "computed"}]

    monkeypatch.setattr(surf, "ANSWER_CHECKS", [fake])
    s = surf.Surf("q", "q", [], None, 5, 3, 1000)
    answerer = ask.StubAnswerer()
    out = surf._result(s, answerer, "the answer", 0.0)
    assert out["answer"] == "the answer [checked]"
    assert out["checks"] == [{"number": "1.23", "verdict": "computed"}]
    s.tools = False
    out = surf._result(s, answerer, "the answer", 0.0)
    assert (out["answer"], out["checks"]) == ("the answer", [])


def test_a_batch_is_one_process(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake(request: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        seen.append(request)
        reads = {"a": {"symbols": ["a"]}, "b": {"symbols": ["a"]}}
        return {
            "batch": [{"same": True, "read": reads}, {"same": False, "read": reads}]
        }

    monkeypatch.setattr(tool, "run", fake)
    answer = "$$a + a = 2 a = 3 a$$"
    _, found = check.check_links(answer)
    assert len(seen) == 1 and len(seen[0]["batch"]) == 2
    assert [c["verdict"] for c in found] == ["holds", "does not hold"]


def test_units_labels_and_lists_are_not_claims() -> None:
    """What the e1/e2 answers held that is not maths (2026-10-02): a unit
    conversion, a word label, a list of definitions in one display."""
    assert check.sides(r"R = 10 \text{ k}\Omega = 10,000 \, \Omega") == []
    assert check.sides(r"C = 1 \text{ \mu F} = 1 \times 10^{-6} \text{ F}") == []
    assert check.sides(r"V_T = 26 \text{ mV} = 0.026 \text{ V}") == []
    assert check.sides(r"A = a + 2RI_s, \quad B = -1, \quad C = -\frac{1}{2V_T}") == []
    assert check.sides(r"\text{Numerator} = 1 - 0.0327") == ["", "1 - 0.0327"]
    assert check.sides(r"5,296,136.52 - 1 = 5,296,135.52") == [
        "5296136.52 - 1",
        "5296135.52",
    ]
    assert check.is_name("u'") and check.is_name("(x)")


def test_what_is_judged() -> None:
    """Arithmetic to the precision its result is written with, and an
    identity in one variable; not an equation in one unknown, nor a
    relation among several (the library's 2,100-formula sample,
    2026-10-02)."""

    def res(a: list[str], b: list[str], **kw: Any) -> dict[str, Any]:
        return {"read": {"a": {"symbols": a}, "b": {"symbols": b}}, **kw}

    v = check.verdict
    assert v(
        r"31/53 \times 1200",
        "701.887",
        res([], [], same=False, difference="(0.0003+0j)"),
    )
    assert not v(
        "0.4/0.02585", "15.47814319", res([], [], same=False, difference="(0.004+0j)")
    )
    assert v(r"\tanh x", "...", res(["x"], ["x"], same=True)) is True
    assert v("1 - y", r"\frac{1}{9}", res(["y"], [], same=False)) is None
    several = res(["Q", "omega_c"], ["Delta_omega"], same=False)
    assert v(r"\omega_c/(2Q)", r"\Delta\omega/2", several) is None
    for display in (
        r"\lim_{z \to \infty} J_0(z) + J_0(0) = 1",
        r"3 \times 7 \frac{1}{51} - 12 = 9 \frac{3}{51}",
        r"n := n + 1",
        r"\frac{1}{\sqrt{2}} \quad g(0) = -g(1)",
        r"36/25 \div 25/18 = 648/625",
    ):
        assert check.sides(display) == [], display
