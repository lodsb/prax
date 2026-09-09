#!/usr/bin/env python3
"""Rebuild the document retrieval field (documents_fts) for every document.

    python scripts/refresh_document_fields.py            # all documents
    python scripts/refresh_document_fields.py --ids 1 2  # a few

The field (title, kind words, creators, venue, summary, an image
description's opening paragraph) follows a document's text and metadata
automatically; this script is the backfill after migration 0005 and the
repair after a change to ``store.document_field``. Changed fields lose
their document vector; run ``embed_pending.py`` afterwards.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ids", type=int, nargs="+")
    a = ap.parse_args()
    con = store.connect()
    store.init_db(con)
    changed = store.refresh_document_fields(con, a.ids)
    total = con.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
    print(f"store: {config.db_path()}; {changed} fields changed, {total} indexed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
