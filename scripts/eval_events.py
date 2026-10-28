#!/usr/bin/env python
"""What a library's event settings should be (``events.gap_hours`` and
``events.min_photos`` in prax.yaml, ``prax.store.graph.events``).

    python scripts/eval_events.py [--db PATH]

Groups the dated pictures as the ``events`` pass does, a run with no gap
of the given hours between two pictures and kept when it holds the given
number, for several gaps and sizes, and prints for each how many groups
it makes, how many pictures they hold, how many are one day long and
the longest in days. A gap too short splits a trip into its days; one
too long joins the weekends at home to the week after. The numbers of
2026-10-26 (a 24-hour gap, 165 groups of eight or more) are in the
module's docstring. Someone who photographs every day, or seldom, reads
this table and sets the two numbers in prax.yaml.

The split where the country changes, and a person's cuts, come on top
of these groups; they are left out here, so the counts are the rule's
alone.

Opens the database read-only (``mode=ro``), like the other measurement
scripts; writes nothing. Prints counts only.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config

GAPS = (6, 12, 24, 36, 48, 72)
SIZES = (4, 8, 16)


def _taken(con: sqlite3.Connection) -> list[datetime]:
    out = []
    for r in con.execute(
        "SELECT json_extract(meta, '$.taken.date') FROM documents"
        " WHERE json_extract(meta, '$.taken.date') IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    ):
        try:
            out.append(datetime.fromisoformat(str(r[0])[:19]))
        except ValueError:
            continue
    return sorted(out)


def groups(taken: list[datetime], gap_hours: float, size: int) -> list[list[datetime]]:
    """Runs of ``taken`` with no gap of ``gap_hours``, of ``size`` or more."""
    runs: list[list[datetime]] = []
    for when in taken:
        if runs and (when - runs[-1][-1]).total_seconds() < gap_hours * 3600:
            runs[-1].append(when)
        else:
            runs.append([when])
    return [r for r in runs if len(r) >= size]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=None)
    args = ap.parse_args()
    db = args.db or config.data_dir() / "prax.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    taken = _taken(con)
    print(f"{len(taken)} dated pictures")
    print("gap h  size  groups  pictures  one day  longest (days)")
    for gap in GAPS:
        for size in SIZES:
            got = groups(taken, gap, size)
            days = [(g[-1].date() - g[0].date()).days + 1 for g in got]
            print(
                f"{gap:5d}  {size:4d}  {len(got):6d}  {sum(map(len, got)):8d}"
                f"  {sum(d == 1 for d in days):7d}  {max(days, default=0):14d}"
            )


if __name__ == "__main__":
    main()
