"""The ``equations`` step (AD2, step 4): every display formula of the
library read once by the calculator, and each link of a chain ``a = b =
c`` judged by the same rule as an answer's (``check.FREE_MAX``). The
result is kept in the chunk's ``data.check``; a formula whose own links
do not hold is shown so beside its passage ("equations nearby").

The door hands out formula chunks not checked at ``CHECK_VERSION``; the
worker runs the calculator over a batch in one process and posts the
checks. A worker without the maths environment (``maths.python``) asks
for nothing. Bump ``CHECK_VERSION`` when the reading rules or the
judging change, and every formula is checked again.
"""

from __future__ import annotations

import itertools
import time
from typing import Any

from prax import store
from prax.steps import leases
from prax.steps.base import HandOut, Pass, Step, TakeIn

from . import check, tool

CHECK_VERSION = 1
BATCH = 50  # formulas a batch; about one second of the calculator each


def judge(latex: str) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """The calculator's requests for one formula: its reading, then a
    ``same`` for each link worth judging; and the links."""
    requests: list[dict[str, Any]] = [{"op": "read", "a": latex}]
    parts = check.sides(latex)
    pairs = [
        (left, right)
        for left, right in itertools.pairwise(parts)
        if left and right and not check.is_name(left) and not check.is_name(right)
    ]
    requests += [{"op": "same", "a": left, "b": right} for left, right in pairs]
    return requests, pairs


def checks_of(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The worker's half: each formula's check, the batch in one process."""
    plan = [(it, *judge(str(it["latex"]))) for it in items]
    batch = [r for _, requests, _ in plan for r in requests]
    got = tool.run({"batch": batch}, timeout=max(60.0, 2.0 * len(batch)))
    answers = got.get("batch")
    if not isinstance(answers, list):
        why = str(got.get("error") or "the calculator gave no batch")
        raise RuntimeError(why)  # noqa: TRY004 - the calculator failed, not a type
    out, at = [], 0
    for it, requests, pairs in plan:
        mine = answers[at : at + len(requests)]
        at += len(requests)
        reading, links = mine[0], mine[1:]
        verdict: dict[str, Any] = {
            "v": CHECK_VERSION,
            "reads": "error" not in reading,
            "links": len(pairs),
            "holds": 0,
            "judged": 0,
        }
        broken = []
        for (left, right), res in zip(pairs, links, strict=False):
            holds = check.verdict(left, right, res)
            if holds is None:
                continue
            verdict["judged"] += 1
            if holds:
                verdict["holds"] += 1
            else:
                broken.append(f"{left} = {right}")
        if broken:
            verdict["broken"] = broken
        out.append({"chunk_id": it["chunk_id"], "check": verdict})
    return out


class FormulaCheck(Step):
    name = "equations"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        found = store.formulas_to_check(
            h.con,
            version=CHECK_VERSION,
            limit=min(h.limit, BATCH) if h.limit else BATCH,
            skip=tuple(sorted(leases.leased(self.name))),
        )
        h.lease([f["chunk_id"] for f in found])
        return h.batch(found)

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        t.release([int(r["chunk_id"]) for r in t.results])
        kept = store.set_formula_checks(
            t.con, [r for r in t.results if not r.get("error")]
        )
        t.out["applied"] = kept
        t.out["broken"] = sum(
            1 for r in t.results if (r.get("check") or {}).get("broken")
        )
        return t.out

    def run(self, p: Pass) -> str | None:
        try:
            tool.python()
        except tool.MathsUnavailable:
            return None  # no maths environment on this worker: nothing asked
        items = p.fetch(self.name).get("items") or []
        if not items:
            return None
        t0 = time.monotonic()
        results = checks_of(items)
        rep = p.post(self.name, {"results": results})
        return (
            f"{rep.get('applied', 0)} formulas checked, {rep.get('broken', 0)} with a"
            f" link that does not hold ({time.monotonic() - t0:.0f} s)"
        )


REGISTERED = {"equations": FormulaCheck()}
