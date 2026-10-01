#!/usr/bin/env python3
"""Ask the maths questions with and without the maths pack's calculator,
in grounded and in open mode, and keep every answer for scoring by hand
(docs/symbolic-maths.md, "How it is measured").

    python scripts/eval_maths.py --out docs/eval/maths-answers.json
    python scripts/eval_maths.py --only moog-tanh-forms --steps 8

A client of the door (``PRAX_DOOR``, ``PRAX_TOKEN``): each question goes
through ``POST /ask`` four times (``tools`` on and off, ``mode`` grounded
and open), with the host's ask model and ``--steps`` steps of surfing.
The door must run the maths pack (``packs:``), or "tools on" asks the
same as "off". Each answer is kept with its trail's ``maths:`` steps, its
citations and its time; ``--out`` is rewritten after every answer, so a
run that stops keeps what it had and a rerun skips what is there.

A regex (``expect``) gives a first reading of each answer; the score is a
person's (or a careful reader's) look at the answer beside ``expected``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax.client import Door

LOAD_TRIES = 10  # asks while the model loads, LOAD_WAIT_S apart
LOAD_WAIT_S = 60
WAYS = (
    ("on", "grounded"),
    ("off", "grounded"),
    ("on", "open"),
    ("off", "open"),
)


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument(
        "--questions", type=Path, default=Path("tests/eval/questions-maths.yaml")
    )
    ap.add_argument("--out", type=Path, default=Path("docs/eval/maths-answers.json"))
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--only", action="append", default=[], help="a question id")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    questions = yaml.safe_load(a.questions.read_text(encoding="utf-8"))["questions"]
    if a.only:
        questions = [q for q in questions if q["id"] in a.only]
    done: dict[str, Any] = {}
    if a.out.exists():
        done = json.loads(a.out.read_text(encoding="utf-8"))
    door = Door.from_env(name="eval-maths", timeout=1800.0)
    for q in questions:
        for tools, mode in WAYS:
            key = f"{q['id']}|{tools}|{mode}"
            if key in done and "error" not in done[key]:
                continue
            body = {
                "question": q["q"],
                "steps": a.steps,
                "mode": mode,
                "tools": tools == "on",
            }
            got: dict[str, Any] | None = None
            for _ in range(LOAD_TRIES):
                t0 = time.time()  # the answer's own time, not the load's
                try:
                    got = door.post_json("/ask", body)
                    break
                except Exception as exc:  # noqa: BLE001 - a failed ask is a row too
                    if "not loaded yet" in str(exc):
                        # llama-server gave the card back when idle; the ask
                        # asked for it again, and a load takes minutes
                        print(f"{key}: the model is loading, waiting", flush=True)
                        time.sleep(LOAD_WAIT_S)
                        continue
                    done[key] = {"error": str(exc)}
                    print(f"{key}: error {exc}", flush=True)
                    break
            if got is None:
                continue
            answer = str(got.get("answer") or "")
            trail = got.get("trail") or []
            maths = [
                {"arg": s.get("arg"), "result": s.get("result")}
                for s in trail
                if s.get("action") == "maths"
            ]
            done[key] = {
                "id": q["id"],
                "kind": q["kind"],
                "tools": tools,
                "mode": mode,
                "answer": answer,
                "maths": maths,
                "cited": sorted(
                    {
                        c.get("doc_id")
                        for c in got.get("citations") or []
                        if c.get("doc_id")
                    }
                ),
                "steps": len(trail),
                "seconds": round(time.time() - t0, 1),
                "regex": bool(re.search(q["expect"], answer, re.IGNORECASE)),
            }
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(
                json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8"
            )
            print(
                f"{key}: {done[key]['seconds']} s, {len(maths)} maths steps,"
                f" regex {'yes' if done[key]['regex'] else 'no'}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
