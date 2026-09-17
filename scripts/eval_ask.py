#!/usr/bin/env python3
"""Ask the door a set of questions and score what it cites.

    python scripts/eval_ask.py --questions tests/eval/questions-equations.yaml
    python scripts/eval_ask.py --questions … --steps 0 --steps 8 \
        --out docs/eval/ask-equations-2026-09-17.md

A client of the door (``PRAX_DOOR``, ``PRAX_TOKEN``): every question goes
through ``POST /ask`` once per steps setting, with the host's ask model
answering. Scored per question:

    sources   the expected document is among the passages the model was given
    cited     the answer cites the expected document
    formula   the answer cites a formula chunk (an equation, not prose about it)
    match     a cited passage of the expected document matches the expectation
    quoted    the answer text holds maths of its own ($…$)

The set names documents by id, so it is bound to one store (the full one
here); the retrieval sets in tests/eval name titles instead.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax.client import Door

MATHS = re.compile(r"\$[^$\n]+\$")


def score(q: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    expect_docs = (
        q["expect_doc"] if isinstance(q["expect_doc"], list) else [q["expect_doc"]]
    )
    expect = re.compile(q.get("expect") or ".", re.IGNORECASE)
    passages = result.get("passages") or []
    cited_n = {c["n"] for c in result.get("citations") or []}
    cited = [p for p in passages if p["n"] in cited_n]
    answer = result.get("answer") or ""
    in_sources = any(p["doc_id"] in expect_docs for p in passages)
    doc_cited = any(p["doc_id"] in expect_docs for p in cited)
    formula_cited = any(p.get("kind") == "formula" for p in cited)
    matched = any(
        p["doc_id"] in expect_docs and expect.search(p.get("text") or "") for p in cited
    )
    return {
        "sources": in_sources,
        "cited": doc_cited,
        "formula": formula_cited,
        "match": matched,
        "quoted": bool(MATHS.search(answer)),
        "cited_kinds": sorted({p.get("kind") or "?" for p in cited}),
        "seconds": result.get("seconds"),
        "steps_taken": result.get("steps"),
        "answer": answer,
    }


def run(
    door: Door, questions: list[dict[str, Any]], steps: int
) -> list[dict[str, Any]]:
    out = []
    for i, q in enumerate(questions, 1):
        t0 = time.monotonic()
        try:
            result = door.post_json(
                "/ask", {"question": q["q"], "steps": steps, "limit": 8}
            )
            row = score(q, result)
        except Exception as exc:  # noqa: BLE001 - one failure is a row, not the end
            row = {"error": f"{type(exc).__name__}: {exc}"}
        row.update(
            {"q": q["q"], "expect_doc": q["expect_doc"], "style": q.get("style")}
        )
        row["wall"] = round(time.monotonic() - t0, 1)
        out.append(row)
        flag = "ok " if row.get("match") else ("src" if row.get("sources") else "-- ")
        print(
            f"  [{steps} steps] {i:>2} {flag} {row['wall']:>6}s  {q['q'][:70]}",
            flush=True,
        )
    return out


def report(rows_by_steps: dict[int, list[dict[str, Any]]], model: str) -> str:
    head = "| steps | sources | cited | formula cited | match | maths quoted"
    lines = [head + " | s/question |", "|---|---|---|---|---|---|---|"]
    for steps, rows in rows_by_steps.items():
        n = len(rows)
        counts = {
            key: sum(1 for r in rows if "error" not in r and r.get(key))
            for key in ("sources", "cited", "formula", "match", "quoted")
        }
        cells = " | ".join(f"{c / n:.0%} ({c})" for c in counts.values())
        secs = sum(r["wall"] for r in rows) / n
        lines.append(f"| {steps} | {cells} | {secs:.0f} |")
    lines.append("")
    lines.append(
        f"Model: {model}. n = {len(next(iter(rows_by_steps.values())))} questions."
    )
    lines.append("")
    lines.append(
        "| # | question | style | "
        + " | ".join(f"{s} steps" for s in rows_by_steps)
        + " |"
    )
    lines.append("|---|---|---|" + "---|" * len(rows_by_steps))
    first = next(iter(rows_by_steps.values()))
    for i, q in enumerate(first):
        cells = []
        for rows in rows_by_steps.values():
            r = rows[i]
            if "error" in r:
                cells.append("error")
                continue
            cells.append(
                ("✓" if r["match"] else ("src" if r["sources"] else "—"))
                + (" f" if r["formula"] else "")
                + (" $" if r["quoted"] else "")
            )
        lines.append(
            f"| {i + 1} | {q['q'][:80]} | {q.get('style')} | "
            + " | ".join(cells)
            + " |"
        )
    lines.append("")
    lines.append(
        "✓ a cited passage of the expected document matches; src: the document"
        " was among the sources but not cited so; f: a formula chunk cited;"
        " $: the answer quotes maths."
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--questions", type=Path, required=True)
    ap.add_argument(
        "--steps",
        type=int,
        action="append",
        help="a steps setting (repeatable); default 0 and the host's",
    )
    ap.add_argument(
        "--door", default=os.environ.get("PRAX_DOOR", "http://127.0.0.1:8000")
    )
    ap.add_argument(
        "--out", type=Path, help="write the Markdown report here (appended)"
    )
    ap.add_argument("--json", type=Path, help="write every answer here")
    a = ap.parse_args()
    door = Door(a.door, token=os.environ.get("PRAX_TOKEN") or None, name="eval-ask")
    questions = yaml.safe_load(a.questions.read_text(encoding="utf-8"))["questions"]
    described = door.get_json("/ask/config")
    model = str(described.get("runtime") or described.get("default") or "?")
    steps_list = a.steps or [0, int(described.get("steps", {}).get("default", 8))]
    rows_by_steps: dict[int, list[dict[str, Any]]] = {}
    for steps in steps_list:
        print(f"== {steps} steps", flush=True)
        rows_by_steps[steps] = run(door, questions, steps)
    text = report(rows_by_steps, model)
    print("\n" + text)
    if a.json:
        a.json.write_text(
            json.dumps(rows_by_steps, indent=1, ensure_ascii=False), encoding="utf-8"
        )
    if a.out:
        with open(a.out, "a", encoding="utf-8") as f:
            f.write("\n" + text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
