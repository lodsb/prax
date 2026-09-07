#!/usr/bin/env python3
"""Parse queue: extract text for documents and index it through prax.store.

    python scripts/parse_pending.py --pending                       # never-indexed docs
    python scripts/parse_pending.py --pending --extractor pymupdf   # fast plain text
    python scripts/parse_pending.py --upgrade zotero-ft-cache --mime text/html
    python scripts/parse_pending.py --upgrade pymupdf/ --extractor docling --limit 20
    python scripts/parse_pending.py --ids 12 13 14 --extractor docling --force

Runs on the batch host (invariant 7). Extractors: see prax.parsers. The
default extractor per MIME type is the first installed one in the registry.
An upgrade keeps the old text when the new one is suspiciously short unless
``--force`` is given. Safe to interrupt and re-run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, parsers, store
from prax.parsers import queue


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sel = ap.add_argument_group("selection (combined with OR)")
    sel.add_argument("--pending", action="store_true", help="documents never indexed")
    sel.add_argument(
        "--upgrade",
        metavar="PREFIX",
        help="documents whose meta.text_source starts with PREFIX",
    )
    sel.add_argument("--ids", type=int, nargs="+", help="explicit document ids")
    ap.add_argument("--mime", help="only this MIME prefix, e.g. application/pdf")
    ap.add_argument("--extractor", choices=parsers.names(), help="override the default")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true", help="replace text even if shorter")
    ap.add_argument("--dry-run", action="store_true", help="list the selection only")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if not (a.pending or a.upgrade or a.ids):
        ap.error("select something: --pending, --upgrade PREFIX or --ids")

    con = store.connect()
    store.init_db(con)
    ids = list(a.ids or [])
    if a.pending or a.upgrade:
        ids += store.select_documents(
            con,
            pending=a.pending,
            text_source_prefix=a.upgrade,
            mime_prefix=a.mime,
        )
    ids = list(dict.fromkeys(ids))
    print(f"store: {config.db_path()}; {len(ids)} documents selected", file=sys.stderr)
    for e in parsers.REGISTRY:
        state = "installed" if e.available() else "missing"
        print(f"  {e.name:12} {state:9} {e.version}", file=sys.stderr)
    if a.dry_run:
        print(" ".join(map(str, ids)))
        return 0

    def log(n: int, doc_id: int, action: str) -> None:
        if not a.quiet and (n % 25 == 0 or action in ("error", "kept", "empty")):
            print(f"[{n:5}/{len(ids)}] {action:8} doc {doc_id}", file=sys.stderr)

    report = queue.run(
        con, ids, extractor=a.extractor, force=a.force, limit=a.limit, log=log
    )
    print(report, flush=True)
    for doc_id, err in report.errors[:50]:
        print(f"  error doc {doc_id}: {err}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
