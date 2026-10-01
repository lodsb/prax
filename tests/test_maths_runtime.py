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
