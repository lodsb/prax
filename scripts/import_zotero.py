#!/usr/bin/env python3
"""Import a Zotero library into prax.

    python scripts/import_zotero.py R:/Zotero --dry-run            # inventory only
    python scripts/import_zotero.py R:/Zotero --dry-run --hash     # + sha256 dedupe
    python scripts/import_zotero.py R:/Zotero --commit --limit 500 # trial run
    python scripts/import_zotero.py R:/Zotero --commit             # the whole library

The library's ``zotero.sqlite`` is copied to ``--workdir`` (default: the prax
data directory) and opened read-only; the live file is never touched.
Documents go through ``prax.store`` into ``PRAX_DATA_DIR``. Re-runs skip what
is already imported, so an interrupted run can simply be started again.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, store
from prax.importers import zotero


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "zotero_dir", type=Path, help="folder holding zotero.sqlite and storage/"
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="print the inventory, write nothing"
    )
    mode.add_argument("--commit", action="store_true", help="write through prax.store")
    ap.add_argument(
        "--hash", action="store_true", help="dry-run: sha256 every file for dedupe"
    )
    ap.add_argument("--limit", type=int, help="stop after N planned documents")
    ap.add_argument("--workdir", type=Path, help="where the zotero.sqlite copy lives")
    ap.add_argument("--quiet", action="store_true", help="no per-document progress")
    a = ap.parse_args()

    workdir = a.workdir or config.data_dir() / "zotero-import"
    lib = zotero.open_library(a.zotero_dir, workdir)
    print(f"library copy: {lib.copy_path}", file=sys.stderr)
    t0 = time.monotonic()
    try:
        if a.dry_run:
            inv = zotero.inventory(lib, hash_files=a.hash, limit=a.limit)
            print(inv.report(), flush=True)
        else:
            con = store.connect()
            store.init_db(con)
            print(f"store: {config.db_path()}", file=sys.stderr)

            def log(n: int, p: zotero.Planned, action: str) -> None:
                if not a.quiet and (n % 100 == 0 or action in ("error", "missing")):
                    print(
                        f"[{n:6}] {action:9} {p.kind:10} {p.key} {p.title[:60]}",
                        file=sys.stderr,
                    )

            report = zotero.run(lib, con, limit=a.limit, log=log)
            print(report, flush=True)
            for key, err in report.errors[:50]:
                print(f"  error {key}: {err}")
            con.close()
    finally:
        lib.close()
    print(f"done in {time.monotonic() - t0:.0f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
