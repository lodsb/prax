"""The maths pack's calculator (src/prax/packs/maths/runtime.py), run as
the pack runs it: a subprocess of the maths environment, JSON in and out.
Skipped where that environment is not installed (CI, the board)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

RUNTIME = Path(__file__).parents[1] / "src" / "prax" / "packs" / "maths" / "runtime.py"
PYTHON = Path(
    os.environ.get("PRAX_MATHS_PYTHON")
    or Path(os.environ.get("LOCALAPPDATA", ""))
    / "prax"
    / "maths-venv"
    / "Scripts"
    / "python.exe"
)
pytestmark = pytest.mark.skipif(not PYTHON.exists(), reason="no maths environment")


def ask(**request: Any) -> dict[str, Any]:
    out = subprocess.run(
        [str(PYTHON), str(RUNTIME)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    got = json.loads(out.stdout)
    assert "error" not in got, got
    return got


def test_a_paper_s_names_are_read_as_names() -> None:
    got = ask(op="read", a=r"\tilde{\zeta}_k + \mathcal{T} x[n-1] + G_{max}")
    read = got["read"]["a"]
    assert {"zeta_tilde_k", "T_cal", "G_max", "n"} <= set(read["symbols"])
    assert "x(n - 1)" in read["text"]  # the signal, not n*x
    assert {"accent", "font", "index", "subscript"} <= set(read["rules"])


def test_a_function_a_paper_names_is_sympys() -> None:
    got = ask(op="read", a=r"\operatorname{sgn}(x) (1 - e^{-|x|})")
    assert "sign(x)" in got["read"]["a"]["text"]


def test_cases_are_a_piecewise() -> None:
    got = ask(
        op="read", a=r"y = \begin{cases} 1 & x > 1 \\ x & \text{otherwise} \end{cases}"
    )
    assert "Piecewise" in got["read"]["a"]["text"]


def test_the_adaa_steps_of_a_tanh_shaper() -> None:
    """The user's example: the antiderivative of the shaper, checked by
    differentiating it back, and its line in C."""
    first = ask(op="integrate", a=r"\tanh(x)", args={"var": "x"})["result"]
    assert "log(cosh(x))" in first["text"] or "x - log(tanh(x) + 1)" in first["text"]
    same = ask(op="same", a=r"\frac{d}{dx} \log(\cosh(x))", b=r"\tanh(x)")
    assert same["same"] is True and same["how"] == "symbolic"
    code = ask(op="code", a=r"\log(\cosh(x))")["code"]
    assert code == "log(cosh(x))"


def test_same_says_how_and_catches_a_wrong_step() -> None:
    assert ask(op="same", a=r"\sin^2(x) + \cos^2(x)", b="1")["same"] is True
    wrong = ask(op="same", a=r"(a + b)^2", b=r"a^2 + b^2")
    assert wrong["same"] is False
    # two papers' notations, mapped by the model
    mapped = ask(
        op="same",
        a=r"\frac{1}{1 + s R C}",
        b=r"\frac{1}{1 + s \tau}",
        mapping={"tau": "R*C"},
    )
    assert mapped["read"]["b"]["symbols"] == ["s", "tau"]


def test_plain_notation_is_parsed_without_eval() -> None:
    got = ask(op="simplify", a="(x**2 - 1)/(x - 1)", notation="plain")
    assert got["result"]["text"] == "x + 1"
    bad = subprocess.run(
        [str(PYTHON), str(RUNTIME)],
        input=json.dumps(
            {
                "op": "read",
                "a": "__import__('os').system('echo pwned')",
                "notation": "plain",
            }
        ),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert "pwned" not in bad.stdout


def test_a_reading_that_stopped_short_is_refused() -> None:
    """The parser may return the first part of a formula and drop the rest;
    the runtime says so instead of answering about the fragment."""
    out = subprocess.run(
        [str(PYTHON), str(RUNTIME)],
        input=json.dumps({"op": "read", "a": r"\tilde{\zeta}_k_j + y"}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    got = json.loads(out.stdout)
    assert "only part of the formula" in got.get("error", ""), got


def test_a_derivation_step_the_model_wrote_is_checked() -> None:
    """The second antiderivative of tanh needs the dilogarithm, which SymPy
    does not find; a model writes it and ``same`` checks it. On 2026-10-01
    it caught a wrong sign in the first try."""
    right = "diff(x**2/2 - x*log(2) + polylog(2, -exp(-2*x))/2, x)"
    wrong = "diff(x**2/2 - x*log(2) - polylog(2, -exp(-2*x))/2, x)"
    assert ask(op="same", notation="plain", a=right, b="log(cosh(x))")["same"] is True
    assert ask(op="same", notation="plain", a=wrong, b="log(cosh(x))")["same"] is False
    out = subprocess.run(
        [str(PYTHON), str(RUNTIME)],
        input=json.dumps({"op": "code", "notation": "plain", "a": "polylog(2, x)"}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert "c has no polylog" in json.loads(out.stdout)["error"]


def test_a_chain_names_the_link_that_breaks() -> None:
    got = ask(
        op="chain", a="(x+1)**2 == x**2 + 2*x + 1 == x**2 + x + 1", notation="plain"
    )
    assert got["chain"] is False and got["broken_at"] == 2
    assert got["links"][0]["same"] is True
    assert ask(op="chain", steps=["sinh(x)/cosh(x)", "tanh(x)"], notation="plain")[
        "chain"
    ]


def test_the_algebra_operations() -> None:
    def text(**r: Any) -> str:
        return str(ask(notation="plain", **r)["result"]["text"])

    assert text(op="factor", a="x**2 - 1") == "(x - 1)*(x + 1)"
    assert text(op="expand", a="(x + 1)**2") == "x**2 + 2*x + 1"
    assert text(op="together", a="1/x + 1/y") == "(x + y)/(x*y)"
    assert text(op="apart", a="1/((s + 1)*(s + 2))", args={"var": "s"}) == (
        "-1/(s + 2) + 1/(s + 1)"
    )


def test_a_value_may_carry_a_prefix_and_a_unit() -> None:
    """10k, 1u, 26mV as a circuit writes them; beta is a quantity, not
    SymPy's beta function."""
    got = ask(
        op="evaluate",
        a="I_s*(exp(V/V_T) - 1)",
        notation="plain",
        args={"values": {"I_s": "1e-12", "V": "0.6", "V_T": "26mV"}},
    )
    assert abs(float(got["result"]["text"]) - 0.010524) < 1e-5
    got = ask(
        op="evaluate",
        a="beta*R",
        notation="plain",
        args={"values": {"beta": "2", "R": "4.7k"}},
    )
    assert float(got["result"]["text"]) == 9400.0
