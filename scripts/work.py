#!/usr/bin/env python3
"""The worker for a cron line or a service unit that has no ``prax`` on its
PATH: ``prax work`` with the same arguments (docs/howto.md 3l).

    python scripts/work.py                          # one pass against the local door
    python scripts/work.py --watch                  # keep going (the usual way)
    python scripts/work.py --door http://board:8000 --watch
    python scripts/work.py --scope all --steps extract -n 20   # a backlog pass

It was a second copy of the command until 2026-09-30, and had drifted from
it (its own step list and batch size); now it is the command.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "clients" / "cli")]

from prax_cli.main import main

if __name__ == "__main__":
    sys.exit(main(["work", *sys.argv[1:]]))
