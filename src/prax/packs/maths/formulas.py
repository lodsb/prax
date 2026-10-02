"""The ``equations`` step (AD2, step 4): every display formula of the
library read once by the calculator, each link of a chain ``a = b = c``
judged (``runtime.judge``, the same judge an answer's links get). The
result is kept in the chunk's ``data.check``; a formula whose own links
do not hold is shown so beside its passage ("equations nearby").

The door hands out formula chunks not checked at ``CHECK_VERSION``; the
worker judges a batch in one calculator process and posts the checks,
each stamped with the sha256 of the LaTeX it judged, so a check never
lands on a chunk whose formula has changed since (chunk ids are reused).
A batch the calculator fails on is judged one formula at a time, with a
time limit each; a formula that still fails is kept as unread, so it is
not handed out again for ever. A worker without the maths environment
(``maths.python``) asks for nothing. Bump ``CHECK_VERSION`` when the
reading rules or the judge change, and every formula is checked again.
"""

from __future__ import annotations

import hashlib
import itertools
import time
from typing import Any

from prax import store
from prax.steps import leases
from prax.steps.base import HandOut, Pass, Step, TakeIn

from . import check, tool

CHECK_VERSION = 2  # 2: the runtime's judge (2026-10-02)
BATCH = 50  # formulas a batch, whatever a worker's own batch size
ONE_TIMEOUT_S = 20.0  # one formula, when its batch failed


def latex_sha(latex: str) -> str:
    return hashlib.sha256(latex.encode("utf-8")).hexdigest()


def _links(latex: str) -> list[tuple[str, str]]:
    return [(a, b) for a, b in itertools.pairwise(check.sides(latex)) if a and b]


def _verdict(item: dict[str, Any], answers: list[dict[str, Any]]) -> dict[str, Any]:
    """One formula's check from its reading and its links' judgements."""
    reading, judged = answers[0], answers[1:]
    pairs = _links(str(item["latex"]))
    out: dict[str, Any] = {
        "v": CHECK_VERSION,
        "sha": latex_sha(str(item["latex"])),
        "reads": "error" not in reading,
        "links": len(pairs),
        "holds": 0,
        "judged": 0,
    }
    broken = []
    for (left, right), res in zip(pairs, judged, strict=False):
        verdict = res.get("verdict")
        if verdict not in ("holds", "does not hold"):
            continue
        out["judged"] += 1
        if verdict == "holds":
            out["holds"] += 1
        else:
            broken.append(f"{left} = {right}")
    if broken:
        out["broken"] = broken
    return out


def _requests(item: dict[str, Any]) -> list[dict[str, Any]]:
    latex = str(item["latex"])
    return [{"op": "read", "a": latex}] + [
        {"op": "judge", "a": a, "b": b} for a, b in _links(latex)
    ]


def checks_of(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The worker's half: each formula's check, the batch in one process;
    one formula at a time when the batch fails."""
    plan = [(it, _requests(it)) for it in items]
    batch = [r for _, requests in plan for r in requests]
    got = tool.run({"batch": batch}, timeout=max(60.0, 2.0 * len(batch)))
    answers = got.get("batch")
    out = []
    if isinstance(answers, list) and len(answers) == len(batch):
        at = 0
        for it, requests in plan:
            out.append(
                {
                    "chunk_id": it["chunk_id"],
                    "check": _verdict(it, answers[at : at + len(requests)]),
                }
            )
            at += len(requests)
        return out
    for it, requests in plan:  # the batch failed: one at a time
        one = tool.run({"batch": requests}, timeout=ONE_TIMEOUT_S).get("batch")
        if isinstance(one, list) and len(one) == len(requests):
            out.append({"chunk_id": it["chunk_id"], "check": _verdict(it, one)})
        else:
            # kept as unread, with its version: not handed out again
            out.append(
                {
                    "chunk_id": it["chunk_id"],
                    "check": {
                        "v": CHECK_VERSION,
                        "sha": latex_sha(str(it["latex"])),
                        "reads": False,
                        "links": 0,
                        "holds": 0,
                        "judged": 0,
                        "failed": "the calculator failed on it",
                    },
                }
            )
    return out


class FormulaCheck(Step):
    name = "equations"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        found = store.formulas_to_check(
            h.con,
            version=CHECK_VERSION,
            limit=BATCH,
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
