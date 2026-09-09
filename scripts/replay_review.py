#!/usr/bin/env python3
"""Replay open review items against the current ontology.

    python scripts/replay_review.py --dry-run
    python scripts/replay_review.py --commit

After `ontology.yaml` grows (widened domains and ranges, new relations),
typed items the old version rejected are linked as edges with their
evidence and source document; no model is called. Details: prax.review.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, ontology, review, store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    con = store.connect()
    store.init_db(con)
    onto = ontology.current()
    if a.dry_run:
        fits = 0
        checked = 0
        for it in store.list_review(con, limit=100_000, unmapped=False):
            if not (it["src_type"] and it["dst_type"]):
                continue
            checked += 1
            try:
                onto.check_edge(it["src_type"], it["rel"], it["dst_type"])
                fits += 1
            except ValueError:
                pass
        print(
            f"store: {config.db_path()}; ontology v{onto.version}: {fits} of"
            f" {checked} typed open items would be linked"
        )
        return 0
    rep = review.replay(con, onto=onto)
    print(
        f"ontology v{rep.ontology_version}: {rep.checked} typed items checked,"
        f" {rep.linked} linked, {rep.existing} already in the graph,"
        f" {rep.still_open} still open"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
