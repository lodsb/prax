#!/usr/bin/env python3
"""Rewrite extraction stamps from one ontology version string to another.

    python scripts/restamp_ontology.py --from 5 --to core1+research5 --dry-run
    python scripts/restamp_ontology.py --from 5 --to core1+research5

When the ontology was split into modules (2026-09-12) its version became
"core1+research5" without a change in what the graph accepts; documents
stamped "5" had been read under exactly that ontology. This rewrites
``meta.extraction.ontology_version`` and the history entries so the
selection and the promote queue do not treat them as unread. Edges keep
the version they were written under; they are history.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    con = store.connect()
    store.init_db(con)
    n = store.restamp_ontology(con, a.src, a.dst, commit=not a.dry_run)
    print(
        f"{config.db_path()}: {n} documents"
        f" {'would be' if a.dry_run else ''} restamped {a.src!r} -> {a.dst!r}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
