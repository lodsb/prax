#!/usr/bin/env python3
"""Run the retrieval eval set and print (or save) the report.

    python scripts/eval_retrieval.py                       # fixture store, all modes
    python scripts/eval_retrieval.py --out docs/eval/retrieval-2026-09-07.md
    python scripts/eval_retrieval.py --store C:/prax-data  # an existing store
    PRAX_EMBED=0 python scripts/eval_retrieval.py --modes fts

Without ``--store`` a throwaway store is built from the Zotero fixture in a
temporary directory (import, parse, embed with the configured embedder).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", type=Path, help="PRAX_DATA_DIR of an existing store")
    ap.add_argument("--queries", type=Path)
    ap.add_argument("--modes", nargs="+", default=["fts", "vec", "hybrid"])
    ap.add_argument("--depth", type=int, default=10)
    ap.add_argument("--out", type=Path, help="write the Markdown report here")
    a = ap.parse_args()

    tmp = None
    if a.store:
        os.environ["PRAX_DATA_DIR"] = str(a.store)
    else:
        tmp = tempfile.TemporaryDirectory(prefix="prax-eval-")
        os.environ["PRAX_DATA_DIR"] = str(Path(tmp.name) / "data")

    from prax import evaluation, store

    con = store.connect()
    store.init_db(con)
    build = None
    if not a.store:
        build = evaluation.build_fixture_store(con, Path(tmp.name) / "work")
        print(f"fixture store: {build}", file=sys.stderr)
    queries = evaluation.load_queries(a.queries or evaluation.QUERIES)
    scores, results = evaluation.evaluate(con, queries, tuple(a.modes), depth=a.depth)
    text = evaluation.report(scores, results, build=build)
    print(text)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
        print(f"wrote {a.out}", file=sys.stderr)
    con.close()
    if tmp:
        tmp.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
