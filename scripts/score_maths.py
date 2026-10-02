#!/usr/bin/env python3
"""Score the maths eval's answers by machine where a question says how
(``check:`` in the questions file), and compare with the hand scores
(docs/symbolic-maths.md, "How it is measured"; AD2, step 3).

    python scripts/score_maths.py docs/eval/maths-answers-2026-10-02-e1.json
    python scripts/score_maths.py docs/eval/maths-answers-*.json --hand

A ``check`` is the answer's yes or no (``answer: yes``), or a number
within a relative tolerance (``number: 0.013232, within: 0.001``), in any
unit a thousand apart (13.23 mA is 0.01323 A), with ``near`` for partly
right. The rest is scored by hand. Reads only the answers file; opens no
database and asks no model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

WAYS = (("on", "grounded"), ("on", "open"), ("off", "grounded"), ("off", "open"))
NUMBER = re.compile(r"(?<![\w.])(\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?)(?!\w)")
SCALES = (1.0, 1e3, 1e-3, 1e6, 1e-6, 1e9, 1e-9)
YES = re.compile(r"\b(yes|no)\b", re.IGNORECASE)
CLOSING = re.compile(r"answer:?\**\s*\**(yes|no)\b", re.IGNORECASE)
# an opening sentence that declines: the hand scores call it wrong even
# when the tool's number stands further on
REFUSAL = re.compile(
    r"(do|does) not (contain|provide|include|state|show|answer)"
    r"|cannot be answered|not possible to",
    re.IGNORECASE,
)
TIMES_TEN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\\times|×|x)\s*10\^\{?(-?\d+)\}?")


def numbers(text: str) -> list[float]:
    """Every number the answer writes, ``2.52 \\times 10^{-9}`` read as one."""
    out = [float(a) * 10 ** int(b) for a, b in TIMES_TEN.findall(text)]
    for m in NUMBER.finditer(TIMES_TEN.sub(" ", text)):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
    return out


def closest(text: str, target: float) -> float:
    """The smallest relative distance of any number in the text, in any
    of the units, to the target."""
    best = float("inf")
    for value in numbers(text):
        for k in SCALES:
            if target:
                best = min(best, abs(value * k - target) / abs(target))
    return best


def opening(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]


def decision(text: str) -> bool | None:
    """The answer's yes or no: the first one in its opening sentence, else
    a closing "Answer: yes"."""
    m = YES.search(opening(text)) or CLOSING.search(text)
    return None if m is None else m.group(1).lower() == "yes"


def score(check: dict[str, Any], answer: str) -> str:
    """R, P or W for one answer under its question's check. ``also`` is a
    pattern a right answer holds besides its verdict (P without it)."""
    if "answer" in check:
        said = decision(answer)
        if said is None or said != bool(check["answer"]):
            return "W"
        also = check.get("also")
        return "R" if not also or re.search(also, answer) else "P"
    if REFUSAL.search(opening(answer)):
        return "W"  # "the passages do not contain it", the number or not
    distance = closest(answer, float(check["number"]))
    if distance <= float(check.get("within", 0.001)):
        return "R"
    if distance <= float(check.get("near", 0)):
        return "P"
    return "W"


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("answers", nargs="+", type=Path)
    ap.add_argument(
        "--questions", type=Path, default=Path("tests/eval/questions-maths.yaml")
    )
    ap.add_argument(
        "--hand",
        nargs="?",
        const=Path("tests/eval/maths-hand-scores.yaml"),
        type=Path,
        help="compare with the hand scores (default file when given bare)",
    )
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    questions = yaml.safe_load(a.questions.read_text(encoding="utf-8"))["questions"]
    checked = {q["id"]: q["check"] for q in questions if q.get("check")}
    hand: dict[str, Any] = {}
    if a.hand:
        hand = yaml.safe_load(a.hand.read_text(encoding="utf-8"))["runs"]
    agree, compared = Counter(), Counter()
    for path in a.answers:
        done = json.loads(path.read_text(encoding="utf-8"))
        run = path.stem.removeprefix("maths-answers-")
        print(f"{path.name}  ({len(checked)} questions scored by machine)")
        for i, (tools, mode) in enumerate(WAYS):
            got = Counter()
            for qid, check in checked.items():
                row = done.get(f"{qid}|{tools}|{mode}")
                if not row or "error" in row:
                    continue
                verdict = score(check, row["answer"])
                got[verdict] += 1
                by_hand = (hand.get(run) or {}).get(qid)
                if by_hand:
                    compared[(tools, mode)] += 1
                    agree[(tools, mode)] += by_hand[i] == verdict
            print(
                f"  tools {tools:3}, {mode:8}  R {got['R']:2}  P {got['P']:2}"
                f"  W {got['W']:2}"
            )
    if compared:
        total = sum(compared.values())
        print(f"agreement with the hand scores: {sum(agree.values())} of {total}")
        for way, n in compared.items():
            print(f"  tools {way[0]:3}, {way[1]:8}  {agree[way]} of {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
