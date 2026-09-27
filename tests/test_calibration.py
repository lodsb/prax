"""The calibration maths the confidence experiment rests on
(``prax.calibration``)."""

from __future__ import annotations

import math
import random

import pytest

from prax import calibration as cal


def test_yes_is_the_share_of_yes_and_no_in_the_top_tokens() -> None:
    top = [("yes", math.log(0.6)), ("Yes", math.log(0.2)), (" no", math.log(0.1))]
    assert cal.yes_probability(top) == pytest.approx(0.8 / 0.9)
    assert cal.yes_probability([("The", -0.1)]) is None


def test_brier_and_the_reliability_table() -> None:
    assert cal.brier([1.0, 0.0], [1, 0]) == 0.0
    assert cal.brier([0.5, 0.5], [1, 0]) == 0.25
    table = cal.reliability([0.05, 0.15, 0.95, 1.0], [0, 0, 1, 1], bins=10)
    assert [(b.low, b.count) for b in table] == [(0.0, 1), (0.1, 1), (0.9, 2)]
    assert table[-1].happened == 1.0


def _overconfident(n: int = 4000) -> tuple[list[float], list[int]]:
    """A model sure too often: when it says 0.95 it is right 75% of the
    time, when it says 0.05 it is wrong 25%."""
    rng = random.Random(3)
    p, y = [], []
    for _ in range(n):
        yes = rng.random() < 0.5
        right = rng.random() < 0.75
        said_yes = yes if right else not yes
        p.append(0.95 if said_yes else 0.05)
        y.append(int(yes))
    return p, y


def test_platt_and_isotonic_fix_an_overconfident_model() -> None:
    p, y = _overconfident()
    fit, test = cal.split(len(p))
    raw = cal.ece([p[i] for i in test], [y[i] for i in test])
    assert raw > 0.15  # says 0.95, happens 0.75
    for fitted in (
        cal.fit_platt([p[i] for i in fit], [y[i] for i in fit]),
        cal.fit_isotonic([p[i] for i in fit], [y[i] for i in fit]),
    ):
        mapped = [fitted(p[i]) for i in test]
        assert cal.ece(mapped, [y[i] for i in test]) < 0.03
        assert fitted(0.95) == pytest.approx(0.75, abs=0.03)
        assert cal.brier(mapped, [y[i] for i in test]) < cal.brier(
            [p[i] for i in test], [y[i] for i in test]
        )


def test_isotonic_is_a_non_decreasing_step() -> None:
    iso = cal.fit_isotonic([0.1, 0.2, 0.3, 0.4], [0, 1, 0, 1])
    values = [iso(x) for x in (0.0, 0.1, 0.25, 0.35, 0.9)]
    assert values == sorted(values)
    assert iso(0.1) == 0.0 and iso(0.9) == 1.0


def test_the_split_is_seeded_and_covers_everything() -> None:
    a, b = cal.split(10)
    assert sorted(a + b) == list(range(10)) and not set(a) & set(b)
    assert cal.split(10) == (a, b)
