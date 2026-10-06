#!/usr/bin/env python3
"""The follow-up set: does a second question's search gain from knowing
what the first was about?

    python scripts/eval_followups.py --store C:/prax-eval
    python scripts/eval_followups.py --store C:/prax-eval --out docs/eval/x.md

Each case (``tests/eval/followups-library.yaml``) is a first question and
a follow-up that leans on it. The arms, each over the same store:

- ``alone``: the follow-up as it was typed;
- ``joined``: what ``ask`` searches for (``ask.search_query``, the
  previous question's words added to a short or pointing follow-up).

A vote for the neighbourhood of what the first question was about was
measured with it and not shipped; its code is
``docs/eval/followups-focus-vote-2026-10-06.diff``, and with that applied
the arms it added come back (``--weights``).

Read only: run it against a copy of the store, never the live one.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SET = Path(__file__).resolve().parents[1] / "tests" / "eval" / "followups-library.yaml"


def rank_of(hits: list[Any], expected: set[int]) -> int | None:
    docs: list[int] = []
    for h in hits:
        if h["doc_id"] not in docs:
            docs.append(int(h["doc_id"]))
    return next((i + 1 for i, d in enumerate(docs) if d in expected), None)


def scores(ranks: list[int | None]) -> dict[str, float]:
    n = max(1, len(ranks))
    return {
        "hit@1": sum(r == 1 for r in ranks) / n,
        "hit@3": sum(r is not None and r <= 3 for r in ranks) / n,
        "hit@10": sum(r is not None for r in ranks) / n,
        "MRR": sum(1 / r for r in ranks if r) / n,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", type=Path, required=True, help="a copy's data dir")
    ap.add_argument("--cases", type=Path, default=SET)
    ap.add_argument(
        "--weights",
        type=float,
        nargs="+",
        default=[],
        help="the focus vote at these weights (needs the diff applied)",
    )
    ap.add_argument("--depth", type=int, default=10)
    ap.add_argument(
        "--max-docs", type=int, help="a focus stated in more documents is none"
    )
    ap.add_argument("--out", type=Path, help="write the Markdown report here")
    a = ap.parse_args()
    os.environ["PRAX_DATA_DIR"] = str(a.store)

    import yaml

    from prax import store
    from prax.answering import ask
    from prax.store.retrieval.knobs import knobs

    if a.weights and not hasattr(store, "focus_entities"):
        ap.error("--weights needs docs/eval/followups-focus-vote-2026-10-06.diff")
    if a.max_docs:
        from prax.store.retrieval import fusion

        fusion.FOCUS_MAX_DOCS = a.max_docs
    cases = yaml.safe_load(a.cases.read_text(encoding="utf-8"))["cases"]
    con = store.connect()
    titles = store.titled_documents(con)
    arms: dict[str, list[int | None]] = {"alone": [], "joined": []}
    for w in a.weights:
        arms[f"focus {w:g}"] = []
    rows: list[str] = []
    for c in cases:
        expected = {
            i
            for pattern in c["expect_title"]
            for i, t in titles
            if re.search(pattern, t, re.IGNORECASE)
        }
        if not expected:
            print(f"nothing resolves for {c['expect_title']}", file=sys.stderr)
            continue
        history = [{"question": c["first"], "answer": "-"}]
        joined = ask.search_query(c["then"], history)
        focus = store.focus_entities(con, c["first"]) if a.weights else []
        names = [
            str(r[0])
            for f in focus
            for r in con.execute("SELECT name FROM entities WHERE id = ?", (f,))
        ]
        if a.weights:
            knobs.FOCUS_WEIGHT = 0.0
        got = {
            "alone": rank_of(store.search(con, c["then"], a.depth), expected),
            "joined": rank_of(store.search(con, joined, a.depth), expected),
        }
        for w in a.weights:
            knobs.FOCUS_WEIGHT = w
            hits = store.search(con, joined, a.depth, focus=focus)
            got[f"focus {w:g}"] = rank_of(hits, expected)
        for arm, r in got.items():
            arms[arm].append(r)
        shown = " ".join(f"{r or '-':>3}" for r in got.values())
        about = ", ".join(names) or "—"
        rows.append(f"| {c.get('domain', '')} | {c['then']} | {about} | {shown} |")
    if a.weights:
        knobs.FOCUS_WEIGHT = 1.0
    head = "| arm | hit@1 | hit@3 | hit@10 | MRR |\n|---|---|---|---|---|"
    table = [head]
    for arm, ranks in arms.items():
        s = scores(ranks)
        table.append(
            f"| {arm} | {s['hit@1']:.2f} | {s['hit@3']:.2f} | {s['hit@10']:.2f}"
            f" | {s['MRR']:.3f} |"
        )
    n = len(arms["alone"])
    text = (
        f"{n} cases, depth {a.depth}\n\n"
        + "\n".join(table)
        + "\n\nRanks per case ("
        + ", ".join(arms)
        + "):\n\n| domain | follow-up | focus | ranks |\n|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n"
    )
    print(text)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
