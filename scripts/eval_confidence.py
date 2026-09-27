#!/usr/bin/env python
"""Whether the local model's probability for "are these one thing?"
predicts the adjudicator's answer (docs/PLAN.md, "A confidence that was
measured, not written"; ``prax.calibration``).

Three steps, each resumable, into a directory of its own (never the
store's):

    python scripts/eval_confidence.py build OUT   # the labelled pairs, read-only
    python scripts/eval_confidence.py ask OUT     # the local model, one token a pair
    python scripts/eval_confidence.py score OUT   # calibration, raw and fitted
    python scripts/eval_confidence.py relabel OUT # Opus again, by pair number (paid)
    python scripts/eval_confidence.py score OUT --against relabel

**The labels are Opus 5's decisions of 2026-09-17**, not the truth. The
declines are exact: ``entity_candidates.decided = 'different'`` (2,704
of the 4,559 are left; later weekly runs replaced the rest with their
pairs). The merges were not recorded with their provenance: they
predate migration 25. They are reconstructed as the unstamped merges of
the likely-tier types whose names differ after normalization (the sure
tier merges equal ones) and whose name embeddings are 0.92 apart or
closer (what made a pair a candidate). Some come from other merge paths,
so the positives are the noisier side.

``ask`` puts each pair to the ``steps.extract`` server alone, in the
adjudicator's words, for one token with its top alternatives, and keeps
P(yes) (``calibration.yes_probability``). ``score`` fits Platt and
isotonic maps on half the pairs and measures Brier, ECE and the
reliability table on the other half, and says how many pairs a threshold
would settle without the paid model.

``relabel`` asks the adjudicator again, forty pairs a call in a shuffled
order, each answer naming its pair (``resolution.answers_by_number``),
and says how its answers differ from the recorded ones. The recorded
declines came from a list by position, padded when short.

Opens the database read-only (``mode=ro``), like the other measurement
scripts; writes nothing to the store.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import calibration as cal
from prax import config, models, resolution

QUESTION = (
    "Decide whether the two names refer to the same entity in a research"
    " library about audio and signal processing. Spelling variants,"
    " abbreviations and singular/plural are the same thing; different"
    " methods, people or concepts are not. Answer yes or no.\n\n"
)


def _ro() -> sqlite3.Connection:
    path = (config.data_dir() / "prax.db").as_posix()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def build(out: Path, threshold: float) -> None:
    con = _ro()
    pairs: list[dict[str, Any]] = []
    for r in con.execute(
        """
        SELECT c.a, c.b, c.type, ea.name AS an, eb.name AS bn, c.score
        FROM entity_candidates c
        JOIN entities ea ON ea.id = c.a JOIN entities eb ON eb.id = c.b
        WHERE c.decided = 'different' AND c.decided_by LIKE 'claude-opus%'
        """
    ):
        pairs.append(
            {
                "type": r["type"],
                "a": r["an"],
                "b": r["bn"],
                "label": 0,
                "cosine": round(float(r["score"]), 4),
            }
        )
    negatives = len(pairs)
    merged = [
        (r["type"], r["canon"], r["alias"])
        for r in con.execute(
            """
            SELECT a.type, b.name AS canon, a.name AS alias
            FROM entities a JOIN entities b ON b.id = a.canonical_id
            WHERE a.canonical_id IS NOT NULL AND a.merged_by IS NULL
              AND a.type = b.type
            """
        )
        if r["type"] in resolution.LIKELY_TYPES
        and resolution.normalize(r["canon"]) != resolution.normalize(r["alias"])
    ]
    from prax import embeddings

    emb = embeddings.current()
    if emb is None:
        raise SystemExit("no embedder configured: the positives need one")
    kept = 0
    for start in range(0, len(merged), 512):
        part = merged[start : start + 512]
        va = emb.embed([m[1] for m in part])
        vb = emb.embed([m[2] for m in part])
        for (etype, canon, alias), x, y in zip(part, va, vb, strict=True):
            cosine = float((x * y).sum())
            if cosine >= threshold:
                kept += 1
                pairs.append(
                    {
                        "type": etype,
                        "a": canon,
                        "b": alias,
                        "label": 1,
                        "cosine": round(cosine, 4),
                    }
                )
    for i, p in enumerate(pairs):
        p["id"] = i
    (out / "pairs.jsonl").write_text(
        "".join(json.dumps(p) + "\n" for p in pairs), encoding="utf-8"
    )
    print(
        f"{negatives} declines, {kept} of {len(merged)} reconstructed merges"
        f" at cosine >= {threshold}: {len(pairs)} pairs in {out / 'pairs.jsonl'}"
    )


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def ask(out: Path, workers: int) -> None:
    spec = models.resolve("extract")
    if spec is None or not spec.base_url:
        raise SystemExit("steps.extract names no served model")
    url = spec.base_url.rstrip("/") + "/chat/completions"
    pairs = _read(out / "pairs.jsonl")
    done = {a["id"] for a in _read(out / "answers.jsonl")}
    todo = [p for p in pairs if p["id"] not in done]
    print(f"{len(todo)} of {len(pairs)} pairs to ask", flush=True)
    lock = threading.Lock()
    started = time.time()
    count = [0]

    def one(p: dict[str, Any]) -> None:
        body = {
            "model": spec.model or spec.name,
            "messages": [
                {
                    "role": "user",
                    "content": QUESTION + f'[{p["type"]}] "{p["a"]}"  vs  "{p["b"]}"',
                }
            ],
            "max_tokens": 1,
            "temperature": 0,
            "logprobs": True,
            "top_logprobs": 10,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        for attempt in range(5):
            try:
                got = json.loads(urllib.request.urlopen(req, timeout=300).read())
                break
            except OSError:
                time.sleep(10 * (attempt + 1))
        else:
            return
        top = got["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        prob = cal.yes_probability([(t["token"], t["logprob"]) for t in top])
        line = json.dumps(
            {"id": p["id"], "p": prob, "top": [t["token"] for t in top[:4]]}
        )
        with lock:
            with (out / "answers.jsonl").open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            count[0] += 1
            if count[0] % 250 == 0:
                rate = count[0] / (time.time() - started)
                print(f"{count[0]} asked, {rate:.1f}/s", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))
    print(f"done: {count[0]} asked in {time.time() - started:.0f} s")


def relabel(out: Path, model: str) -> None:
    pairs = _read(out / "pairs.jsonl")
    done = {a["id"] for a in _read(out / "relabels.jsonl")}
    todo = [p for p in pairs if p["id"] not in done]
    random.Random(7).shuffle(todo)
    print(f"{len(todo)} of {len(pairs)} pairs to ask {model}", flush=True)
    judge = resolution.ClaudeAdjudicator(model=model)
    started = time.time()
    step = judge.batch * 5
    for start in range(0, len(todo), step):
        part = todo[start : start + step]
        same = judge.decide(
            [
                resolution.Candidate(0, 0, p["a"], p["b"], p["type"], "likely", 0.0)
                for p in part
            ]
        )
        with (out / "relabels.jsonl").open("a", encoding="utf-8") as f:
            for p, yes in zip(part, same, strict=True):
                f.write(json.dumps({"id": p["id"], "same": yes}) + "\n")
        print(
            f"{start + len(part)} asked, ${judge.cost:.2f},"
            f" {time.time() - started:.0f} s",
            flush=True,
        )
    compare(out)


def compare(out: Path) -> None:
    """The recorded labels against the ones asked again."""
    pairs = {p["id"]: p for p in _read(out / "pairs.jsonl")}
    table: dict[tuple[int, Any], int] = {}
    flipped: list[dict[str, Any]] = []
    for a in _read(out / "relabels.jsonl"):
        p = pairs[a["id"]]
        new = a["same"]
        key = (p["label"], None if new is None else int(new))
        table[key] = table.get(key, 0) + 1
        if new is not None and int(new) != p["label"]:
            flipped.append({**p, "now": new})
    print("\n| recorded | asked again: same | different | no answer |")
    print("|---|---|---|---|")
    for label, name in ((1, "same"), (0, "different")):
        print(
            f"| {name} | {table.get((label, 1), 0)} | {table.get((label, 0), 0)}"
            f" | {table.get((label, None), 0)} |"
        )
    random.Random(7).shuffle(flipped)
    for label, name in (
        (0, "recorded different, now same"),
        (1, "recorded same, now different"),
    ):
        some = [f for f in flipped if f["label"] == label][:15]
        print(f"\n{name} (a sample):\n")
        for f in some:
            print(f'- [{f["type"]}] "{f["a"]}" vs "{f["b"]}"')


def score(out: Path, bins: int, against: str = "recorded") -> None:
    pairs = {p["id"]: p for p in _read(out / "pairs.jsonl")}
    if against == "relabel":
        kept = {}
        for a in _read(out / "relabels.jsonl"):
            if a["same"] is not None:
                kept[a["id"]] = {**pairs[a["id"]], "label": int(a["same"])}
        pairs = kept
    rows = [
        (a["p"], pairs[a["id"]]["label"], pairs[a["id"]])
        for a in _read(out / "answers.jsonl")
        if a["id"] in pairs
    ]
    missing = sum(1 for p, _, _ in rows if p is None)
    rows = [r for r in rows if r[0] is not None]
    p = [r[0] for r in rows]
    y = [r[1] for r in rows]
    fit, test = cal.split(len(p))
    pf, yf = [p[i] for i in fit], [y[i] for i in fit]
    pt, yt = [p[i] for i in test], [y[i] for i in test]
    maps = {
        "raw": lambda v: v,
        "platt": cal.fit_platt(pf, yf),
        "isotonic": cal.fit_isotonic(pf, yf),
    }
    print(
        f"{len(rows)} pairs answered ({sum(y)} merged by Opus,"
        f" {len(y) - sum(y)} declined);"
        f" {missing} with neither yes nor no in the top tokens"
    )
    print(f"fitted on {len(fit)}, measured on {len(test)}\n")
    print("| map | Brier | ECE | agreement at 0.5 |\n|---|---|---|---|")
    for name, m in maps.items():
        q = [m(v) for v in pt]
        agree = sum(
            1 for qi, yi in zip(q, yt, strict=True) if (qi >= 0.5) == bool(yi)
        ) / len(yt)
        print(
            f"| {name} | {cal.brier(q, yt):.4f} | {cal.ece(q, yt, bins):.4f}"
            f" | {agree:.3f} |"
        )
    base = sum(yt) / len(yt)
    print(f"| always {base:.2f} | {cal.brier([base] * len(yt), yt):.4f} | — | — |")
    for name in ("raw", "isotonic"):
        m = maps[name]
        q = [m(v) for v in pt]
        print(
            f"\nreliability, {name}:\n\n| bin | pairs | said | happened |"
            "\n|---|---|---|---|"
        )
        for b in cal.reliability(q, yt, bins):
            print(
                f"| {b.low:.1f}–{b.high:.1f} | {b.count} | {b.said:.3f}"
                f" | {b.happened:.3f} |"
            )
    iso = maps["isotonic"]
    q = [iso(v) for v in pt]
    print(
        "\nwhat a threshold settles without the paid model (isotonic, measured half):\n"
    )
    print(
        "| settle when | settled | agree with Opus | left for Opus |\n|---|---|---|---|"
    )
    for cut in (0.99, 0.97, 0.95, 0.9, 0.8):
        sure = [
            (qi, yi) for qi, yi in zip(q, yt, strict=True) if qi >= cut or qi <= 1 - cut
        ]
        agree = sum(1 for qi, yi in sure if (qi >= 0.5) == bool(yi))
        share = len(sure) / len(yt)
        print(
            f"| p ≥ {cut} or ≤ {1 - cut:.2f} | {share:.1%} | "
            f"{(agree / len(sure) if sure else 0):.3f} | {1 - share:.1%} |"
        )
    worst = sorted(
        ((abs(iso(r[0]) - r[1]), r) for i, r in enumerate(rows) if i in set(test)),
        key=lambda t: -t[0],
    )[:12]
    print("\nthe strongest disagreements with Opus (measured half):\n")
    for _, (pi, yi, pair) in worst:
        print(
            f"- local {pi:.3f}, Opus {'same' if yi else 'different'}:"
            f' [{pair["type"]}] "{pair["a"]}" vs "{pair["b"]}"'
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=("build", "ask", "score", "relabel", "compare"))
    ap.add_argument("out", type=Path, help="a directory for the pairs and answers")
    ap.add_argument("--threshold", type=float, default=resolution.LIKELY_THRESHOLD)
    ap.add_argument(
        "--workers", type=int, default=2, help="requests at once (the server's slots)"
    )
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument(
        "--against",
        choices=("recorded", "relabel"),
        default="recorded",
        help="score against the recorded labels or the ones asked again",
    )
    ap.add_argument("--model", default="claude-opus-5", help="relabel's adjudicator")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # the tables print ≥
    a.out.mkdir(parents=True, exist_ok=True)
    if a.step == "build":
        build(a.out, a.threshold)
    elif a.step == "ask":
        ask(a.out, a.workers)
    elif a.step == "relabel":
        relabel(a.out, a.model)
    elif a.step == "compare":
        compare(a.out)
    else:
        score(a.out, a.bins, a.against)


if __name__ == "__main__":
    main()
