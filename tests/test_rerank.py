"""Cross-encoder reranking: off by default, applied to the top hits when on."""

from __future__ import annotations

import os
import sqlite3

import numpy as np
import pytest

from prax import embeddings, evaluation, rerank, store


@pytest.fixture(autouse=True)
def stub_models(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRAX_EMBED", "hash")
    monkeypatch.delenv("PRAX_RERANK", raising=False)
    embeddings._build.cache_clear()
    rerank._build.cache_clear()
    store._indexes.clear()
    yield
    for idx in list(store._indexes.values()):
        idx.close()
    store._indexes.clear()
    rerank._build.cache_clear()
    embeddings._build.cache_clear()


def _load(con: sqlite3.Connection) -> dict[str, int]:
    docs = {
        "exact": "Extended Kalman filter pitch tracking, sample by sample.",
        "padded": "Kalman Kalman Kalman filter "
        + "x " * 300
        + "pitch tracking overview",
        "other": "Feedback delay networks for reverberation.",
    }
    return {k: store.ingest_text(con, v, title=k)["doc_id"] for k, v in docs.items()}


def test_off_by_default_and_explicit_without_model_raises(
    con: sqlite3.Connection,
) -> None:
    _load(con)
    assert rerank.current() is None
    hits = store.search(con, "kalman pitch tracking")
    assert "rerank_score" not in hits[0]
    with pytest.raises(ValueError, match="PRAX_RERANK"):
        store.search(con, "kalman pitch tracking", rerank=True)


def test_stub_reranker_reorders_top_hits(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _load(con)
    monkeypatch.setenv("PRAX_RERANK", "stub")
    rerank._build.cache_clear()
    plain = store.search(con, "kalman pitch tracking", rerank=False)
    assert {h["doc_id"] for h in plain[:2]} == {ids["exact"], ids["padded"]}
    reranked = store.search(con, "kalman pitch tracking")  # None follows the env
    assert reranked[0]["doc_id"] == ids["exact"]
    assert all("rerank_score" in h for h in reranked)
    scores = [h["rerank_score"] for h in reranked]
    assert scores == sorted(scores, reverse=True)
    assert len(reranked) <= 10  # limit still applies after rescoring


def test_stub_scores_and_registry() -> None:
    s = rerank.StubReranker().score("kalman filter", ["kalman filter", "delay"])
    assert s[0] > s[1] and s.dtype == np.float32
    assert rerank.StubReranker().score("q", []).shape == (0,)
    os.environ["PRAX_RERANK"] = "nope"
    rerank._build.cache_clear()
    with pytest.raises(ValueError, match="unknown reranker"):
        rerank.current()
    os.environ["PRAX_RERANK"] = "ms-marco-MiniLM-L-6-v2"
    rerank._build.cache_clear()
    assert isinstance(rerank.current(), rerank.OnnxReranker)


def test_eval_harness_scores_the_rerank_mode(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _load(con)
    monkeypatch.setenv("PRAX_RERANK", "stub")
    rerank._build.cache_clear()
    keys = {"K1": ids["exact"]}
    q = evaluation.Query(q="kalman pitch tracking", expect=["K1"])
    r = evaluation.run_query(con, q, evaluation.RERANK_MODE, keys, 10)
    assert r.rank == 1


@pytest.mark.skipif(
    not os.environ.get("PRAX_TEST_EMBED"), reason="PRAX_TEST_EMBED unset"
)
def test_real_cross_encoder_prefers_the_relevant_passage() -> None:
    os.environ["PRAX_RERANK"] = "ms-marco-MiniLM-L-6-v2"
    rerank._build.cache_clear()
    s = rerank.current().score(
        "how to build a reverb",
        ["Feedback delay networks build artificial reverberation.", "Invoice due."],
    )
    assert s[0] > s[1]


def test_server_reranker_posts_the_candidates_and_keeps_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_post(url: str, body: dict, timeout: float) -> dict:
        seen.update(url=url, body=body)
        return {
            "model": "bge-reranker-v2-m3",
            "results": [  # the server sorts by score; we want them back in order
                {"index": 2, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.4},
                {"index": 1, "relevance_score": -1.2},
            ],
        }

    monkeypatch.setattr(rerank, "post_json", fake_post)
    monkeypatch.setenv("PRAX_RERANK", "server")
    monkeypatch.setenv("PRAX_RERANK_URL", "http://gpu-box:8081/")
    rerank._build.cache_clear()
    r = rerank.current()
    assert isinstance(r, rerank.ServerReranker)
    scores = r.score("kalman", ["a", "b", "c"])
    assert scores.tolist() == pytest.approx([0.4, -1.2, 0.9])
    assert seen["url"] == "http://gpu-box:8081/rerank"
    assert seen["body"] == {"query": "kalman", "documents": ["a", "b", "c"], "top_n": 3}
    assert r.name == "server:bge-reranker-v2-m3"
    assert r.score("q", []).shape == (0,)
