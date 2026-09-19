#!/usr/bin/env python3
"""Ask the door a set of questions and score what it cites.

    python scripts/eval_ask.py --questions tests/eval/questions-equations.yaml
    python scripts/eval_ask.py --questions … --steps 0 --steps 8 \
        --judge server-35b --out docs/eval/ask-equations-2026-09-17.md

A client of the door (``PRAX_DOOR``, ``PRAX_TOKEN``): every question goes
through ``POST /ask`` once per steps setting, with the host's ask model
answering. Scored per question:

    sources   the expected document is among the passages the model was given
    cited     the answer cites the expected document
    formula   the answer cites a formula chunk (an equation, not prose about it)
    match     a cited passage of the expected document matches the expectation
    quoted    the answer text holds maths of its own ($…$)
    stated    a judge read the answer beside the paper's equation: does the
              answer state it (yes, partly, no)? --judge names a models:
              entry of the host's prax.yaml; a paid one is your choice

The first five are what a regex can see, and they saturate: an answer
that cites the equation and says "the passages do not state it" scores
as one that quotes it. The judge is the measure; the others its floor.
Its noise floor is a verdict or two per twenty-two asked once: --repeat
N asks each question N times and reports the mean over the runs, with
the spread between them (the lowest and highest run), so a change to
ask is judged against what the same ask does twice.

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

from prax import models
from prax.client import Door

MATHS = re.compile(r"\$[^$\n]+\$")

JUDGE = """You judge whether an answer states an equation. You are shown a question,
the equation(s) the paper gives for it (LaTeX, with a reading in words), and
an answer. Reply with one word:
  yes     the answer states the equation (the same relation, in LaTeX or in
          words precise enough to write it down; notation may differ)
  partly  the answer gives part of it, or a variant, or names its terms
          without the relation
  no      the answer does not state it (it describes, hedges, or states
          something else)
One word, nothing else."""

VERDICTS = ("yes", "partly", "no")


def expected_equations(door: Door, q: dict[str, Any], limit: int = 5) -> str:
    """The formula chunks of the expected document(s) that match the
    question's expectation, LaTeX and reading; the first few otherwise."""
    docs = q["expect_doc"] if isinstance(q["expect_doc"], list) else [q["expect_doc"]]
    expect = re.compile(q.get("expect") or ".", re.IGNORECASE)
    found: list[str] = []
    fallback: list[str] = []
    for doc_id in docs:
        try:
            chunks = door.get_json(f"/doc/{doc_id}/chunks")
        except Exception as exc:  # noqa: BLE001 - a missing document is no equation
            print(f"  doc {doc_id}: {exc}", flush=True)
            chunks = []
        for c in chunks:
            if c.get("kind") != "formula":
                continue
            text = c.get("text") or ""
            (found if expect.search(text) else fallback).append(text.strip())
    return "\n\n".join((found or fallback)[:limit])


def judge(runtime: Any, q: dict[str, Any], equations: str, answer: str) -> str:
    """One word from the judge, or ``?`` when it says something else."""
    user = (
        f"Question: {q['q']}\n\nThe paper's equation(s):\n{equations or '(none found)'}"
        f"\n\nThe answer:\n{answer}\n\nDoes the answer state the equation?"
    )
    try:
        text, _ = runtime.chat(JUDGE, user, max_tokens=8, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 - the judge's failure is a row, not the end
        return f"?{type(exc).__name__}"
    word = (text or "").strip().strip(".").lower().split()
    return word[0] if word and word[0] in VERDICTS else "?"


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
    door: Door,
    questions: list[dict[str, Any]],
    steps: int,
    judge_rt: Any | None = None,
    equations: dict[int, str] | None = None,
    repeat: int = 1,
) -> list[dict[str, Any]]:
    """Every question once per run, ``repeat`` runs, the rows in run
    order (run 1's twenty-two, then run 2's); each row says which."""
    out = []
    for r, (i, q) in (
        (r, iq) for r in range(1, repeat + 1) for iq in enumerate(questions, 1)
    ):
        if i == 1 and repeat > 1:
            print(f"-- run {r} of {repeat}", flush=True)
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
        row["run"] = r
        row["wall"] = round(time.monotonic() - t0, 1)
        if judge_rt is not None and "error" not in row:
            row["stated"] = judge(
                judge_rt, q, (equations or {}).get(i - 1, ""), row["answer"]
            )
        out.append(row)
        flag = "ok " if row.get("match") else ("src" if row.get("sources") else "-- ")
        verdict = f" {row['stated']:<6}" if "stated" in row else ""
        print(
            f"  [{steps} steps] {i:>2} {flag}{verdict} {row['wall']:>6}s"
            f"  {q['q'][:70]}",
            flush=True,
        )
    return out


def _runs(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The rows of one steps setting split by run (rows carry ``run``;
    an older save without it is one run)."""
    by_run: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_run.setdefault(int(r.get("run") or 1), []).append(r)
    return [by_run[k] for k in sorted(by_run)]


def _count(rows: list[dict[str, Any]], key: str, value: Any = True) -> int:
    return sum(1 for r in rows if "error" not in r and r.get(key) == value)


def _mean_cell(runs: list[list[dict[str, Any]]], key: str, value: Any = True) -> str:
    """The mean count over the runs as a share, the count, and the spread
    between the lowest and the highest run when there is more than one."""
    n = len(runs[0])
    counts = [_count(run, key, value) for run in runs]
    mean = sum(counts) / len(counts)
    cell = (
        f"{mean / n:.0%} ({mean:.1f})"
        if len(runs) > 1
        else f"{mean / n:.0%} ({counts[0]})"
    )
    if len(runs) > 1 and min(counts) != max(counts):
        cell += f" {min(counts)}–{max(counts)}"
    return cell


def _marks(r: dict[str, Any]) -> str:
    return (
        ("✓" if r["match"] else ("src" if r["sources"] else "—"))
        + (" f" if r["formula"] else "")
        + (" $" if r["quoted"] else "")
    )


def _question_cell(rows: list[dict[str, Any]]) -> str:
    """One question at one setting over its runs: the regex marks of the
    majority of runs, then the judge's yes count (and partly) over them."""
    ok = [r for r in rows if "error" not in r]
    if not ok:
        return "error"
    if len(rows) == 1:
        r = ok[0]
        cell = _marks(r)
        if r.get("stated") == "yes":
            cell += f" **{r['stated']}**"
        elif r.get("stated") in ("partly", "no"):
            cell += f" {r['stated']}"
        elif str(r.get("stated", "")).startswith("?"):
            cell += " ?"
        return cell
    half = len(ok) / 2
    majority = {
        "match": sum(1 for r in ok if r["match"]) > half,
        "sources": sum(1 for r in ok if r["sources"]) > half,
        "formula": sum(1 for r in ok if r["formula"]) > half,
        "quoted": sum(1 for r in ok if r["quoted"]) > half,
    }
    cell = _marks(majority)
    if any("stated" in r for r in ok):
        yes = sum(1 for r in ok if r.get("stated") == "yes")
        partly = sum(1 for r in ok if r.get("stated") == "partly")
        cell += f" **{yes}/{len(rows)}**" if yes > half else f" {yes}/{len(rows)}"
        if partly:
            cell += f" (+{partly})"
    return cell


def report(
    rows_by_steps: dict[int, list[dict[str, Any]]], model: str, judge_name: str = ""
) -> str:
    judged = bool(judge_name)
    head = "| steps | sources | cited | formula cited | match | maths quoted"
    if judged:
        head += " | stated (partly)"
    lines = [
        head + " | s/question |",
        "|---|---|---|---|---|---|" + "---|" * (2 if judged else 1),
    ]
    repeats = 1
    for steps, rows in rows_by_steps.items():
        runs = _runs(rows)
        repeats = max(repeats, len(runs))
        cells = " | ".join(
            _mean_cell(runs, key)
            for key in ("sources", "cited", "formula", "match", "quoted")
        )
        if judged:
            partly = sum(_count(run, "stated", "partly") for run in runs) / len(runs)
            cells += f" | {_mean_cell(runs, 'stated', 'yes')}, +{partly:.1f}"
        secs = sum(r["wall"] for r in rows) / len(rows)
        lines.append(f"| {steps} | {cells} | {secs:.0f} |")
    lines.append("")
    n_questions = len(_runs(next(iter(rows_by_steps.values())))[0])
    lines.append(
        f"Model: {model}. n = {n_questions} questions"
        + (
            f", each asked {repeats} times: the counts are means over the runs,"
            " the range the lowest and highest run."
            if repeats > 1
            else "."
        )
        + (f" Judge: {judge_name}." if judged else "")
    )
    lines.append("")
    lines.append(
        "| # | question | style | "
        + " | ".join(f"{s} steps" for s in rows_by_steps)
        + " |"
    )
    lines.append("|---|---|---|" + "---|" * len(rows_by_steps))
    first = _runs(next(iter(rows_by_steps.values())))[0]
    for i, q in enumerate(first):
        cells = []
        for rows in rows_by_steps.values():
            runs = _runs(rows)
            cells.append(_question_cell([run[i] for run in runs if i < len(run)]))
        lines.append(
            f"| {i + 1} | {q['q'][:80]} | {q.get('style')} | "
            + " | ".join(cells)
            + " |"
        )
    lines.append("")
    lines.append(
        "✓ a cited passage of the expected document matches; src: the document"
        " was among the sources but not cited so; f: a formula chunk cited;"
        " $: the answer quotes maths"
        + (
            "; **yes**/partly/no: the judge, does the answer state the equation"
            if judged
            else ""
        )
        + (
            "; k/N: the runs the judge said yes to, the marks those of most runs."
            if repeats > 1
            else "."
        )
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
    ap.add_argument(
        "--judge",
        metavar="MODEL",
        help="a models: entry of the host's prax.yaml that judges whether each"
        " answer states the equation (a paid one is your choice)",
    )
    ap.add_argument(
        "--rejudge",
        type=Path,
        metavar="JSON",
        help="judge the answers saved by an earlier --json instead of asking again",
    )
    ap.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="ask each question N times; the report averages the runs and"
        " shows their spread (the noise floor of a change)",
    )
    a = ap.parse_args()
    judge_rt = None
    if a.judge:
        spec = models.spec(a.judge)
        if spec is None:
            ap.error(f"no model named {a.judge!r} in {models.config_path()}")
        judge_rt = models.runtime(spec)
    door = Door(a.door, token=os.environ.get("PRAX_TOKEN") or None, name="eval-ask")
    questions = yaml.safe_load(a.questions.read_text(encoding="utf-8"))["questions"]
    described = door.get_json("/ask/config")
    model = str(described.get("runtime") or described.get("default") or "?")
    steps_list = a.steps or [0, int(described.get("steps", {}).get("default", 8))]
    equations = (
        {i: expected_equations(door, q) for i, q in enumerate(questions)}
        if judge_rt is not None
        else None
    )
    rows_by_steps: dict[int, list[dict[str, Any]]] = {}
    if a.rejudge:
        if judge_rt is None:
            ap.error("--rejudge needs --judge")
        saved = json.loads(a.rejudge.read_text(encoding="utf-8"))
        for steps_key, rows in saved.items():
            print(f"== {steps_key} steps (saved answers)", flush=True)
            for k, row in enumerate(rows):
                i = k % len(questions)  # the rows are runs of the questions
                q = questions[i]
                if "error" not in row:
                    row["stated"] = judge(
                        judge_rt, q, (equations or {}).get(i, ""), row["answer"]
                    )
                print(f"  {i + 1:>2} {row.get('stated', '-'):<6} {q['q'][:70]}")
            rows_by_steps[int(steps_key)] = rows
    for steps in [] if a.rejudge else steps_list:
        print(f"== {steps} steps", flush=True)
        rows_by_steps[steps] = run(
            door, questions, steps, judge_rt, equations, repeat=max(1, a.repeat)
        )
    text = report(rows_by_steps, model, a.judge or "")
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
