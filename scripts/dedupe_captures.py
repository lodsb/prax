#!/usr/bin/env python3
"""Retire duplicate captures: the same page sent several times became
several documents (a page's markup differs between two visits, so the
bytes' hash does); by the fingerprint of their chunks they are one.

    python scripts/dedupe_captures.py --dry-run
    python scripts/dedupe_captures.py --commit
    python scripts/dedupe_captures.py --commit --threshold 0.8

Per URL one capture is kept (a snapshot over a bare DOM, an extracted one
over one not yet read, then the oldest) and the others whose chunk
fingerprint matches it (Jaccard at or above the threshold) are retired:
out of search and the graph, row and file kept, ``meta.retired`` naming
the keeper. Captures of a page that changed in between stay apart. New
captures are checked the same way as they arrive, so this is for what
came in before.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--commit", action="store_true")
    ap.add_argument("--threshold", type=float, default=store.DUPLICATE_THRESHOLD)
    a = ap.parse_args()
    con = store.connect()
    store.init_db(con)
    rep = store.dedupe_captures(con, threshold=a.threshold, commit=a.commit)
    verb = "retired" if a.commit else "would retire"
    print(
        f"store: {config.db_path()}; {len(rep['groups'])} URLs captured more than once"
    )
    for g in rep["groups"]:
        apart = f"; apart: {g['apart']}" if g["apart"] else ""
        gone = g["retire"] or "nothing"
        print(f"  keep {g['keep']}, {verb} {gone}{apart}  {g['url'][:70]}")
    print(f"{verb} {rep['retired']} documents; {rep['kept_apart']} kept apart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
