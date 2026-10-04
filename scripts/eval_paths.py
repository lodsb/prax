#!/usr/bin/env python
"""Whether the path index (``prax.graph.paths``, stage AM) finds sound
connections where there are some and offers none where there are not.

    python scripts/eval_paths.py [--db PATH] [--seed 11]

Pairs with a known connection:

- ``cites``: a library paper citing another (the references pass matched
  the citation to the document), the direct facts between them banned, so
  the path must be found another way;
- ``topic``: two documents linked from the same topic page, the pages
  banned as connectors.

Pairs with mostly none:

- ``recipe-paper``: a recipe and a research paper;
- ``random``: two random research papers (a focused library relates some
  of them; read those paths by hand before calling them false).

For each set, how many have a path at or under each cost, so the line
``paths.SOUND`` is read off the table, and the query time.

Opens the database read-only (``mode=ro``), like the other measurement
scripts; writes nothing to the store.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config
from prax.graph import paths
from prax.store.graph.paths import _rows

DOCUMENT_TYPES = {"paper", "document", "page", "recipe", "manual", "article", "thesis"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(config.data_dir() / "prax.db"))
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--pairs", type=int, default=150)
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{Path(args.db).as_posix()}?mode=ro", uri=True)
    rnd = random.Random(args.seed)
    hidden = frozenset(
        int(r[0])
        for r in con.execute("SELECT id FROM documents WHERE sensitivity IS NOT NULL")
    )
    t = time.time()
    ix = paths.build(_rows(con, as_of=None))
    print(
        f"index: {len(ix.ids)} entities, {len(ix.src)} facts, {time.time() - t:.1f} s"
    )
    types = dict(
        con.execute("SELECT id, type FROM entities WHERE canonical_id IS NULL")
    )

    def doc_entity(doc_id: int, title: str) -> int | None:
        row = con.execute(
            "SELECT COALESCE(s.canonical_id, s.id) FROM edges e"
            " JOIN entities s ON s.id = e.src WHERE e.source_doc = ? AND s.name = ?"
            " AND e.valid_to IS NULL LIMIT 1",
            (doc_id, title),
        ).fetchone()
        return int(row[0]) if row and row[0] in ix.node_of else None

    def degree(entity: int) -> int:
        return ix.degree(ix.node_of[entity])

    def direct(a: int, b: int) -> frozenset[int]:
        na, nb = ix.node_of[a], ix.node_of[b]
        return frozenset(
            int(ix.slot_fact[s])
            for s in range(ix.off[na], ix.off[na + 1])
            if ix.nbr[s] == nb
        )

    cites = [
        (int(ix.ids[ix.src[f]]), int(ix.ids[ix.dst[f]]))
        for f in range(len(ix.src))
        if ix.rels[ix.rel[f]] == "cites"
        and types.get(int(ix.ids[ix.src[f]])) in DOCUMENT_TYPES
        and types.get(int(ix.ids[ix.dst[f]])) in DOCUMENT_TYPES
        and ix.degree(int(ix.src[f])) >= 3
        and ix.degree(int(ix.dst[f])) >= 3
    ]
    cites = rnd.sample(cites, min(args.pairs, len(cites)))
    docs = con.execute(
        "SELECT id, title, json_extract(meta, '$.domains') FROM documents"
        " WHERE sensitivity IS NULL AND title IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    ).fetchall()
    kitchen = [
        (i, t) for i, t, d in docs if d and "kitchen" in d and "research" not in d
    ]
    research = [(i, t) for i, t, d in docs if d and d.strip('[]"') == "research"]
    rnd.shuffle(research)
    ke = [e for i, t in kitchen if (e := doc_entity(i, t)) and degree(e) >= 3]
    re_ = [e for i, t in research[:3000] if (e := doc_entity(i, t)) and degree(e) >= 3]
    sets: dict[str, list[tuple[int, int]]] = {"cites": cites}
    pages = {
        int(r[0])
        for r in con.execute(
            "SELECT COALESCE(canonical_id, id) FROM entities WHERE type = 'page'"
        )
    }
    page_facts = frozenset(
        int(ix.slot_fact[s])
        for p in pages
        if p in ix.node_of
        for s in range(ix.off[ix.node_of[p]], ix.off[ix.node_of[p] + 1])
    )
    topic: list[tuple[int, int]] = []
    for (pid,) in con.execute(
        "SELECT d.id FROM pages p JOIN documents d ON d.id = p.doc_id"
        " WHERE json_extract(d.meta, '$.page.kind') = 'topic'"
    ):
        linked = [
            int(r[0])
            for r in con.execute(
                "SELECT DISTINCT COALESCE(t.canonical_id, t.id) FROM edges e"
                " JOIN entities t ON t.id = e.dst"
                " WHERE e.source_doc = ? AND e.rel = 'annotates'"
                " AND e.valid_to IS NULL",
                (pid,),
            )
        ]
        linked = [x for x in linked if x in ix.node_of and degree(x) >= 2]
        pairs = [(x, y) for i, x in enumerate(linked) for y in linked[i + 1 :]]
        topic += rnd.sample(pairs, min(40, len(pairs)))
    sets["topic"] = topic
    if ke and re_:
        sets["recipe-paper"] = [
            (rnd.choice(ke), rnd.choice(re_)) for _ in range(args.pairs)
        ]
    if len(re_) > 1:
        sets["random"] = [tuple(rnd.sample(re_, 2)) for _ in range(2 * args.pairs)]  # type: ignore[misc]
    lines = (3, 4, 5, paths.SOUND, 7, 8, 10)
    print(
        f"\n{'set':<14}{'pairs':>6}{'found':>7}"
        + "".join(f"{'<=' + str(x):>8}" for x in lines)
        + f"{'ms p50':>8}"
    )
    for name, pairs in sets.items():
        costs, times = [], []
        for a, b in pairs:
            banned = (
                direct(a, b)
                if name == "cites"
                else page_facts
                if name == "topic"
                else frozenset()
            )
            t = time.time()
            got = paths.connect(ix, [a], [b], hidden=hidden, banned=banned, k=1)
            times.append((time.time() - t) * 1000)
            costs.append(got[0].cost if got else float("inf"))
        found = sum(c != float("inf") for c in costs)
        cells = "".join(f"{sum(c <= x for c in costs):>8}" for x in lines)
        print(
            f"{name:<14}{len(pairs):>6}{found:>7}{cells}{statistics.median(times):>8.1f}"
        )


if __name__ == "__main__":
    main()
