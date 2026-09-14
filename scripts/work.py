#!/usr/bin/env python3
"""The worker: model work for a door, from this machine, without opening
the database (docs/howto.md 3l). ``prax work`` is the same thing under a
shorter name; this script is what a cron line or a service unit calls
without the package's console script on PATH.

    python scripts/work.py                          # one pass against the local door
    python scripts/work.py --watch                  # keep going (the usual way)
    python scripts/work.py --door http://board:8000 --watch
    python scripts/work.py --scope all --steps extract --limit 20   # a backlog pass
    python scripts/work.py --no-embed               # leave vectors to another worker

Steps: parse (the originals the door only registered), titles, extract,
embed; each with the models of this machine's prax.yaml, never a paid
one unasked. Local drop folders (Downloads/prax-inbox, ``--also``) are
uploaded with their sidecars. ``PRAX_DOOR`` and ``PRAX_TOKEN`` in the
environment stand in for ``--door`` and ``--token``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import inbox, worker


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--door", default=os.environ.get("PRAX_DOOR", "http://127.0.0.1:8000")
    )
    ap.add_argument("--token", default=os.environ.get("PRAX_TOKEN") or None)
    ap.add_argument("--watch", action="store_true", help="keep going")
    ap.add_argument("--interval", type=float, default=20.0)
    ap.add_argument("--scope", choices=("captures", "all"), default="captures")
    ap.add_argument("--steps", default="parse,titles,extract,embed")
    ap.add_argument(
        "--spend", action="store_true", help="let the promote step spend money"
    )
    for step in worker.STEPS:
        ap.add_argument(
            f"--no-{step}", action="store_true", help=f"skip the {step} step"
        )
    ap.add_argument("--limit", type=int, default=10, help="documents per batch")
    ap.add_argument(
        "--workers", type=int, default=3, help="extraction workers (served model)"
    )
    ap.add_argument(
        "--also",
        type=Path,
        action="append",
        default=[],
        help="a local drop folder to upload",
    )
    ap.add_argument(
        "--domains", help="comma-separated domains for uploaded files that name none"
    )
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    steps = tuple(
        s.strip()
        for s in a.steps.split(",")
        if s.strip() in worker.STEPS and not getattr(a, f"no_{s.strip()}")
    )
    door = worker.Door(a.door, token=a.token)
    try:
        door.get_json("/health")
    except Exception as exc:  # noqa: BLE001
        print(f"no door at {a.door}: {exc}", file=sys.stderr)
        return 2
    folders = list(a.also) or inbox.browser_drop_folders()
    doms = [d.strip() for d in (a.domains or "").split(",") if d.strip()] or None
    say = None if a.quiet else (lambda t: print(t, flush=True))
    print(
        f"worker {door.name} -> {a.door}; steps {', '.join(steps)}; scope {a.scope}",
        file=sys.stderr,
    )
    try:
        worker.watch(
            door,
            interval=a.interval,
            steps=steps,
            scope=a.scope,
            limit=a.limit,
            workers=a.workers,
            folders=folders,
            domains=doms,
            spend=a.spend,
            log_=say,
            once=not a.watch,
        )
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
