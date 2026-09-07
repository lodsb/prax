#!/usr/bin/env python3
"""Rebuild chunks from the stored text artifacts.

    python scripts/rechunk.py --all              # every indexed document
    python scripts/rechunk.py --legacy           # only rows without a kind yet
    python scripts/rechunk.py --ids 12 34

Chunks are disposable (rationale R3): this touches only the ``chunks`` table
and its FTS index. Run it after a chunker change (``prax.chunking``) or after
a migration that added chunk columns. Safe to interrupt and re-run.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--all", action="store_true")
    sel.add_argument(
        "--legacy", action="store_true", help="documents with unkinded chunks"
    )
    sel.add_argument("--ids", type=int, nargs="+")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    con = store.connect()
    store.init_db(con)
    if a.ids:
        ids = a.ids
    elif a.legacy:
        ids = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT doc_id FROM chunks WHERE kind IS NULL ORDER BY doc_id"
            )
        ]
    else:
        ids = [
            r[0]
            for r in con.execute(
                "SELECT id FROM documents WHERE text_hash IS NOT NULL ORDER BY id"
            )
        ]
    ids = ids[: a.limit]
    print(f"store: {config.db_path()}; {len(ids)} documents", file=sys.stderr)
    t0 = time.monotonic()
    total = 0
    for n, doc_id in enumerate(ids, 1):
        total += store.rechunk(con, doc_id)
        if n % 500 == 0:
            print(f"[{n}/{len(ids)}] {total} chunks", file=sys.stderr)
    con.close()
    seconds = time.monotonic() - t0
    print(f"rechunked {len(ids)} documents into {total} chunks in {seconds:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
