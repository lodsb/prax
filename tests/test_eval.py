"""The retrieval eval harness itself: it must build the fixture store, score
every mode, and hold a floor on keyword recall. Uses the hash embedder, so
the vector numbers here say nothing about the real model (that run is
recorded in docs/eval/)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from prax import embeddings, evaluation, parsers, store

needs_pymupdf = pytest.mark.skipif(
    not parsers.by_name("pymupdf4llm").available(), reason="pymupdf4llm not installed"
)


@pytest.fixture(autouse=True)
def hash_embedder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRAX_EMBED", "hash")
    embeddings._build.cache_clear()
    store._indexes.clear()
    yield
    for idx in list(store._indexes.values()):
        idx.close()
    store._indexes.clear()
    embeddings._build.cache_clear()


def test_query_set_loads_and_is_well_formed() -> None:
    queries = evaluation.load_queries()
    assert len(queries) >= 20
    assert all(q.expect and q.q for q in queries)
    assert {q.style for q in queries} == {"keyword", "paraphrase", "structure"}


@needs_pymupdf
def test_harness_scores_every_mode_and_keyword_floor(
    con: sqlite3.Connection, tmp_path: Path
) -> None:
    build = evaluation.build_fixture_store(con, tmp_path / "work")
    assert build["documents"]["created"] == 12 and build["parsed"]["upgraded"] >= 7
    queries = evaluation.load_queries()
    scores, results = evaluation.evaluate(con, queries)
    by_mode = {s.mode: s.as_dict() for s in scores}
    modes = {"fts", "hybrid"} | ({"vec"} if store.vectors_available() else set())
    assert set(by_mode) == modes
    assert all(s["n"] == len(queries) for s in by_mode.values())
    keyword = [r for r in results if r.mode == "fts" and r.query.style == "keyword"]
    assert sum(r.rank == 1 for r in keyword) / len(keyword) >= 0.8
    text = evaluation.report(scores, results, build=build)
    assert "| fts |" in text and "hit@1" in text


def test_unresolvable_expectation_is_an_error(con: sqlite3.Connection) -> None:
    store.ingest_text(con, "some text", title="t")
    q = evaluation.Query(q="some text", expect=["NOPE0000"])
    with pytest.raises(KeyError):
        evaluation.run_query(con, q, "fts", evaluation.resolve_keys(con), 10)
