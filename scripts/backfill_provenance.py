#!/usr/bin/env python3
"""Fill producer and run on edges written before migration 0007.

    python scripts/backfill_provenance.py

Citation and page edges are recognized by their evidence prefix, Zotero
seeds by their relation and source, extracted edges by their source
document's extraction stamp (run = the ontology version). Idempotent.
Then ``store.provenance_summary`` (or this script's output) shows what
each producer and run contributed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, store


def main() -> int:
    con = store.connect()
    store.init_db(con)
    counts = store.backfill_provenance(con)
    print(f"store: {config.db_path()}; tagged {counts}")
    for row in store.provenance_summary(con):
        print(
            f"  {row['producer']!s:22} {row['run']!s:28} live {row['live']:6}"
            f"  retired {row['retired']:5}  since {row['first_at']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
