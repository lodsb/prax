#!/usr/bin/env python3
"""Collect the acronyms the library defines and write the acronyms table.

    python scripts/build_acronyms.py            # scan every text artifact, replace
    python scripts/build_acronyms.py --dry-run  # the top pairings, nothing written

"phrase (ACRONYM)" definitions in the text artifacts (prax.acronyms), one
count per document; pairings defined by at least one document are kept and
the search uses those defined by two or more (store.expand_query). Runs in
a few minutes over the library; re-run after a large import.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import acronyms, config, store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=25)
    a = ap.parse_args()
    con = store.connect()
    store.init_db(con)
    t0 = time.monotonic()
    counts: Counter[tuple[str, str]] = Counter()
    n = 0
    for doc_id, text_hash in con.execute(
        "SELECT id, text_hash FROM documents WHERE text_hash IS NOT NULL"
    ).fetchall():
        path = config.archive_dir() / text_hash[:2] / text_hash
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        counts.update(acronyms.find(text))
        n += 1
    rows = [(acr, exp, docs) for (acr, exp), docs in counts.items()]
    distinct = len({acr for acr, _, _ in rows})
    print(
        f"{n} documents scanned in {time.monotonic() - t0:.0f} s:"
        f" {len(rows)} pairings, {distinct} acronyms,"
        f" {sum(1 for r in rows if r[2] >= 2)} pairings in two or more documents"
    )
    for (acr, exp), docs in counts.most_common(a.show):
        print(f"  {docs:4d}  {acr:8s} {exp}")
    if a.dry_run:
        return 0
    store.replace_acronyms(con, rows)
    print(f"acronyms table replaced: {len(rows)} rows in {config.db_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
