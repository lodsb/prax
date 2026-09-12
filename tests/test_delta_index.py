"""The delta vector index: new vectors land in a small writable file beside
the main one, a query sees both, a merge folds them together."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from prax import embeddings, store

needs_usearch = pytest.mark.skipif(
    not store.vectors_available(), reason="usearch not installed"
)


def _embed_all(con: sqlite3.Connection) -> int:
    emb = embeddings.current()
    assert emb is not None
    rows = store.pending_embeddings(con, emb.name)
    n = store.store_embeddings(
        con,
        [
            (r["chunk_id"], r["kind"], v)
            for r, v in zip(rows, emb.embed([r["text"] for r in rows]), strict=True)
        ],
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


@needs_usearch
def test_delta_then_merge(
    con: sqlite3.Connection, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_EMBED", "hash")
    a = store.ingest_text(con, "feedback delay network reverberation " * 20, title="A")[
        "doc_id"
    ]
    _embed_all(con)
    main = data_dir / "vectors-hash-test.usearch"
    delta = data_dir / "vectors-hash-test.delta.usearch"
    assert main.exists() and not delta.exists()  # the first save creates the main file
    first = store.vec_status(con)["index"]["count"]
    # a second document: its vectors go to the delta, the main file is untouched
    stamp = main.stat().st_mtime_ns
    b = store.ingest_text(con, "granular cloud synthesis of textures " * 20, title="B")[
        "doc_id"
    ]
    _embed_all(con)
    assert delta.exists() and main.stat().st_mtime_ns == stamp
    status = store.vec_status(con)
    assert status["delta"]["chunks"] >= 1 and status["index"]["count"] > first
    # a query finds both
    hits = store.search(con, "granular cloud textures", mode="vec")
    assert hits and hits[0]["doc_id"] == b
    hits = store.search(con, "feedback delay reverberation", mode="vec")
    assert hits and hits[0]["doc_id"] == a
    assert [
        d["doc_id"] for d in store.similar_documents(con, a)
    ]  # the delta serves lookups too
    # the merge folds the delta into the main file
    rep = store.merge_vectors("hash-test")
    assert rep["chunks"]["merged"] >= 1 and not delta.exists()
    assert store.vec_status(con)["delta"]["chunks"] == 0
    assert store.vec_status(con)["index"]["count"] == status["index"]["count"]
    hits = store.search(con, "granular cloud textures", mode="vec")
    assert hits and hits[0]["doc_id"] == b
    # a large delta merges by itself on save
    monkeypatch.setattr(store, "DELTA_MERGE_AT", 1)
    store.ingest_text(con, "wave digital filter diode clipper " * 20, title="C")
    _embed_all(con)
    assert not delta.exists()
