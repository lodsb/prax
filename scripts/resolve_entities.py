#!/usr/bin/env python3
"""Entity resolution: merge entities that name the same thing.

    python scripts/resolve_entities.py --dry-run              # list candidates
    python scripts/resolve_entities.py --commit               # merge the sure ones
    python scripts/resolve_entities.py --commit --adjudicate  # ask Claude on the rest
    python scripts/resolve_entities.py --type author --dry-run

Merges are recorded as ``entities.canonical_id`` (prax.resolution); nothing
is deleted and ``traverse`` follows the pointers. Sure candidates are equal
after normalization or an initials form of the same author name; likely
candidates are close by name embedding and need an adjudicator.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, models, resolution, store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--commit", action="store_true")
    ap.add_argument("--type", help="only this entity type")
    ap.add_argument(
        "--no-likely", action="store_true", help="leave out the likely tier"
    )
    ap.add_argument(
        "--adjudicate", action="store_true", help="ask Claude about likely pairs"
    )
    ap.add_argument(
        "--model",
        help="a Claude model id or a prax.yaml model name (default: adjudicate step)",
    )
    ap.add_argument(
        "--twins",
        action="store_true",
        help="merge a concept into the method of the same name",
    )
    ap.add_argument("--show", type=int, default=40, help="candidates to print per tier")
    a = ap.parse_args()

    con = store.connect()
    store.init_db(con)
    plan = resolution.plan(con, etype=a.type, likely=not a.no_likely)
    print(
        f"store: {config.db_path()}; {len(plan.sure)} sure, {len(plan.likely)} likely"
        f" candidates{' (' + a.type + ')' if a.type else ''}",
        file=sys.stderr,
    )
    print(f"  {len(plan.twins)} concept/method twins", file=sys.stderr)
    for tier, items in (
        ("sure", plan.sure),
        ("twins", plan.twins),
        ("likely", plan.likely),
    ):
        for c in items[: a.show]:
            print(
                f"  {tier:6} {c.score:.2f} [{c.type}] {c.drop_name!r}"
                f" -> {c.keep_name!r}"
            )
        if len(items) > a.show:
            print(f"  … {len(items) - a.show} more {tier}")
    if a.dry_run:
        return 0
    adjudicator = None
    if a.adjudicate:
        spec = models.spec(a.model) if a.model else models.resolve("adjudicate")
        if spec is None or spec.kind != "claude":
            print(
                "adjudication needs a Claude model: --model, PRAX_ADJUDICATE or"
                " steps.adjudicate.model in prax.yaml",
                file=sys.stderr,
            )
            return 2
        adjudicator = resolution.ClaudeAdjudicator(model=spec.model)
    report = resolution.apply(con, plan, adjudicator=adjudicator, twins=a.twins)
    print(
        f"merged {report.merged_sure} sure, {report.merged_twins} twins and"
        f" {report.merged_likely} likely; {report.declined} declined"
    )
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
