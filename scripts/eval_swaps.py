#!/usr/bin/env python
"""How the card plan does (stage AI): per day, the swaps ``prax up`` made
and how long readings waited.

    python scripts/eval_swaps.py [--db PATH] [--days 14]

The swaps are ``run/swaps.jsonl`` beside the database (one line a swap or
a give-back, written by ``prax up`` since 2026-10-05: the quality
review's finding 20); the waits are the ``readings`` queue's, from asked
(``at``) to finished (``finished_at``), by extractor. Fewer swaps for the
same waits is what the plan is for, and the number a change to it is
judged by.

Opens the database read-only (``mode=ro``), like the other measurement
scripts; writes nothing.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config
from prax.host import process


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(config.data_dir() / "prax.db"))
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    since = datetime.now(UTC) - timedelta(days=args.days)
    swaps: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    path = process.run_dir(Path(args.db).parent) / process.SWAPS
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                at = _when(row["at"])
            except (ValueError, KeyError):
                continue
            if at >= since:
                why = str(row.get("why") or "")
                how = (
                    "plan"
                    if why.startswith("the plan")
                    else "asked"
                    if why == "asked for"
                    else "other"
                )
                swaps[at.date().isoformat()][f"{row.get('kind')}:{how}"] += 1
    con = sqlite3.connect(f"file:{Path(args.db).as_posix()}?mode=ro", uri=True)
    waits: dict[str, list[float]] = defaultdict(list)
    for at, done in con.execute(
        "SELECT at, finished_at FROM readings"
        " WHERE finished_at IS NOT NULL AND at >= ?",
        (since.strftime("%Y-%m-%dT%H:%M:%SZ"),),
    ):
        hours = (_when(done) - _when(at)).total_seconds() / 3600
        waits[_when(done).date().isoformat()].append(hours)
    print("day         swaps (plan / asked / back)   readings done   wait h p50   p90")
    for day in sorted(set(swaps) | set(waits)):
        s = swaps.get(day, {})
        w = sorted(waits.get(day, []))
        p50 = f"{statistics.median(w):9.1f}" if w else "        -"
        p90 = f"{w[int(len(w) * 0.9) - 1 if len(w) > 1 else 0]:6.1f}" if w else "     -"
        back = sum(n for k, n in s.items() if k.startswith("back:"))
        print(
            f"{day}  {s.get('swap:plan', 0):5} / {s.get('swap:asked', 0):5} / {back:4}"
            f"          {len(w):10}   {p50}   {p90}"
        )
    if not path.exists():
        print(f"(no {path.name} yet: prax up writes it from its next swap)")


if __name__ == "__main__":
    main()
