#!/usr/bin/env python3
"""Measure the model's reading of a publication date from the first page
against the documents Zotero dated (``meta.published.by == "record"``).

    python scripts/eval_dates.py --sample 120
    python scripts/eval_dates.py --sample 300 --seed 7 --model server-35b

Opens the store read-only (``PRAX_DATA_DIR``; the measurement scripts'
exception to invariant 4) and asks the model the titles step uses unless
``--model`` names another. Prints counts and document ids, never titles.
Scored: how often the model answers, how often its year is Zotero's, its
month where both have one, and the same for the heuristic it replaces
(the latest plausible year on the first page).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, models
from prax.writing import dates as reading

YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-2]\d)(?!\d)")


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--sample", type=int, default=120)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--model", help="a models: entry (default: the titles step's)")
    ap.add_argument(
        "--words", action="store_true", help="show the quoted words of a wrong answer"
    )
    a = ap.parse_args()
    data = config.data_dir()
    con = sqlite3.connect(f"file:{data / 'prax.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, title, text_hash, added_at, json_extract(meta, '$.published.date')"
        " AS gold FROM documents WHERE json_extract(meta, '$.published.by') = 'record'"
        " AND text_hash IS NOT NULL AND mime = 'application/pdf'"
        " AND json_extract(meta, '$.sensitivity.state') IS NULL ORDER BY id"
    ).fetchall()
    random.Random(a.seed).shuffle(rows)
    rows = rows[: a.sample]
    spec = models.spec(a.model) if a.model else models.resolve("titles")
    if spec is None:
        print("no model: name one with --model")
        return 2
    runtime = models.runtime(spec)
    tally: Counter[str] = Counter()
    wrong: list[str] = []
    t0 = time.monotonic()
    for r in rows:
        path = data / "archive" / r["text_hash"][:2] / r["text_hash"]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            continue
        gold = str(r["gold"])
        tally["documents"] += 1
        years = [
            int(y)
            for y in YEAR.findall(text[:4000])
            if int(y) <= int(r["added_at"][:4])
        ]
        if years and str(max(years)) == gold[:4]:
            tally["heuristic year right"] += 1
        got, _usage = reading.read_date(runtime, text, r["title"] or "")
        if got is None:
            tally["model abstained"] += 1
            continue
        tally["model answered"] += 1
        tally[f"answered {got.confidence}"] += 1
        agrees = bool(years) and str(max(years)) == got.date[:4]
        tally["agrees with heuristic"] += agrees
        if got.date[:4] == gold[:4]:
            tally["model year right"] += 1
            tally["right when agreeing"] += agrees
            tally[f"year right {got.confidence}"] += 1
            if len(got.date) >= 7 and len(gold) >= 7:
                tally["months compared"] += 1
                tally["month right"] += got.date[:7] == gold[:7]
        else:
            off = abs(int(got.date[:4]) - int(gold[:4]))
            tally[f"year off by {min(off, 5)}{'+' if off >= 5 else ''}"] += 1
            line = f"doc {r['id']}: model {got.date} ({got.confidence}), Zotero {gold}"
            wrong.append(line + (f" | {got.words[:70]}" if a.words else ""))
    n = max(tally["documents"], 1)
    answered = max(tally["model answered"], 1)
    t = tally
    took = time.monotonic() - t0
    print(f"{t['documents']} documents Zotero dated, model {spec.name}, {took:.0f} s")
    h = t["heuristic year right"]
    print(f"  heuristic (latest year on the first page): year right {h} ({h / n:.0%})")
    print(
        f"  model answered {t['model answered']} ({t['model answered'] / n:.0%}),"
        f" abstained {t['model abstained']}"
    )
    right = t["model year right"]
    print(
        f"  of its answers, year right {right} ({right / answered:.0%});"
        f" high {t['year right high']}/{t['answered high']},"
        f" medium {t['year right medium']}/{t['answered medium']}"
    )
    print(
        f"  where the model and the heuristic agree: {t['right when agreeing']}"
        f" right of {t['agrees with heuristic']}"
    )
    print(f"  month right {t['month right']} of {t['months compared']} compared")
    print(
        "  " + json.dumps({k: v for k, v in tally.items() if k.startswith("year off")})
    )
    for line in wrong[:15]:
        print("   ", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
