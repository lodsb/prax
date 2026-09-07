"""Vector layer: sqlite-vec table, embedding bookkeeping, hybrid search.

Uses the deterministic hash embedder (``PRAX_EMBED=hash``) so nothing is
downloaded; the real ONNX model runs only with ``PRAX_TEST_EMBED=1``.
"""

from __future__ import annotations

import os
import sqlite3

import numpy as np
import pytest

from prax import embeddings, store


@pytest.fixture(autouse=True)
def hash_embedder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRAX_EMBED", "hash")
    embeddings._build.cache_clear()
    yield
    embeddings._build.cache_clear()


def _needs_vec(con: sqlite3.Connection) -> None:
    if not store.has_vec(con):
        pytest.skip("sqlite-vec not loadable on this Python")


DOCS = {
    "reverb": "Feedback delay networks build artificial reverberation from delays.",
    "pitch": "Monophonic pitch tracking with an extended Kalman filter estimator.",
    "table": (
        "Table 2: filter cutoff\n\n| filter | cutoff |\n|---|---|\n| lowpass | 20 kHz |"
    ),
}


def _load(con: sqlite3.Connection) -> dict[str, int]:
    return {k: store.ingest_text(con, v, title=k)["doc_id"] for k, v in DOCS.items()}


def _embed_all(con: sqlite3.Connection) -> int:
    emb = embeddings.current()
    rows = store.pending_embeddings(con, emb.name)
    vectors = emb.embed([r["text"] for r in rows])
    return store.store_embeddings(
        con,
        [(r["chunk_id"], r["kind"], v) for r, v in zip(rows, vectors, strict=True)],
        emb.name,
    )


# --------------------------------------------------------------- embedder


def test_hash_embedder_is_deterministic_and_normalized() -> None:
    emb = embeddings.HashEmbedder()
    a, b = emb.embed(["reverb delay network", "reverb delay network"])
    assert np.allclose(a, b) and abs(float(np.linalg.norm(a)) - 1.0) < 1e-5
    far = emb.embed_query("unrelated words entirely")
    assert float(a @ far) < float(a @ emb.embed_query("reverb network"))
    assert emb.embed([]).shape == (0, 384)


def test_current_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAX_EMBED", "0")
    embeddings._build.cache_clear()
    assert embeddings.current() is None
    monkeypatch.setenv("PRAX_EMBED", "nope")
    embeddings._build.cache_clear()
    with pytest.raises(ValueError, match="unknown embedding model"):
        embeddings.current()
    monkeypatch.setenv("PRAX_EMBED", "bge-small-en-v1.5")
    embeddings._build.cache_clear()
    emb = embeddings.current()
    assert isinstance(emb, embeddings.OnnxEmbedder) and emb.dim == 384


# ------------------------------------------------------------------ store


def test_vec_table_and_bookkeeping(con: sqlite3.Connection) -> None:
    _needs_vec(con)
    assert store.schema_version(con) >= 3
    _load(con)
    pending = store.pending_embeddings(con, "hash-test")
    assert len(pending) == 3 and {p["kind"] for p in pending} == {"text", "table"}
    assert store.count_pending_embeddings(con, "hash-test") == 3
    assert _embed_all(con) == 3
    assert store.pending_embeddings(con, "hash-test") == []
    assert store.count_pending_embeddings(con, "hash-test") == 0
    assert len(store.pending_embeddings(con, "other-model")) == 3  # model mismatch
    status = store.vec_status(con)
    assert status == {
        "available": True,
        "rows": 3,
        "models": {"hash-test": 3},
        "embedder": "hash-test",
    }
    assert con.execute("SELECT count(*) FROM chunks_vec").fetchone()[0] == 3


def test_vector_and_hybrid_search(con: sqlite3.Connection) -> None:
    _needs_vec(con)
    ids = _load(con)
    _embed_all(con)
    vec = store.search(con, "kalman pitch estimator", mode="vec")
    assert vec[0]["doc_id"] == ids["pitch"] and "kind" in vec[0]
    hybrid = store.search(con, "kalman pitch estimator")
    assert hybrid[0]["doc_id"] == ids["pitch"]
    assert hybrid[0]["fts_rank"] == 1 and hybrid[0]["vec_rank"] == 1
    assert hybrid[0]["score"] == pytest.approx(2 / (store.RRF_K + 1))
    assert "[pitch]" in hybrid[0]["snippet"].lower()  # FTS snippet wins
    fts = store.search(con, "kalman pitch estimator", mode="fts")
    assert fts[0]["doc_id"] == ids["pitch"] and "fts_rank" not in fts[0]
    # vector-only recall: no shared token with FTS, still ranked by the model
    assert (
        store.search(con, "cutoff lowpass", kind="table", mode="vec")[0]["kind"]
        == "table"
    )
    with pytest.raises(ValueError):
        store.search(con, "x", mode="sideways")


def test_hybrid_fuses_per_document(con: sqlite3.Connection) -> None:
    _needs_vec(con)
    long = "\n\n".join(
        f"Kalman pitch tracking section {i}. " + "x " * 200 for i in range(6)
    )
    big = store.ingest_text(con, long, title="big")["doc_id"]
    small = store.ingest_text(con, "Kalman pitch tracking, one short note.", title="s")
    _embed_all(con)
    hits = store.search(con, "kalman pitch tracking", limit=10)
    docs = [h["doc_id"] for h in hits]
    assert len(docs) == len(set(docs)) and set(docs) == {big, small["doc_id"]}
    assert all(h["fts_rank"] is not None and h["vec_rank"] is not None for h in hits)
    assert all("[kalman]" in h["snippet"].lower() for h in hits)  # snippets filled
    chunk_hits = store.search(con, "kalman pitch tracking", limit=10, mode="fts")
    assert len(chunk_hits) > len(hits)  # fts mode stays chunk-level


def test_hybrid_degrades_to_fts_without_vectors(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _load(con)
    hits = store.search(con, "reverberation delays")  # nothing embedded yet
    assert hits[0]["doc_id"] == ids["reverb"] and "fts_rank" not in hits[0]
    monkeypatch.setenv("PRAX_EMBED", "0")
    embeddings._build.cache_clear()
    assert store.search(con, "reverberation delays")[0]["doc_id"] == ids["reverb"]
    with pytest.raises(ValueError, match="unavailable"):
        store.search(con, "x", mode="vec")


def test_reindex_drops_stale_vectors(con: sqlite3.Connection) -> None:
    _needs_vec(con)
    ids = _load(con)
    _embed_all(con)
    store.index_text(con, ids["reverb"], "Completely new text about granular clouds.")
    assert con.execute("SELECT count(*) FROM chunks_vec").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 2
    assert [p["text"] for p in store.pending_embeddings(con, "hash-test")] == [
        "Completely new text about granular clouds."
    ]
    live = {r[0] for r in con.execute("SELECT id FROM chunks")}
    stored = {r[0] for r in con.execute("SELECT chunk_id FROM chunks_vec")}
    assert stored <= live  # no orphan vectors


def test_store_embeddings_without_extension_raises(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(store._vec_loaded, id(con), False)
    with pytest.raises(RuntimeError):
        store.store_embeddings(con, [(1, "text", np.zeros(384))], "m")


@pytest.mark.skipif(
    not os.environ.get("PRAX_TEST_EMBED"), reason="PRAX_TEST_EMBED unset"
)
def test_real_model_embeds_and_ranks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAX_EMBED", "bge-small-en-v1.5")
    embeddings._build.cache_clear()
    emb = embeddings.current()
    vs = emb.embed(
        ["artificial reverberation with feedback delay networks", "tax invoice"]
    )
    q = emb.embed_query("how to build a reverb")
    assert vs.shape == (2, 384) and float(vs[0] @ q) > float(vs[1] @ q)
