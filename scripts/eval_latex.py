#!/usr/bin/env python
"""How many of the library's display formulas a LaTeX parser reads, and
how many of the readings are plausibly faithful (docs/symbolic-maths.md).

    python scripts/eval_latex.py [--sample 2000] [--seed 4]

Needs SymPy with its LaTeX parser: ``antlr4-python3-runtime`` 4.11 for the
``antlr`` backend and ``lark`` for the other. prax's environment has
another ANTLR, so run it from an environment of its own. Opens the
database read-only (``mode=ro``), like the other measurement scripts.
Each parse runs in a worker process with a timeout: a parser can hang on
a pathological input.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, TimeoutError
from pathlib import Path

NUMBER = re.compile(r"^\s*\(\s*\d+[a-z]?\s*\)\s*(\\quad|\\qquad)?")
TAIL = re.compile(r"(\\quad|\\qquad|\\,|\\;|\\!|,|\.|;)+\s*$")
# what a command read as a symbol leaves in the expression's text
SIGNS = re.compile(
    r"\b(tilde|widetilde|hat|bar|vec|dot|mathcal|mathbf|mathrm|mathbb"
    r"|operatorname|text|quad)\b"
)


def clean(s: str) -> str:
    """The formula without its number, trailing spacing and punctuation,
    alignment marks, ``\\left``/``\\right``, labels and tags."""
    s = NUMBER.sub("", s)
    s = s.replace("\\\\", " ").replace("&", " ")
    s = re.sub(r"\\(left|right)\b", "", s)
    s = re.sub(r"\\(displaystyle|textstyle)", "", s)
    s = re.sub(r"\\(label|tag)\{[^}]*\}", "", s)
    return TAIL.sub("", s.strip()).strip()


def judge(backend: str, s: str) -> str:
    """What became of one formula: no parse, a sign of a misreading, or
    plausible."""
    from sympy.parsing.latex import parse_latex

    try:
        e = parse_latex(s, backend=backend)
    except Exception:  # noqa: BLE001 - every failure is the parser's answer
        return "no parse"
    t = str(e)
    if getattr(e, "is_Boolean", False) and not getattr(e, "is_Relational", False):
        return "evaluated to a truth value"
    if SIGNS.search(t):
        return "a command read as a symbol"
    if re.search(r"[A-Za-z]\[", s):
        return "an index read as a product"
    if "=" in s and not getattr(e, "is_Relational", False):
        return "cut short"
    if re.search(r"\b[A-Z](\*[A-Z]){2,}\b", t):
        return "a name split into letters"
    return "plausible"


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--sample", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=4)
    ap.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("PRAX_DATA_DIR", "data")) / "prax.db",
    )
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rows = [
        json.loads(d)["latex"]
        for (d,) in con.execute(
            "SELECT data FROM chunks WHERE kind = 'formula' AND data IS NOT NULL"
        )
    ]
    random.seed(a.seed)
    sample = [clean(s) for s in random.sample(rows, min(a.sample, len(rows)))]
    print(f"{len(rows)} formulas; a sample of {len(sample)}")
    for backend in ("antlr", "lark"):
        counts: Counter[str] = Counter()
        with ProcessPoolExecutor(max_workers=6) as pool:
            for f in [pool.submit(judge, backend, s) for s in sample]:
                try:
                    counts[f.result(timeout=20)] += 1
                except TimeoutError:
                    counts["timeout"] += 1
        print(backend)
        for what, n in counts.most_common():
            print(f"  {n:5} {100 * n / len(sample):3.0f}%  {what}")


if __name__ == "__main__":
    main()
