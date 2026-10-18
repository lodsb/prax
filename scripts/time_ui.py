#!/usr/bin/env python
"""Time what the web UI asks the door for, the way a person's page does:
the page and its files, then the main views' requests, each a few times.

The web UI performance work of 2026-10-18. A page is slow when the door
answers slowly, and the door answers slowly when its Python is busy with a
worker's hand-out, so the numbers mean most with the worker running: take
them before and after a change, under the same load. A client of the door
like any (``PRAX_DOOR``, ``PRAX_TOKEN``); it reads, and writes nothing.

    python scripts/time_ui.py                  # each request 5 times
    python scripts/time_ui.py --rounds 10 --doc 1234
    python scripts/time_ui.py --json > before.json

Per request it prints the median and the slowest time, the bytes on the
wire and whether they came compressed.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax.client import Door

# the views' first requests, as the UI's scripts make them
VIEWS: list[tuple[str, str, dict[str, Any]]] = [
    ("poll", "/changes", {}),
    ("search", "/search", {"q": "granular synthesis", "limit": 20}),
    ("browse", "/documents", {"limit": 50}),
    ("jobs", "/jobs", {}),
    ("graph", "/graph/overview", {}),
    ("communities", "/communities/map", {}),
    ("text checks", "/documents/quality", {}),
]
# and an open document's, with ``{doc}`` filled in
DOCUMENT: list[tuple[str, str, dict[str, Any]]] = [
    ("doc poll", "/changes", {"doc": "{doc}"}),
    ("doc", "/get/{doc}", {"brief": "false"}),
    ("doc chunks", "/doc/{doc}/chunks", {}),
    ("doc context", "/doc/{doc}/context", {}),
]


def timed(door: Door, path: str, params: dict[str, Any]) -> tuple[float, int, bool]:
    """Seconds, bytes on the wire and whether they were compressed."""
    t = time.perf_counter()
    with door.client.stream(
        "GET", path, params=params, headers={**door.headers, "Accept-Encoding": "gzip"}
    ) as res:
        size = sum(len(b) for b in res.iter_raw())
        status = res.status_code
        gz = res.headers.get("content-encoding") == "gzip"
    took = time.perf_counter() - t
    if status >= 400:
        raise RuntimeError(f"{path}: HTTP {status}")
    return took, size, gz


def page_files(door: Door) -> list[str]:
    """The files the page names, as a browser would fetch them."""
    page = door.client.get("/ui/", headers=door.headers).text
    return ["/ui/" + n for n in re.findall(r'(?:src|href)="([^"#:]+)"', page)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--doc", type=int, help="the document to open (the newest)")
    ap.add_argument("--json", action="store_true", help="one JSON object, for a diff")
    args = ap.parse_args()
    door = Door.from_env(name="time_ui", timeout=300.0)
    doc = args.doc or door.get_json("/documents", {"limit": 1})["items"][0]["id"]

    rows: list[dict[str, Any]] = []

    def measure(name: str, path: str, params: dict[str, Any]) -> None:
        path = path.replace("{doc}", str(doc))
        params = {k: str(v).replace("{doc}", str(doc)) for k, v in params.items()}
        got = [timed(door, path, params) for _ in range(args.rounds)]
        times = [g[0] for g in got]
        rows.append(
            {
                "name": name,
                "path": path,
                "median_ms": round(statistics.median(times) * 1000, 1),
                "max_ms": round(max(times) * 1000, 1),
                "bytes": got[-1][1],
                "gzip": got[-1][2],
            }
        )

    files = page_files(door)
    t = time.perf_counter()
    sent = sum(timed(door, f, {})[1] for f in ["/ui/", *files])
    rows.append(
        {
            "name": f"page and {len(files)} files",
            "path": "/ui/",
            "median_ms": round((time.perf_counter() - t) * 1000, 1),
            "max_ms": None,
            "bytes": sent,
            "gzip": None,
        }
    )
    for name, path, params in [*VIEWS, *DOCUMENT]:
        try:
            measure(name, path, params)
        except Exception as exc:  # noqa: BLE001
            rows.append({"name": name, "path": path, "error": str(exc)})

    if args.json:
        print(json.dumps({"doc": doc, "rounds": args.rounds, "rows": rows}, indent=1))
        return 0
    print(f"door {door.base_url}, document {doc}, {args.rounds} rounds")
    for r in rows:
        if "error" in r:
            print(f"  {r['name']:<24} {r['error']}")
            continue
        slow = f"{r['max_ms']:8.0f}" if r["max_ms"] is not None else " " * 8
        gz = {True: "gzip", False: "", None: ""}[r["gzip"]]
        print(
            f"  {r['name']:<24} {r['median_ms']:8.0f} ms {slow} ms max"
            f" {r['bytes'] / 1024:8.1f} KB {gz}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
