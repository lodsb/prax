"""Whether a model's probability means what it says (docs/PLAN.md, "A
confidence that was measured, not written").

A model asked a yes/no question gives a probability with its answer: the
share of the next token's mass on "yes" against "no". That number is not
yet a confidence. An instruction-tuned model is known to be sure too
often, so 0.95 may be right four times in five. Calibration is the
check: over decisions whose outcome is known, does 0.8 come true eight
times in ten? When it does not, a map fitted on part of the decisions
(Platt's logistic, or isotonic regression) turns the raw number into one
that does, and is measured on the rest.

Pure functions over lists of floats, no dependency: what the experiment
(``scripts/eval_confidence.py``) and later a threshold in the likely tier
of entity resolution both need.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

EPS = 1e-6


def yes_probability(top: Sequence[tuple[str, float]]) -> float | None:
    """P(yes) from one token's top alternatives (``(token, logprob)``): the
    mass on the forms of "yes" over the mass on "yes" and "no" together.
    None when neither word is among them."""
    yes = no = 0.0
    for token, logprob in top:
        word = token.strip().lower()
        if word == "yes":
            yes += math.exp(logprob)
        elif word == "no":
            no += math.exp(logprob)
    if yes + no <= 0:
        return None
    return yes / (yes + no)


def brier(p: Sequence[float], y: Sequence[int]) -> float:
    """The mean squared distance between a probability and what happened:
    0 is perfect, 0.25 is always saying one half."""
    return sum((pi - yi) ** 2 for pi, yi in zip(p, y, strict=True)) / max(1, len(p))


@dataclass
class Bin:
    low: float
    high: float
    count: int
    said: float  # the mean probability in the bin
    happened: float  # the share of yes in the bin


def reliability(p: Sequence[float], y: Sequence[int], bins: int = 10) -> list[Bin]:
    """The reliability table: probabilities in equal-width bins, and in
    each how often the answer was yes. A calibrated model's ``said`` and
    ``happened`` agree in every bin."""
    out = []
    for k in range(bins):
        low, high = k / bins, (k + 1) / bins
        idx = [
            i
            for i, pi in enumerate(p)
            if low <= pi < high or (k == bins - 1 and pi == 1.0)
        ]
        if not idx:
            continue
        out.append(
            Bin(
                low,
                high,
                len(idx),
                sum(p[i] for i in idx) / len(idx),
                sum(y[i] for i in idx) / len(idx),
            )
        )
    return out


def ece(p: Sequence[float], y: Sequence[int], bins: int = 10) -> float:
    """Expected calibration error: the gap between said and happened,
    averaged over the bins by how many decisions each holds."""
    table = reliability(p, y, bins)
    n = sum(b.count for b in table)
    return sum(b.count * abs(b.said - b.happened) for b in table) / max(1, n)


def _logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


@dataclass
class Platt:
    """A logistic map of the raw probability's logit: σ(a·logit(p) + b)."""

    a: float = 1.0
    b: float = 0.0

    def __call__(self, p: float) -> float:
        z = self.a * _logit(p) + self.b
        return 1.0 / (1.0 + math.exp(-z)) if z > -700 else 0.0


def _log_loss(a: float, b: float, x: Sequence[float], t: Sequence[float]) -> float:
    total = 0.0
    for xi, ti in zip(x, t, strict=True):
        z = a * xi + b
        # log(1 + e^z) - t z, written so neither side overflows
        soft = z + math.log1p(math.exp(-z)) if z > 0 else math.log1p(math.exp(z))
        total += soft - ti * z
    return total


def fit_platt(p: Sequence[float], y: Sequence[int], *, steps: int = 100) -> Platt:
    """Platt's fit by Newton's method on the log loss, the step halved
    until the loss falls (a full step overshoots when the raw
    probabilities take few values), with the targets softened as Platt
    did so a perfectly separated set stays finite."""
    pos = sum(y)
    neg = len(y) - pos
    hi, lo = (pos + 1) / (pos + 2), 1 / (neg + 2)
    t = [hi if yi else lo for yi in y]
    x = [_logit(pi) for pi in p]
    a, b = 1.0, 0.0
    loss = _log_loss(a, b, x, t)
    for _ in range(steps):
        ga = gb = haa = hab = hbb = 0.0
        for xi, ti in zip(x, t, strict=True):
            q = Platt(a, b)(1.0 / (1.0 + math.exp(-xi)))
            d = q - ti
            w = max(q * (1 - q), 1e-12)
            ga += d * xi
            gb += d
            haa += w * xi * xi
            hab += w * xi
            hbb += w
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        scale = 1.0
        while scale > 1e-8:
            na, nb = a - scale * da, b - scale * db
            new = _log_loss(na, nb, x, t)
            if new <= loss:
                break
            scale /= 2
        else:
            break
        moved = abs(na - a) + abs(nb - b)
        a, b, loss = na, nb, new
        if moved < 1e-9:
            break
    return Platt(a, b)


@dataclass
class Isotonic:
    """A non-decreasing step map from the raw probability, fitted by pool
    adjacent violators: the knots and the value above each."""

    knots: list[float]
    values: list[float]

    def __call__(self, p: float) -> float:
        if not self.knots:
            return p
        lo, hi = 0, len(self.knots) - 1
        if p <= self.knots[0]:
            return self.values[0]
        while lo < hi:  # the last knot at or below p
            mid = (lo + hi + 1) // 2
            if self.knots[mid] <= p:
                lo = mid
            else:
                hi = mid - 1
        return self.values[lo]


def fit_isotonic(p: Sequence[float], y: Sequence[int]) -> Isotonic:
    """Pool adjacent violators over the probabilities in order, the ties
    of one probability pooled first: many decisions share a value, and
    pooling a tie half gathered put one of 0.95's into 0.05's block."""
    groups: list[list[float]] = []  # [sum of y, count, p]
    for pi, yi in sorted(zip(p, y, strict=True)):
        if groups and groups[-1][2] == pi:
            groups[-1][0] += yi
            groups[-1][1] += 1
        else:
            groups.append([float(yi), 1.0, pi])
    blocks: list[list[float]] = []
    for g in groups:
        blocks.append(list(g))
        while len(blocks) > 1 and (
            blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]
        ):
            s_, n_, _ = blocks.pop()
            blocks[-1][0] += s_
            blocks[-1][1] += n_
    return Isotonic([b[2] for b in blocks], [b[0] / b[1] for b in blocks])


def split(n: int, *, share: float = 0.5, seed: int = 7) -> tuple[list[int], list[int]]:
    """Indices into a fitting half and a measuring half."""
    order = list(range(n))
    random.Random(seed).shuffle(order)
    cut = int(n * share)
    return sorted(order[:cut]), sorted(order[cut:])
