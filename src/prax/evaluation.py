"""Retrieval evaluation: a fixed query set with expected documents, scored
per search mode at document level.

The query set (``tests/eval/queries.yaml``) names expected documents by
Zotero key; keys resolve against the store at run time, so the same set
works on the fixture store the harness builds and on any store that holds
those documents. Metrics are computed over documents, not chunks: several
chunks of one document count as one hit at the rank of its best chunk.

* ``hit@1``, ``hit@3``: share of queries whose expected document appears at
  rank 1 / within the top 3;
* ``mrr``: mean reciprocal rank of the first expected document.

``build_fixture_store`` makes a throwaway store from the Zotero fixture:
import, parse (structure-aware Markdown), embed with the configured
embedder. ``evaluate`` runs the set; ``report`` renders a Markdown table.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from prax import config, embeddings, store
from prax.importers import zotero
from prax.parsers import queue

QUERIES = config.REPO_ROOT / "tests" / "eval" / "queries.yaml"
FIXTURE = config.REPO_ROOT / "tests" / "fixtures" / "zotero"
MODES = ("fts", "vec", "hybrid")


@dataclass
class Query:
    q: str
    expect: list[str] = field(default_factory=list)  # Zotero keys
    expect_title: list[str] = field(default_factory=list)  # regexes, case-insensitive
    style: str = "keyword"
    kind: str | None = None


@dataclass
class QueryResult:
    query: Query
    mode: str
    rank: int | None  # 1-based rank of the first expected doc, None if absent
    top: list[int]  # doc ids in rank order (deduplicated)


@dataclass
class ModeScore:
    mode: str
    n: int = 0
    hit1: int = 0
    hit3: int = 0
    rr: float = 0.0
    by_style: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    @property
    def mrr(self) -> float:
        return self.rr / self.n if self.n else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "n": self.n,
            "hit@1": self.hit1 / self.n if self.n else 0.0,
            "hit@3": self.hit3 / self.n if self.n else 0.0,
            "mrr": self.mrr,
            "mrr_by_style": {
                s: sum(v) / len(v) for s, v in sorted(self.by_style.items())
            },
        }


def load_queries(path: Path = QUERIES) -> list[Query]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Query(**q) for q in data["queries"]]


@dataclass
class Resolver:
    """Turns a query's expectations into document ids for one store."""

    keys: dict[str, int]
    titles: list[tuple[int, str]]

    @classmethod
    def for_store(cls, con: sqlite3.Connection) -> Resolver:
        titles = [
            (r["id"], r["title"])
            for r in con.execute(
                "SELECT id, title FROM documents WHERE title IS NOT NULL"
            )
        ]
        return cls(store.meta_index(con, "$.zotero.keys"), titles)

    def expected(self, query: Query) -> set[int]:
        ids = {self.keys[k] for k in query.expect if k in self.keys}
        for pattern in query.expect_title:
            rx = re.compile(pattern, re.IGNORECASE)
            ids.update(i for i, t in self.titles if rx.search(t))
        if not ids:
            wanted = query.expect or query.expect_title
            raise KeyError(f"nothing resolves for {wanted} in this store")
        return ids


def resolve_keys(con: sqlite3.Connection) -> dict[str, int]:
    """Zotero key -> document id, over every key the importer recorded."""
    return store.meta_index(con, "$.zotero.keys")


def run_query(
    con: sqlite3.Connection,
    query: Query,
    mode: str,
    resolver: Resolver | dict[str, int],
    depth: int,
) -> QueryResult:
    if isinstance(resolver, dict):
        resolver = Resolver(resolver, [])
    expected = resolver.expected(query)
    hits = store.search(con, query.q, depth, kind=query.kind, mode=mode)
    docs: list[int] = []
    for h in hits:
        if h["doc_id"] not in docs:
            docs.append(h["doc_id"])
    rank = next((i + 1 for i, d in enumerate(docs) if d in expected), None)
    return QueryResult(query, mode, rank, docs)


def evaluate(
    con: sqlite3.Connection,
    queries: list[Query],
    modes: tuple[str, ...] = MODES,
    *,
    depth: int = 10,
) -> tuple[list[ModeScore], list[QueryResult]]:
    resolver = Resolver.for_store(con)
    scores: list[ModeScore] = []
    results: list[QueryResult] = []
    for mode in modes:
        score = ModeScore(mode)
        for query in queries:
            try:
                r = run_query(con, query, mode, resolver, depth)
            except ValueError:  # e.g. mode=vec with no vectors
                continue
            results.append(r)
            score.n += 1
            rr = 1.0 / r.rank if r.rank else 0.0
            score.rr += rr
            score.hit1 += r.rank == 1
            score.hit3 += r.rank is not None and r.rank <= 3
            score.by_style[query.style].append(rr)
        if score.n:
            scores.append(score)
    return scores, results


# ----------------------------------------------------------- fixture store


def build_fixture_store(
    con: sqlite3.Connection, workdir: Path, *, embed: bool = True
) -> dict[str, Any]:
    """Import, parse and embed the Zotero fixture into ``con``'s store."""
    lib = zotero.open_library(FIXTURE, workdir)
    try:
        imported = zotero.run(lib, con)
    finally:
        lib.close()
    ids = store.select_documents(con, text_source_prefix="zotero-ft-cache")
    parsed = queue.run(con, ids)
    n_vec = 0
    emb = embeddings.current() if embed else None
    if emb is not None and store.has_vec(con):
        rows = store.pending_embeddings(con, emb.name)
        vectors = emb.embed([r["text"] for r in rows])
        n_vec = store.store_embeddings(
            con,
            [(r["chunk_id"], r["kind"], v) for r, v in zip(rows, vectors, strict=True)],
            emb.name,
        )
    return {
        "documents": dict(imported.actions),
        "parsed": dict(parsed.actions),
        "vectors": n_vec,
        "embedder": emb.name if emb else None,
    }


# ------------------------------------------------------------------ report


def report(
    scores: list[ModeScore], results: list[QueryResult], *, build: dict | None = None
) -> str:
    lines = ["# Retrieval eval", ""]
    if build:
        lines.append(
            f"Fixture store: {build['documents']} documents, parsed {build['parsed']},"
            f" {build['vectors']} vectors ({build['embedder']})."
        )
        lines.append("")
    lines += [
        "| mode | n | hit@1 | hit@3 | MRR | MRR by style |",
        "|---|---|---|---|---|---|",
    ]
    for s in scores:
        d = s.as_dict()
        styles = ", ".join(f"{k} {v:.2f}" for k, v in d["mrr_by_style"].items())
        lines.append(
            f"| {s.mode} | {s.n} | {d['hit@1']:.2f} | {d['hit@3']:.2f} | {d['mrr']:.2f}"
            f" | {styles} |"
        )
    misses = [r for r in results if r.rank is None or r.rank > 1]
    if misses:
        lines += [
            "",
            "Queries not at rank 1:",
            "",
            "| mode | rank | style | query |",
            "|---|---|---|---|",
        ]
        for r in misses:
            rank = r.rank if r.rank else "-"
            lines.append(f"| {r.mode} | {rank} | {r.query.style} | {r.query.q} |")
    return "\n".join(lines) + "\n"
