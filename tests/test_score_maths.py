"""The maths eval's machine scoring (scripts/score_maths.py, AD2 step 3)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _scorer() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "score_maths.py"
    spec = importlib.util.spec_from_file_location("score_maths", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_yes_or_no_is_the_opening_sentences() -> None:
    sm = _scorer()
    check = {"answer": True}
    assert sm.score(check, "Yes, they are the same. No doubt.") == "R"
    assert sm.score(check, "No, they are not the same.") == "W"
    assert sm.score(check, "Based on the passages we can tell.\n\nAnswer: Yes") == "R"
    assert sm.score(check, "The passages are silent.") == "W"
    also = {"answer": False, "also": "V_T"}
    assert sm.score(also, "No: the exponent has V_1 for V_T.") == "R"
    assert sm.score(also, "No, it does not follow.") == "P"


def test_a_number_in_any_unit_within_its_tolerance() -> None:
    sm = _scorer()
    check = {"number": 0.013232, "within": 0.001, "near": 0.02}
    assert sm.score(check, "The current is 13.23 mA.") == "R"
    assert sm.score(check, "The current is 0.01323 A.") == "R"
    assert sm.score(check, "About 13.28 mA.") == "P"
    assert sm.score(check, "About 15 mA.") == "W"
    tau = {"number": 0.01, "within": 0.001}
    assert sm.score(tau, r"$\tau = 10,000 \times 1 \times 10^{-6} = 0.01$ s") == "R"
    assert sm.score(tau, "The passages do not contain it; the tool gave 0.01 s.") == "W"


def test_the_door_s_marks_are_not_the_model_s_answer() -> None:
    sm = _scorer()
    check = {"number": 0.013232, "within": 0.001, "near": 0.02}
    marked = "The current is 13.28 (the calculator gives 13.23) mA."
    assert sm.score(check, marked) == "P"  # the model's 13.28, not the mark's
