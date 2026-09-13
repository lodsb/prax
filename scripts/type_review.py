#!/usr/bin/env python3
"""Apply the typing rules to the review queue (prax.review.apply_typing_rules).

    python scripts/type_review.py --dry-run     # what the rules would do, per rule
    python scripts/type_review.py --commit      # link, drop, close
    PRAX_TYPING=server-35b python scripts/type_review.py --model --dry-run --limit 200
    PRAX_TYPING=server-35b python scripts/type_review.py --model --commit

A model's misfits are systematic: the document typed as what it is about,
``authored_by`` written backwards or with authors on both ends, ``cites``
for a tool or method the paper uses, ``about`` for a claim it makes. The
rules recover what was meant from the item and the document's title, link
the result as INFERRED edges (producer ``typing-rules``), drop what no
relation can hold, and leave the rest open for a person or a bigger
ontology. Run ``replay_review.py`` first after an ontology bump.

``--model`` is the other half (prax.typing_pass): the items with no types
at all go to the model named by the ``typing`` step, a batch at a time,
which says what kind of thing each end is or that it is none; producer
``typing:<model>``, one run id per pass.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, review, store, typing_pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--commit", action="store_true")
    ap.add_argument(
        "--model", action="store_true", help="the model pass over the untyped items"
    )
    ap.add_argument("--limit", type=int, help="model: at most this many items")
    ap.add_argument("--workers", type=int, default=2, help="model: parallel requests")
    ap.add_argument("--samples", type=int, default=30, help="model: lines to show")
    a = ap.parse_args()
    con = store.connect()
    store.init_db(con)
    if a.model:
        return model_pass(con, a)
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


def model_pass(con: store.sqlite3.Connection, a: argparse.Namespace) -> int:
    say = lambda t: print(t, file=sys.stderr, flush=True)
    with store.Job(
        con, "typing (model)", note="dry run" if a.dry_run else "linking"
    ) as job:
        rep = typing_pass.run(
            con,
            commit=a.commit,
            limit=a.limit,
            workers=a.workers,
            log=say,
            keep_samples=a.samples,
        )
        job.update(note=str(rep)[:200])
    mode = "would" if a.dry_run else "did"
    print(f"store: {config.db_path()}; {mode}: {rep}")
    print(f"  tokens in {rep.input_tokens:,}, out {rep.output_tokens:,}")
    for shape, n in sorted(rep.by_shape.items(), key=lambda kv: -kv[1])[:25]:
        print(f"  {n:6d}  {shape}")
    if rep.samples:
        print("samples:")
        for outcome, line in rep.samples:
            print(f"  {outcome:5} {line}")
    if a.commit:
        print(f"run {rep.run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
