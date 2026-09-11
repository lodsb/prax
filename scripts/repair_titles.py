#!/usr/bin/env python3
"""Give documents a title worth the name (prax.titles, store.retitle).

    python scripts/repair_titles.py --dry-run        # who needs one, and why
    python scripts/repair_titles.py --sample 20      # guesses only, nothing applied
    python scripts/repair_titles.py --reason caps    # the recase rule only (no model)
    python scripts/repair_titles.py                  # everything, applied
    python scripts/repair_titles.py --ids 4056 4170  # named documents, even if repaired

ALL CAPS titles are recased by rule. File names and Zotero's "Unknown - No
Title" names go to the model of the titles step (prax.yaml, or --model) with the
beginning of the text, the file name, the first heading and the PDF's
metadata title as hints. A guess the text does not confirm (low confidence)
is applied only with --apply-low; documents without text keep their file
name. Every change goes through ``store.retitle``: the old title stays in
``meta.title_history``, the paper entity follows, the document field is
refreshed (run ``embed_pending.py`` afterwards, with the door stopped).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, models, pipeline, store, titles

REASONS = ("empty", "filename", "zotero-auto", "caps")


def select(
    con: store.sqlite3.Connection, reasons: tuple[str, ...]
) -> list[tuple[int, str]]:
    return pipeline.titles_needed(con, reasons=reasons)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sample", type=int, help="guess for N documents, apply nothing")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", type=int, nargs="+")
    ap.add_argument("--reason", choices=REASONS, nargs="+", default=list(REASONS))
    ap.add_argument(
        "--model",
        help="a model name from prax.yaml or a .gguf path (default: the titles step)",
    )
    ap.add_argument(
        "--apply-low",
        action="store_true",
        help="apply guesses the text does not confirm (low confidence) too",
    )
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    con = store.connect()
    store.init_db(con)
    if a.ids:
        chosen = [
            (
                i,
                titles.needs_title(store.get_document(con, i, max_chars=0)["title"])
                or "named",
            )
            for i in a.ids
        ]
    else:
        chosen = select(con, tuple(a.reason))
    if a.limit:
        chosen = chosen[: a.limit]
    by_reason: dict[str, int] = {}
    for _, why in chosen:
        by_reason[why] = by_reason.get(why, 0) + 1
    print(
        f"store: {config.db_path()}; {len(chosen)} documents: {by_reason}",
        file=sys.stderr,
    )
    if a.dry_run:
        for doc_id, why in chosen[:30]:
            d = store.get_document(con, doc_id, max_chars=0)
            print(f"  {doc_id} [{why}] {d['title'][:80]}")
        return 0

    runtime = None
    needs_model = any(why != "caps" for _, why in chosen)
    if needs_model:
        if a.model and a.model.lower().endswith(".gguf"):
            spec = models.ModelSpec(name="cli", kind="gguf", path=a.model)
        elif a.model:
            spec = models.spec(a.model)
        else:
            spec = models.resolve("titles")
        if spec is None:
            print(
                "no model for titles: steps.titles.model in prax.yaml, PRAX_TITLES,"
                " or --model",
                file=sys.stderr,
            )
            return 2
        runtime = models.runtime(spec)

    run = "titles-" + time.strftime("%Y%m%dT%H%M%S")
    say = None if a.quiet else (lambda t: print(t, file=sys.stderr, flush=True))
    if a.sample:
        rep = pipeline.retitle_documents(
            con, chosen[: a.sample], runtime, run=run, apply=False, log=say
        )
        for g in rep.guesses:
            print(f"  {g['doc_id']} [{g['why']}] {g['old'][:60]!r}")
            print(f"      -> {g['new']!r} ({g['confidence'] or 'rule'})")
        return 0
    with store.Job(con, "titles", total=len(chosen)) as job:
        rep = pipeline.retitle_documents(
            con, chosen, runtime, run=run, apply_low=a.apply_low, log=say, job=job
        )
        job.note(str(rep))
    print(f"run {run}: {rep}. Next: embed_pending.py, or the inbox watcher.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
