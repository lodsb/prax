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
import math
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


# who asks for readings without a person behind the request: the door's
# clock, the door itself (a re-parse asking its follow-ups), the OCR window
MACHINE = frozenset({"clock", "door", "ocr-window"})
# a person's request of at most this many readings asked in one minute is
# one they wait for; more is a backlog they handed over (2,956 figure
# readings asked on 2026-10-03 waited a day, as a backlog should)
SMALL = 10


def _quantiles(hours: list[float]) -> str:
    w = sorted(hours)
    p90 = w[max(0, math.ceil(len(w) * 0.9) - 1)]
    return f"{len(w):6}   {statistics.median(w):7.2f}   {p90:7.2f}   {w[-1]:7.1f}"


def _by_request(con: sqlite3.Connection, since: datetime) -> None:
    """The waits of a person's small requests, a person's bulk requests
    and the machine's, by reader: what the plan promises is the first."""
    rows = con.execute(
        "SELECT asked_by, extractor, substr(at, 1, 16), at, finished_at"
        " FROM readings WHERE finished_at IS NOT NULL AND at >= ?",
        (since.strftime("%Y-%m-%dT%H:%M:%SZ"),),
    ).fetchall()
    asked: dict[tuple[str, str], int] = defaultdict(int)
    for who, _, minute, _, _ in rows:
        asked[(who, minute)] += 1
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for who, reader, minute, at, done in rows:
        if who in MACHINE:
            kind = "machine"
        else:
            kind = "person, small" if asked[(who, minute)] <= SMALL else "person, bulk"
        hours = (_when(done) - _when(at)).total_seconds() / 3600
        groups[(kind, reader)].append(hours)
    print()
    print("asked by        reader               n   p50 h     p90 h     max h")
    for (kind, reader), spent in sorted(groups.items()):
        print(f"{kind:15} {reader:16} {_quantiles(spent)}")


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
    _by_request(con, since)
    if not path.exists():
        print(f"(no {path.name} yet: prax up writes it from its next swap)")


if __name__ == "__main__":
    main()
