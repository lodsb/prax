#!/usr/bin/env python3
"""The ``worlddates`` step on a sample, applying nothing (AL step 5).

    PRAX_WORLDDATES=server-27b-u python scripts/bench_world_dates.py --sample 30
    PRAX_WORLDDATES=server-27b-u python scripts/bench_world_dates.py --ids 9213 4479
    ... --out C:/prax-data/eval/world-dates.jsonl

Opens the store read-only. For each document it shows the sentences the
door would hand out, what the model proposed and what the checks kept,
with the live edges of the same fact and document beside them, so a
person (or the one measuring) can read whether each kept date is right.
A sample leaves out documents a person or a rule called personal.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, models
from prax.graph import lineformat, ontology, worlddates


def _texts(con: sqlite3.Connection, doc_id: int) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT text FROM chunks WHERE doc_id = ? AND kind = 'text' ORDER BY seq",
            (doc_id,),
        )
    ]


def _open(con: sqlite3.Connection) -> list[int]:
    return [
        r[0]
        for r in con.execute(
            "SELECT id FROM documents WHERE text_hash IS NOT NULL"
            " AND json_extract(meta, '$.retired') IS NULL"
            " AND coalesce(sensitivity, 'open') NOT IN ('personal', 'suspected')"
        )
    ]


def _live(con: sqlite3.Connection, doc_id: int, t: worlddates.Triple) -> list[str]:
    return [
        f"edge {r[0]} {r[1] or ''}..{r[2] or r[3] or ''}"
        for r in con.execute(
            "SELECT e.id, e.world_from, e.world_to, e.world_to_precision FROM edges e"
            " JOIN entities s ON s.id = e.src JOIN entities d ON d.id = e.dst"
            " WHERE s.name = ? AND e.rel = ? AND d.name = ? AND e.source_doc = ?"
            " AND e.valid_to IS NULL",
            (t.src, t.rel, t.dst, doc_id),
        )
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ids", type=int, nargs="*", default=[])
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    db = config.db_path().as_posix()
    con = sqlite3.connect(f"file:///{db.lstrip('/')}?mode=ro", uri=True)
    spec = models.resolve("worlddates")
    if spec is None:
        raise SystemExit("no model for the worlddates step (PRAX_WORLDDATES=…)")
    runtime = models.runtime(spec)
    onto = ontology.current()
    ids = list(a.ids)
    if a.sample:
        pool = _open(con)
        random.Random(a.seed).shuffle(pool)
        for d in pool:
            if len(ids) >= len(a.ids) + a.sample:
                break
            if d not in ids and worlddates.sentences(_texts(con, d)):
                ids.append(d)
    rows = []
    totals = {
        "documents": 0,
        "sentences": 0,
        "proposed": 0,
        "checked": 0,
        "kept": 0,
        "seconds": 0.0,
    }
    for doc_id in ids:
        said = worlddates.sentences(_texts(con, doc_id))
        title = (
            con.execute(
                "SELECT title FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
            or [""]
        )[0] or ""
        start = time.time()
        text, _usage = runtime.chat(
            worlddates.system_prompt(onto),
            worlddates.user_message(title, said),
            grammar=worlddates.grammar(onto),
            max_tokens=1200,
            temperature=0.0,
        )
        proposed = lineformat.parse(text).triples
        passed = worlddates.checked(list(proposed), said, onto)
        judged = [
            (
                t,
                worlddates.supported(
                    runtime,
                    worlddates.sentence_of(t, said),
                    worlddates.statement(t, onto),
                ),
            )
            for t in passed
        ]
        line = worlddates.supported_line()
        kept = [t for t, p in judged if p is not None and p >= line]
        totals["checked"] += len(passed)
        totals["documents"] += 1
        totals["sentences"] += len(said)
        totals["proposed"] += len(proposed)
        totals["kept"] += len(kept)
        took = time.time() - start
        totals["seconds"] += took
        row = {
            "doc_id": doc_id,
            "sentences": said,
            "answer": text,
            "kept": [
                {**worlddates.as_result(t), "live": _live(con, doc_id, t)} for t in kept
            ],
            "judged": [
                {
                    **worlddates.as_result(t),
                    "statement": worlddates.statement(t, onto),
                    "p": p,
                }
                for t, p in judged
            ],
            "seconds": round(took, 1),
        }
        rows.append(row)
        print(
            f"doc {doc_id}: {len(said)} sentences, {len(proposed)} proposed,"
            f" {len(passed)} checked, {len(kept)} kept, {took:.1f} s",
            flush=True,
        )
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        with a.out.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({k: round(v, 1) for k, v in totals.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
