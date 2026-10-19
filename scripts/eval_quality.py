#!/usr/bin/env python
"""What the checks of a text (``prax.parsers.quality``, stage AO) find over
a library, and what a change to one of their thresholds would move: the
measurement behind ``docs/eval/quality-checks-2026-10-18.md``, repeatable.

    python scripts/eval_quality.py [--db PATH]          # counts per check
    python scripts/eval_quality.py --sample 12 --seed 3 # ids to read by hand
    python scripts/eval_quality.py --sweep SHORT=0.3,0.5,0.7
    python scripts/eval_quality.py --save run.json      # then, after a change,
    python scripts/eval_quality.py --diff run.json      # what moved

Each text is checked as the door checks it (``quality.assess``), with the
PDF profile the store keeps (``meta.profile``). A PDF without one is left
out and counted, unless ``--profile``, which reads its original as the
nightly pass would (38 minutes for 11,493 PDFs on the desktop); nothing is
kept. ``--sweep`` sets one of ``SCANNED``, ``MATHS_PAGES``,
``DEBRIS_LINES`` or ``SHORT`` to each value in turn and counts each check
at each, with how many documents enter and leave the check against the
threshold as it stands; ``--sample`` then names ids from what moved.

Ids and counts only, never a title or a piece of text: the library holds
personal documents (the precision is read by hand, in the document view).
Opens the database read-only (``mode=ro``), like the other measurement
scripts; writes nothing to the store.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config
from prax.parsers import quality

SWEEPABLE = ("SCANNED", "MATHS_PAGES", "DEBRIS_LINES", "SHORT")


def _texts(con: sqlite3.Connection, archive: Path) -> list[dict[str, Any]]:
    """Every live document with a text: its text, mime, original's hash and
    meta, read once (a sweep checks the same texts again)."""
    out = []
    for r in con.execute(
        "SELECT id, hash, mime, text_hash, meta FROM documents"
        " WHERE text_hash IS NOT NULL AND json_extract(meta, '$.retired') IS NULL"
        " ORDER BY id"
    ):
        path = archive / r[3][:2] / r[3]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.append(
            {
                "id": r[0],
                "hash": r[1],
                "mime": r[2],
                "text": text,
                "meta": json.loads(r[4] or "{}"),
            }
        )
    return out


def _run(
    docs: list[dict[str, Any]], archive: Path, profile: bool
) -> tuple[dict[str, set[int]], int]:
    """The ids each check finds, and how many PDFs were left out for want
    of a kept profile."""
    found: dict[str, set[int]] = defaultdict(set)
    unprofiled = 0
    for d in docs:
        kept = d["meta"].get("profile")
        if d["mime"] == "application/pdf" and not kept and not profile:
            unprofiled += 1
            continue

        def read(h: str = d["hash"]) -> bytes | None:
            try:
                return (archive / h[:2] / h).read_bytes()
            except OSError:
                return None

        got = quality.assess(
            d["text"],
            stamp=str(d["meta"].get("text_source") or ""),
            mime=d["mime"],
            original=d["hash"],
            meta=d["meta"],
            read=read,
        )
        if got.keep is not None:  # profiled here: kept for the sweep's next runs
            d["meta"] = {**d["meta"], "profile": got.keep}
        for f in got.findings:
            found[f.check].add(int(d["id"]))
    return found, unprofiled


def _counts(found: dict[str, set[int]]) -> dict[str, int]:
    return {k: len(v) for k, v in sorted(found.items())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, default=None, help="the database (the host's)")
    ap.add_argument(
        "--profile", action="store_true", help="profile PDFs without a kept profile"
    )
    ap.add_argument(
        "--sweep", metavar="NAME=V1,V2", help=f"one of {', '.join(SWEEPABLE)}"
    )
    ap.add_argument("--sample", type=int, default=0, metavar="N", help="ids per check")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--save", type=Path, help="keep the ids per check")
    ap.add_argument("--diff", type=Path, help="what moved since a saved run")
    args = ap.parse_args()
    db = args.db or config.db_path()
    archive = db.parent / "archive"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    docs = _texts(con, archive)
    con.close()
    found, unprofiled = _run(docs, archive, args.profile)
    print(f"{len(docs)} texts, checks version {quality.VERSION}", end="")
    print(
        f"; {unprofiled} PDFs without a kept profile left out (--profile)"
        if unprofiled
        else ""
    )
    for check, n in _counts(found).items():
        print(f"  {check:<16} {n:6}")
    rng = random.Random(args.seed)

    def sample(ids: set[int]) -> str:
        picked = rng.sample(sorted(ids), min(args.sample, len(ids)))
        return " ".join(map(str, sorted(picked)))

    if args.sample and not args.sweep:
        for check, ids in sorted(found.items()):
            print(f"  {check}: {sample(ids)}")
    if args.save:
        args.save.write_text(json.dumps({k: sorted(v) for k, v in found.items()}))
    if args.diff:
        old = {k: set(v) for k, v in json.loads(args.diff.read_text()).items()}
        for check in sorted(set(old) | set(found)):
            came, went = (
                found.get(check, set()) - old.get(check, set()),
                old.get(check, set()) - found.get(check, set()),
            )
            if came or went:
                print(f"  {check}: +{len(came)} -{len(went)}")
                if args.sample:
                    print(f"    came {sample(came)}\n    went {sample(went)}")
    if args.sweep:
        name, _, values = args.sweep.partition("=")
        if name not in SWEEPABLE or not values:
            ap.error(
                f"--sweep takes NAME=V1,V2 with NAME one of {', '.join(SWEEPABLE)}"
            )
        now = getattr(quality, name)
        print(f"\n{name} (now {now}):")
        for raw in values.split(","):
            value = type(now)(float(raw)) if isinstance(now, int) else float(raw)
            setattr(quality, name, value)
            at, _ = _run(docs, archive, args.profile)
            setattr(quality, name, now)
            for check in sorted(set(at) | set(found)):
                came = at.get(check, set()) - found.get(check, set())
                went = found.get(check, set()) - at.get(check, set())
                if came or went or value == now:
                    n = len(at.get(check, ()))
                    print(
                        f"  {name}={value:<6} {check:<16} {n:6}"
                        f"  +{len(came)} -{len(went)}"
                    )
                    if args.sample and (came or went):
                        print(f"      came {sample(came)}  went {sample(went)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
