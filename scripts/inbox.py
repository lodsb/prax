#!/usr/bin/env python3
"""One pass over the drop folder and the captures, on the store host,
straight through the store (the door does this by itself while it runs:
it scans ``data/inbox/`` and a worker, ``scripts/work.py``, does the
model passes). This script is for the cases without a door: registering
somebody's folder once, or a pass on a machine where nothing else runs.

    python scripts/inbox.py                       # one pass over everything new
    python scripts/inbox.py --no-extract --no-embed   # parse and titles only
    python scripts/inbox.py --dir D:/drop         # another drop folder (consumed)
    python scripts/inbox.py --from D:/papers --domains research
                                                  # somebody's folder: files stay

``--watch`` still works but is not the way any more: two processes writing
one store is what the worker protocol exists to avoid.

Files: a file in ``inbox/<module>/`` belongs to that domain; ``<file>.json``
next to a file is a sidecar (``title``, ``source_url``, ``domains``,
``tags``); consumed files are removed, refused ones go to ``inbox/failed/``.

The passes never spend money: a step whose model is the Claude API is
skipped with a note (the promote pass is for that). They never touch the
curated imports either, only captures, uploads and dropped files. Embedding
needs to replace the index files; when the door on this machine has them
mapped, the watcher asks it to let go (``--door``, default the local door)
and tries again next time if it cannot. Every pass is a job (``GET /jobs``,
the Jobs view).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import inbox, pipeline, store


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, help="the drop folder (default: data/inbox)")
    ap.add_argument(
        "--also",
        type=Path,
        action="append",
        default=[],
        help="another folder to consume; by default Downloads/prax-inbox when it"
        " exists, where the browser extension saves what it could not fetch itself",
    )
    ap.add_argument(
        "--from",
        dest="from_dir",
        type=Path,
        help="register every file under this folder and leave the files in place",
    )
    ap.add_argument(
        "--domains", help="comma-separated domains for files that name none"
    )
    ap.add_argument("--watch", action="store_true", help="keep going")
    ap.add_argument(
        "--interval", type=float, default=20.0, help="seconds between passes"
    )
    ap.add_argument("--no-titles", action="store_true")
    ap.add_argument("--no-extract", action="store_true")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument(
        "--workers", type=int, default=3, help="extraction workers (served model)"
    )
    ap.add_argument(
        "--door",
        default=os.environ.get("PRAX_DOOR", "http://127.0.0.1:8000"),
        help="the door to ask for its index views before embedding",
    )
    ap.add_argument("--parse", action="store_true", help=argparse.SUPPRESS)  # always on
    ap.add_argument("--quiet", action="store_true", help="only the pass summaries")
    a = ap.parse_args()
    if a.from_dir and (a.dir or a.watch):
        ap.error("--from is one pass over somebody's folder; no --dir, no --watch")
    doms = [d.strip() for d in (a.domains or "").split(",") if d.strip()] or None
    con = store.connect()
    store.init_db(con)
    reaped = store.job_reap(con)
    if reaped:
        print(f"closed {reaped} job(s) whose process is gone", file=sys.stderr)
    root = a.from_dir or a.dir or inbox.inbox_dir()
    print(f"{'from' if a.from_dir else 'inbox'}: {root}", file=sys.stderr)
    say = None if a.quiet else (lambda t: print(t, flush=True))
    token = os.environ.get("PRAX_TOKEN") or None
    watch_job = store.Job(con, "inbox-watch", note=str(root)) if a.watch else None
    try:
        while True:
            report = inbox.scan(con, root, consume=not a.from_dir, domains=doms)
            for extra in (
                (a.also or inbox.browser_drop_folders()) if not a.from_dir else []
            ):
                if extra.is_dir():
                    more = inbox.scan(con, extra, consume=True, domains=doms)
                    for k in ("registered", "duplicates", "failed"):
                        getattr(report, k).extend(getattr(more, k))
                    report.waiting += more.waiting
            if report.registered or report.failed or not a.watch:
                print(f"{time.strftime('%H:%M:%S')} {report}", flush=True)
            try:
                done = pipeline.process_captures(
                    con,
                    retitle=not a.no_titles,
                    extract=not a.no_extract,
                    embed=not a.no_embed,
                    workers=a.workers,
                    door=a.door,
                    token=token,
                    log=say,
                )
            except Exception as exc:  # noqa: BLE001 - the watcher outlives a bad pass
                done = {"pass": f"failed: {type(exc).__name__}: {exc}"}
            if done:
                for step, what in done.items():
                    print(f"{time.strftime('%H:%M:%S')} {step}: {what}", flush=True)
            if watch_job:
                watch_job.update(note=f"last pass {time.strftime('%H:%M:%S')}")
            if not a.watch:
                return 1 if report.failed else 0
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        if watch_job:
            store.job_finish(con, watch_job.id, status="done", note="stopped")


if __name__ == "__main__":
    sys.exit(main())
