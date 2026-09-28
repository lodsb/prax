#!/usr/bin/env python
"""Whether a query can be matched to its region of the library ("A way
in", docs/PLAN.md): the measurement ``prax.graph.regions`` waits on before
``search`` or ``ask`` may show a region.

    python scripts/eval_regions.py [--queries tests/eval/queries-library.yaml]

The right region of a query is where its expected documents live: the
region (and the part) most of their entities belong to, weighed by how
central each entity is to it. Two ways of finding it are scored:

- ``text``: the query's vector against each region's name, summary and
  heaviest members;
- ``hits``: where the query's search hits live, the same weighing over
  the entities of its first documents, with the share of the weight the
  best region holds as the confidence.

For each, how often its best region is the right one, as a function of
the score, so the threshold for showing a region is read off the table.

Opens the database read-only (``mode=ro``), like the other measurement
scripts; writes nothing to the store.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, evaluation, store
from prax.ml import embeddings

HITS = 5  # the search hits whose entities say where the answer lives
Row = tuple[float, bool, str]  # score, right, the query's style


# the text method, kept here as the comparison it lost (a region's name,
# summary and heaviest members against the query's vector)


@dataclass(frozen=True)
class Region:
    id: int
    level: int
    parent: int | None
    size: int
    label: str
    summary: str


def region_text(row: dict[str, Any]) -> str:
    """What a region is, as one text to embed."""
    members = ", ".join(row.get("members") or [])
    return f"{row['label']}. {row['summary']} It holds: {members}."


class RegionIndex:
    """The regions' vectors, and the best of them for a query vector."""

    def __init__(self, rows: list[dict[str, Any]], embedder: Any) -> None:
        self.regions = [
            Region(
                int(r["id"]),
                int(r["level"]),
                r.get("parent"),
                int(r["size"]),
                str(r["label"]),
                str(r["summary"]),
            )
            for r in rows
        ]
        self.vectors = (
            np.asarray(embedder.embed([region_text(r) for r in rows]), dtype=np.float32)
            if rows
            else np.zeros((0, 1), dtype=np.float32)
        )

    def match(
        self, query_vector: Any, *, level: int | None = None, k: int = 3
    ) -> list[tuple[Region, float]]:
        """The regions nearest the query, best first."""
        if not self.regions:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        scores = self.vectors @ q
        order = np.argsort(-scores)
        out: list[tuple[Region, float]] = []
        for i in order:
            region = self.regions[int(i)]
            if level is not None and region.level != level:
                continue
            out.append((region, float(scores[int(i)])))
            if len(out) >= k:
                break
        return out


def _ro() -> sqlite3.Connection:
    path = (config.data_dir() / "prax.db").as_posix()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def weights(con: sqlite3.Connection, docs: list[int]) -> dict[int, Counter[int]]:
    """How much of each region and part the entities of these documents
    make up, by their weight in it: {level: {community: weight}}."""
    out: dict[int, Counter[int]] = defaultdict(Counter)
    if not docs:
        return out
    marks = ",".join("?" * len(docs))
    for r in con.execute(
        "SELECT m.level, m.community_id, m.weight FROM edges e"
        " JOIN entities x ON x.id IN (e.src, e.dst)"
        " JOIN entity_communities m ON m.entity_id = coalesce(x.canonical_id, x.id)"
        f" WHERE e.valid_to IS NULL AND e.source_doc IN ({marks})",
        docs,
    ):
        out[int(r["level"])][int(r["community_id"])] += float(r["weight"])
    return out


def best(counts: Counter[int]) -> tuple[int | None, float]:
    """The community with the most weight, and its share of all of it."""
    if not counts:
        return None, 0.0
    cid, top = counts.most_common(1)[0]
    return cid, top / sum(counts.values())


def table(name: str, got: list[Row], cuts: tuple[float, ...]) -> None:
    right = sum(1 for _, ok, _ in got if ok)
    print(f"{name}: {len(got)} queries, best right {right / len(got):.2f}")
    for style in sorted({s for *_, s in got}):
        part = [ok for _, ok, st in got if st == style]
        print(f"   {style or '-'}: {sum(part)}/{len(part)} right")
    print("\n| shown when the score is at least | shown | right when shown |")
    print("|---|---|---|")
    for t in cuts:
        shown = [ok for sc, ok, _ in got if sc >= t]
        if shown:
            share = len(shown) / len(got)
            print(f"| {t:.2f} | {share:.0%} | {sum(shown) / len(shown):.2f} |")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--queries",
        type=Path,
        default=evaluation.QUERIES.with_name("queries-library.yaml"),
    )
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    con = _ro()
    emb = embeddings.current()
    if emb is None:
        raise SystemExit("no embedder configured")
    rows = store.regions_for_matching(con)
    index = RegionIndex(rows, emb)
    resolver = evaluation.Resolver.for_store(con)
    text: dict[int, list[Row]] = {0: [], 1: []}
    hits: dict[int, list[Row]] = {0: [], 1: []}
    for query in evaluation.load_queries(a.queries):
        try:
            docs = sorted(resolver.expected(query))
        except KeyError:
            continue
        right = {lvl: best(c)[0] for lvl, c in weights(con, docs).items()}
        vec = emb.embed_query(query.q)
        found = [int(h["doc_id"]) for h in store.search(con, query.q, HITS)]
        # what search shows (store.regions_of), with no threshold: the
        # table below is the threshold's measurement
        where = store.regions_of(con, found, share=0.0, part_share=0.0) or {}
        style = query.style or ""
        for lvl, key in ((0, "region"), (1, "part")):
            if right.get(lvl) is None:
                continue
            m = index.match(vec, level=lvl, k=1)
            if m:
                text[lvl].append((m[0][1], m[0][0].id == right[lvl], style))
            shown = where.get(key)
            if shown:
                hits[lvl].append((shown["share"], shown["id"] == right[lvl], style))
    print(f"{len(rows)} described regions and parts\n")
    for lvl, name in ((0, "regions"), (1, "parts")):
        table(f"text, {name}", text[lvl], (0.0, 0.6, 0.65, 0.7, 0.75))
        table(f"hits, {name}", hits[lvl], (0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9))


if __name__ == "__main__":
    main()
