#!/usr/bin/env python3
"""Apply the typing rules to the review queue (prax.review.apply_typing_rules).

    python scripts/type_review.py --dry-run     # what the rules would do, per rule
    python scripts/type_review.py --commit      # link, drop, close

A model's misfits are systematic: the document typed as what it is about,
``authored_by`` written backwards or with authors on both ends, ``cites``
for a tool or method the paper uses, ``about`` for a claim it makes. The
rules recover what was meant from the item and the document's title, link
the result as INFERRED edges (producer ``typing-rules``), drop what no
relation can hold, and leave the rest open for a person or a bigger
ontology. Run ``replay_review.py`` first after an ontology bump.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, review, store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    con = store.connect()
    store.init_db(con)
    rep = review.apply_typing_rules(con, commit=a.commit)
    mode = "would" if a.dry_run else "did"
    print(f"store: {config.db_path()}; {rep.checked} typed items checked")
    for rule, n in sorted(rep.by_rule.items(), key=lambda kv: -kv[1]):
        print(f"  {n:6d}  {rule}")
    print(
        f"{mode} link {rep.linked} ({rep.existing} already in the graph), drop"
        f" {rep.dropped}; {rep.still_open} stay open"
        + (f"; run {rep.run}" if a.commit else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
