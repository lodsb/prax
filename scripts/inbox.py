#!/usr/bin/env python3
"""The drop folder: register what lands in data/inbox/ and, on the batch
host, parse it.

    python scripts/inbox.py                       # one scan
    python scripts/inbox.py --parse               # scan, then parse what is pending
    python scripts/inbox.py --watch --interval 30 # keep scanning
    python scripts/inbox.py --dir D:/drop         # another drop folder (consumed)
    python scripts/inbox.py --from D:/papers --domains research --parse
                                                  # somebody's folder: files stay

A file in ``inbox/<module>/`` belongs to that domain (``inbox/family/``);
``<file>.json`` next to a file is a sidecar with ``title``, ``source_url``,
``domains`` and ``tags``. Consumed files are removed (the archive holds
their bytes); files the store refused go to ``inbox/failed/``. Uploads
through the door and pages sent by the extension are captures too; with
``--parse`` this script indexes those the door left pending as well; a
``--watch --parse`` loop on the batch host is what turns an uploaded PDF
from "pending" into "indexed" within one interval.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import inbox, store


def _parse_pending(con: store.sqlite3.Connection, *, quiet: bool) -> str | None:
    """Parse the captures the door only registered; None when there are
    none. Skips what this extractor version already tried."""
    from prax.parsers import queue

    ids = inbox.pending_captures(con)
    if not ids:
        return None
    log = (
        None
        if quiet
        else (lambda n, i, a: print(f"  [{n + 1}/{len(ids)}] {a} doc {i}"))
    )
    report = queue.run(con, ids, log=log)
    if report.actions.keys() <= {"seen", "skipped"}:
        return None  # nothing new to say
    return f"{time.strftime('%H:%M:%S')} {report}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, help="the drop folder (default: data/inbox)")
    ap.add_argument(
        "--from",
        dest="from_dir",
        type=Path,
        help="register every file under this folder and leave the files in place",
    )
    ap.add_argument(
        "--domains", help="comma-separated domains for files that name none"
    )
    ap.add_argument(
        "--parse",
        action="store_true",
        help="also parse captures the door only registered (uploads, fetched PDFs)",
    )
    ap.add_argument("--quiet", action="store_true", help="no per-document lines")
    ap.add_argument("--watch", action="store_true", help="keep scanning")
    ap.add_argument(
        "--interval", type=float, default=30.0, help="seconds between scans"
    )
    a = ap.parse_args()
    if a.from_dir and (a.dir or a.watch):
        ap.error("--from is one pass over somebody's folder; no --dir, no --watch")
    doms = [d.strip() for d in (a.domains or "").split(",") if d.strip()] or None
    con = store.connect()
    store.init_db(con)
    root = a.from_dir or a.dir or inbox.inbox_dir()
    if a.from_dir and not root.is_dir():
        ap.error(f"no such folder: {root}")
    print(f"{'from' if a.from_dir else 'inbox'}: {root}", file=sys.stderr)
    while True:
        report = inbox.scan(con, root, consume=not a.from_dir, domains=doms)
        if report.registered or report.failed or not a.watch:
            print(f"{time.strftime('%H:%M:%S')} {report}", flush=True)
        if a.parse:
            parsed = _parse_pending(con, quiet=a.quiet)
            if parsed or not a.watch:
                print(parsed or "nothing pending", flush=True)
        if not a.watch:
            return 1 if report.failed else 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
