#!/usr/bin/env python3
"""Stage 1 stub: inventory + content-hash the old zoetrope disk.

Usage: python scripts/backfill.py /path/to/zoetrope-copy --dry-run
Run against a COPY first; review the dedupe report before real ingestion.
"""
import argparse
import hashlib
import sys
from collections import defaultdict
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    by_hash: dict[str, list[Path]] = defaultdict(list)
    for p in args.root.rglob("*"):
        if p.is_file():
            by_hash[hashlib.sha256(p.read_bytes()).hexdigest()].append(p)

    dupes = {h: ps for h, ps in by_hash.items() if len(ps) > 1}
    print(f"{sum(len(v) for v in by_hash.values())} files, "
          f"{len(by_hash)} unique, {len(dupes)} duplicated hashes")
    for h, ps in list(dupes.items())[:20]:
        print(f"  {h[:12]}: {[str(p) for p in ps]}")
    # TODO(Stage 1): when not --dry-run, archive + ingest via prax.store
    return 0


if __name__ == "__main__":
    sys.exit(main())
