#!/usr/bin/env python
"""Whether ``ask`` answers a question about a whole field better with the
region's summary in its bundle ("A way in", docs/PLAN.md).

    python scripts/eval_ask_regions.py [--out FILE]

A client of the door (``PRAX_DOOR``, ``PRAX_TOKEN``), like ``eval_ask.py``.
One question per named region of the library, "what does my library hold
on <its name>?", asked twice through ``POST /ask`` with no surfing steps:
without the region and with it (``regions``). Scored, without a judge:

    breadth   how many of the region's twenty heaviest members the answer
              names: an answer about a field should reach across it
    cited     how many passages the answer cites: the summary must not
              take the place of the evidence
    matched   with the region: whether the bundle's region is the one the
              question named

The breadth favours the summary, which names members. That is the question
asked: does the answer reach further with it, and does it still cite?
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax.client import Door

MEMBERS = 20


def breadth(answer: str, members: list[str]) -> float:
    said = answer.lower()
    return sum(1 for m in members if m.lower() in said) / max(1, len(members))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, help="the answers as JSON lines")
    ap.add_argument("--limit", type=int, default=0, help="only the first N regions")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    door = Door.from_env(timeout=900.0)
    regions = [
        c for c in door.get_json("/communities")["communities"] if c.get("label")
    ]
    if a.limit:
        regions = regions[: a.limit]
    rows: list[dict[str, Any]] = []
    for c in regions:
        whole = door.get_json(f"/communities/{c['id']}")
        members = [m["name"] for m in whole["members"][:MEMBERS]]
        question = f"What does my library hold on {c['label'].lower()}?"
        row: dict[str, Any] = {"id": c["id"], "label": c["label"], "question": question}
        for flag in (False, True):
            t0 = time.monotonic()
            got = door.post_json(
                "/ask", {"question": question, "steps": 0, "regions": flag}
            )
            answer = got.get("answer") or ""
            key = "with" if flag else "without"
            row[key] = {
                "breadth": round(breadth(answer, members), 3),
                "cited": len({c.get("n") for c in got.get("citations") or []}),
                "words": len(answer.split()),
                "seconds": round(time.monotonic() - t0, 1),
                "region": (got.get("region") or {}).get("id"),
                "answer": answer,
            }
        row["matched"] = row["with"]["region"] == c["id"]
        rows.append(row)
        print(
            f"{c['label'][:40]:40} breadth {row['without']['breadth']:.2f} ->"
            f" {row['with']['breadth']:.2f}, cited {row['without']['cited']} ->"
            f" {row['with']['cited']}, region {'right' if row['matched'] else 'other'}",
            flush=True,
        )
        if a.out:
            with a.out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    if not rows:
        return

    def mean(key: str, field: str) -> float:
        return sum(r[key][field] for r in rows) / len(rows)

    print(f"\n{len(rows)} regions asked about\n")
    print("| | without the region | with it |\n|---|---|---|")
    for field in ("breadth", "cited", "words", "seconds"):
        print(f"| {field} | {mean('without', field):.2f} | {mean('with', field):.2f} |")
    matched = sum(1 for r in rows if r["matched"])
    print(f"\nthe bundle's region was the one asked about for {matched} of {len(rows)}")
    wider = sum(1 for r in rows if r["with"]["breadth"] > r["without"]["breadth"])
    narrower = sum(1 for r in rows if r["with"]["breadth"] < r["without"]["breadth"])
    print(f"broader with it: {wider}; narrower: {narrower}")


if __name__ == "__main__":
    main()
