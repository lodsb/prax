"""Vector layer: the usearch index file, embedding bookkeeping, hybrid search.

Uses the deterministic hash embedder (``PRAX_EMBED=hash``) so nothing is
downloaded; the real ONNX model runs only with ``PRAX_TEST_EMBED=1``.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from prax import embeddings, store

needs_usearch = pytest.mark.skipif(
    not store.vectors_available(), reason="usearch not installed"
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
    n = store.store_embeddings(
        con,
        [(r["chunk_id"], r["kind"], v) for r, v in zip(rows, vectors, strict=True)],
        emb.name,
    )
    store.save_vectors(emb.name)
    fields = store.pending_document_embeddings(con, emb.name)
    if fields:
        store.store_document_embeddings(
            con,
            [
                (r["doc_id"], v)
                for r, v in zip(
                    fields, emb.embed([r["text"] for r in fields]), strict=True
                )
            ],
            emb.name,
        )
        store.save_document_vectors(emb.name)
    return n


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


# ------------------------------------------------------------------ index


@needs_usearch
def test_index_file_and_bookkeeping(con: sqlite3.Connection, data_dir: Path) -> None:
    assert store.schema_version(con) >= 3
    _load(con)
    pending = store.pending_embeddings(con, "hash-test")
    assert len(pending) == 3 and {p["kind"] for p in pending} == {"text", "table"}
    assert store.count_pending_embeddings(con, "hash-test") == 3
    assert _embed_all(con) == 3
    assert store.pending_embeddings(con, "hash-test") == []
    assert len(store.pending_embeddings(con, "other-model")) == 3  # model mismatch
    path = data_dir / "vectors-hash-test.usearch"
    assert path.exists() and not path.with_suffix(".usearch.tmp").exists()
    status = store.vec_status(con)
    assert status["available"] and status["rows"] == 3
    assert status["models"] == {"hash-test": 3} and status["embedder"] == "hash-test"
    assert status["index"]["count"] == 3 and status["index"]["dim"] == 384


@needs_usearch
def test_vector_and_hybrid_search(con: sqlite3.Connection) -> None:
    ids = _load(con)
    _embed_all(con)
    vec = store.search(con, "kalman pitch estimator", mode="vec")
    assert vec[0]["doc_id"] == ids["pitch"] and "kind" in vec[0]
    hybrid = store.search(con, "kalman pitch estimator")
    assert hybrid[0]["doc_id"] == ids["pitch"]
    assert hybrid[0]["fts_rank"] == 1 and hybrid[0]["vec_rank"] == 1
    assert hybrid[0]["score"] == pytest.approx(
        (3 + store.FIELD_WEIGHT) / (store.RRF_K + 1)
    )
    assert "[pitch]" in hybrid[0]["snippet"].lower()  # FTS snippet wins
    fts = store.search(con, "kalman pitch estimator", mode="fts")
    assert fts[0]["doc_id"] == ids["pitch"] and "fts_rank" not in fts[0]
    # kind filter is applied after the KNN over a wider candidate set
    tables = store.search(con, "cutoff lowpass", kind="table", mode="vec")
    assert tables and all(h["kind"] == "table" for h in tables)
    with pytest.raises(ValueError):
        store.search(con, "x", mode="sideways")


@needs_usearch
def test_similar_documents_by_centroid(con: sqlite3.Connection) -> None:
    ids = _load(con)
    _embed_all(con)
    twin = store.ingest_text(con, DOCS["pitch"] + " Again.", title="pitch twin")[
        "doc_id"
    ]
    _embed_all(con)
    similar = store.similar_documents(con, ids["pitch"], limit=3)
    assert similar[0]["doc_id"] == twin and similar[0]["score"] >= 1 / (store.RRF_K + 1)
    assert all(d["doc_id"] != ids["pitch"] for d in similar)
    assert store.document_context(con, ids["pitch"])["similar"][0]["doc_id"] == twin
    assert store.similar_documents(con, 999) == []


def test_document_field_follows_text_and_meta(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(
        con,
        "Sidechain notes. " * 5,
        title="1176sch.gif",
        meta={"creators": [{"name": "Ada"}], "zotero": {"kind": "note"}},
    )["doc_id"]
    field = store.document_field(con, doc)
    assert field.startswith("1176sch.gif\ntext note\nby Ada")
    row = con.execute(
        "SELECT field FROM documents_fts WHERE rowid = ?", (doc,)
    ).fetchone()
    assert row[0] == field
    _embed_all(con)
    assert store.count_pending_document_embeddings(con, "hash-test") == 0
    # unchanged metadata keeps the vector; a new summary drops it and rewrites
    meta = store.get_meta(con, doc)
    store.set_meta(con, doc, meta)
    assert store.count_pending_document_embeddings(con, "hash-test") == 0
    meta["summary"] = "A schematic of the UREI 1176 compressor."
    store.set_meta(con, doc, meta)
    assert store.count_pending_document_embeddings(con, "hash-test") == 1
    assert "schematic of the UREI 1176" in store.document_field(con, doc)
    assert store.document_field(con, 999) is None
    assert store.refresh_document_fields(con) == 0  # nothing changed since
    assert store.vec_status(con)["documents"] == {}  # the row was dropped


def test_document_field_ranks_identity_first(con: sqlite3.Connection) -> None:
    ids = _load(con)
    sch = store.ingest_text(
        con,
        "Schematic notes: the sidechain and the gain cell.",
        title="1176sch.gif",
        meta={"summary": "A schematic of the UREI 1176 compressor."},
    )["doc_id"]
    manual = store.ingest_text(
        con,
        "\n\n".join(
            "The schematic editor opens a schematic; every schematic sheet holds"
            " nets and a schematic frame."
            for _ in range(12)
        ),
        title="CAD manual",
    )["doc_id"]
    _embed_all(con)
    fts = store.search(con, "schematic", mode="fts")
    assert fts[0]["doc_id"] == manual  # chunk scoring rewards repetition
    hybrid = store.search(con, "schematic")
    assert hybrid[0]["doc_id"] == sch and hybrid[0]["field_rank"] == 1
    assert (
        hybrid[0]["chunk_id"] is not None
        and "[schematic]" in hybrid[0]["snippet"].lower()
    )
    assert manual in {h["doc_id"] for h in hybrid}
    assert {k for k in hybrid[0] if k.endswith("_rank")} == {
        "fts_rank",
        "vec_rank",
        "field_rank",
        "dvec_rank",
    }
    # doctype filters at the end; kind filters skip the field lists
    assert store.search(con, "schematic", doctype="image") == []
    texts = store.search(con, "schematic", doctype="text")
    assert texts and all(h["doc_id"] in (sch, manual, *ids.values()) for h in texts)
    with pytest.raises(ValueError):
        store.search(con, "schematic", doctype="pdf-ish")
    tables = store.search(con, "schematic", kind="text")
    assert all(h.get("field_rank") is None for h in tables)


def test_hybrid_fuses_per_document(con: sqlite3.Connection) -> None:
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
    hits = store.search(con, "reverberation delays")  # no index file yet
    assert hits[0]["doc_id"] == ids["reverb"] and "fts_rank" not in hits[0]
    monkeypatch.setenv("PRAX_EMBED", "0")
    embeddings._build.cache_clear()
    assert store.search(con, "reverberation delays")[0]["doc_id"] == ids["reverb"]
    with pytest.raises(ValueError, match="unavailable"):
        store.search(con, "x", mode="vec")


@needs_usearch
def test_reindex_leaves_stale_keys_that_queries_skip_and_compact_removes(
    con: sqlite3.Connection,
) -> None:
    ids = _load(con)
    _embed_all(con)
    old_chunk = con.execute(
        "SELECT id FROM chunks WHERE doc_id = ?", (ids["reverb"],)
    ).fetchone()[0]
    store.index_text(con, ids["reverb"], "Completely new text about granular clouds.")
    assert con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 2
    assert [p["text"] for p in store.pending_embeddings(con, "hash-test")] == [
        "Completely new text about granular clouds."
    ]
    # the stale key is still in the file but never returned
    hits = store.search(con, "feedback delay reverberation", mode="vec")
    assert old_chunk not in {h["chunk_id"] for h in hits}
    rec = store.compact_vectors(con, "hash-test")
    assert rec == {"removed_stale": 1, "forgot_missing": 0, "count": 2}
    _embed_all(con)
    assert store.vec_status(con)["index"]["count"] == 3


@needs_usearch
def test_compact_forgets_bookkeeping_without_vectors(con: sqlite3.Connection) -> None:
    """Bookkeeping rows committed after the last save (a crash) are dropped so
    the chunks get embedded again."""
    _load(con)
    emb = embeddings.current()
    rows = store.pending_embeddings(con, emb.name)
    store.store_embeddings(
        con,
        [
            (r["chunk_id"], r["kind"], v)
            for r, v in zip(rows, emb.embed([r["text"] for r in rows]), strict=True)
        ],
        emb.name,
    )
    store.save_vectors(emb.name)
    # a fourth document is embedded and booked, then the process dies before save
    store.ingest_text(con, "Late arrival about granular clouds.", title="late")
    rows = store.pending_embeddings(con, emb.name)
    store.store_embeddings(
        con,
        [(rows[0]["chunk_id"], rows[0]["kind"], emb.embed([rows[0]["text"]])[0])],
        emb.name,
    )
    for idx in list(store._indexes.values()):
        idx.close()
    store._indexes.clear()  # the unsaved in-memory index is gone
    assert store.count_pending_embeddings(con, emb.name) == 0  # booked, no vector
    rec = store.compact_vectors(con, emb.name)
    assert rec["forgot_missing"] == 1 and rec["count"] == 3
    assert store.count_pending_embeddings(con, emb.name) == 1  # embedded again


def test_store_embeddings_without_usearch_raises(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store.vectors, "available", lambda: False)
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
