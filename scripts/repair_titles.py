#!/usr/bin/env python3
"""Give documents a title worth the name (prax.titles, store.retitle).

    python scripts/repair_titles.py --dry-run        # who needs one, and why
    python scripts/repair_titles.py --sample 20      # guesses only, nothing applied
    python scripts/repair_titles.py --reason caps    # the recase rule only (no model)
    python scripts/repair_titles.py                  # everything, applied
    python scripts/repair_titles.py --ids 4056 4170  # named documents, even if repaired

ALL CAPS titles are recased by rule. File names and Zotero's "Unknown - No
Title" names go to the local model (PRAX_LOCAL_MODEL or --model) with the
beginning of the text, the file name, the first heading and the PDF's
metadata title as hints. A guess the text does not confirm (low confidence)
is applied only with --apply-low; documents without text keep their file
name. Every change goes through ``store.retitle``: the old title stays in
``meta.title_history``, the paper entity follows, the document field is
refreshed (run ``embed_pending.py`` afterwards, with the door stopped).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, local_llm, store, titles

REASONS = ("empty", "filename", "zotero-auto", "caps")


def _pdf_title(con: store.sqlite3.Connection, doc: dict) -> str | None:
    if doc["mime"] != "application/pdf":
        return None
    try:
        import pymupdf

        path = config.archive_dir() / doc["hash"][:2] / doc["hash"]
        with pymupdf.open(str(path)) as pdf:
            return titles.pdf_meta_title((pdf.metadata or {}).get("title"))
    except Exception:  # noqa: BLE001 - a hint only
        return None


def _filename(doc: dict) -> str | None:
    z = (doc["meta"] or {}).get("zotero") or {}
    return z.get("filename") or doc["title"]


def select(
    con: store.sqlite3.Connection, reasons: tuple[str, ...]
) -> list[tuple[int, str]]:
    out = []
    for r in con.execute("SELECT id, title, meta FROM documents ORDER BY id"):
        why = titles.needs_title(r["title"], json.loads(r["meta"] or "{}"))
        if why in reasons:
            out.append((r["id"], why))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sample", type=int, help="guess for N documents, apply nothing")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", type=int, nargs="+")
    ap.add_argument("--reason", choices=REASONS, nargs="+", default=list(REASONS))
    ap.add_argument("--model", help="GGUF path (default PRAX_LOCAL_MODEL)")
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
        path = a.model or os.environ.get("PRAX_LOCAL_MODEL")
        if not path:
            print("the model pass needs PRAX_LOCAL_MODEL or --model", file=sys.stderr)
            return 2
        runtime = local_llm.shared_runtime(path)

    run = "titles-" + time.strftime("%Y%m%dT%H%M%S")
    t0 = time.monotonic()
    done = skipped = failed = unconfirmed = 0
    entity_actions: dict[str, int] = {}
    sample_left = a.sample or 0
    for n, (doc_id, why) in enumerate(chosen, 1):
        doc = store.get_document(con, doc_id, max_chars=60000)
        if doc is None:
            continue
        old = doc["title"] or ""
        source: str
        confidence: str | None = None
        if why == "caps":
            new = titles.recase(old)
            source = "recase"
        else:
            if not doc["text"].strip():
                skipped += 1
                continue
            assert runtime is not None
            guess = titles.guess_title(
                runtime,
                doc["text"],
                filename=_filename(doc),
                heading=titles.first_heading(doc["text"]),
                pdf_title=_pdf_title(con, doc),
            )
            if guess is None:
                failed += 1
                if not a.quiet:
                    print(f"  {doc_id}: no usable title", file=sys.stderr)
                continue
            new, source, confidence = guess.title, runtime.name, guess.confidence
            if confidence == "low" and not a.sample and not a.apply_low:
                # the text does not confirm it: the file name stays for now
                unconfirmed += 1
                continue
        if a.sample:
            print(f"  {doc_id} [{why}] {old[:60]!r}")
            print(f"      -> {new!r} ({confidence or 'rule'})")
            sample_left -= 1
            if sample_left <= 0:
                break
            continue
        r = store.retitle(
            con, doc_id, new, source=source, run=run, confidence=confidence
        )
        if r["changed"]:
            done += 1
            if r["entity"]:
                entity_actions[r["entity"]] = entity_actions.get(r["entity"], 0) + 1
        if not a.quiet and (n % 50 == 0 or n == len(chosen)):
            rate = (time.monotonic() - t0) / n
            left = rate * (len(chosen) - n) / 60
            print(
                f"  {n}/{len(chosen)} ({rate:.1f} s/doc, ~{left:.0f} min left)",
                file=sys.stderr,
                flush=True,
            )
    if not a.sample:
        print(
            f"run {run}: {done} retitled, {skipped} without text, {failed} without a"
            f" usable guess, {unconfirmed} unconfirmed (file name kept),"
            f" entities {entity_actions or 'untouched'};"
            f" {time.monotonic() - t0:.0f} s. Next: embed_pending.py (door stopped)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
