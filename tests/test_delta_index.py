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
    monkeypatch.setattr(store.retrieval, "DELTA_MERGE_AT", 1)
    store.ingest_text(con, "wave digital filter diode clipper " * 20, title="C")
    _embed_all(con)
    assert not delta.exists()


@needs_usearch
def test_a_vector_that_arrives_during_a_merge_is_kept(
    con: sqlite3.Connection, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The merge builds the new main file outside the index lock; a vector
    added to the delta meanwhile lands in a fresh delta, searchable."""
    from prax import vectors
    from prax.store import retrieval

    monkeypatch.setenv("PRAX_EMBED", "hash")
    a = store.ingest_text(con, "feedback delay network reverberation " * 20, title="A")[
        "doc_id"
    ]
    _embed_all(con)
    b = store.ingest_text(con, "granular cloud synthesis of textures " * 20, title="B")[
        "doc_id"
    ]
    _embed_all(con)
    delta = data_dir / "vectors-hash-test.delta.usearch"
    assert delta.exists()
    # while the build runs, a third document's vectors arrive in the delta
    real_save_to = vectors.VectorIndex.save_to
    arrived: list[int] = []

    def save_to_and_more(self: vectors.VectorIndex, path: Path) -> None:
        real_save_to(self, path)
        if not arrived:
            c = store.ingest_text(
                con, "wave digital filter diode clipper " * 20, title="C"
            )["doc_id"]
            arrived.append(c)
            _embed_all_no_save(con)

    monkeypatch.setattr(vectors.VectorIndex, "save_to", save_to_and_more)
    rep = retrieval._merge(data_dir / "vectors-hash-test.usearch")
    assert rep["merged"] >= 1 and rep["delta"] >= 1  # the latecomer, kept aside
    assert delta.exists()
    for q, doc in (
        ("granular cloud textures", b),
        ("feedback delay reverberation", a),
        ("wave digital diode clipper", arrived[0]),
    ):
        hits = store.search(con, q, mode="vec")
        assert hits and hits[0]["doc_id"] == doc, q


def _embed_all_no_save(con: sqlite3.Connection) -> None:
    emb = embeddings.current()
    assert emb is not None
    rows = store.pending_embeddings(con, emb.name)
    store.store_embeddings(
        con,
        [
            (r["chunk_id"], r["kind"], v)
            for r, v in zip(rows, emb.embed([r["text"] for r in rows]), strict=True)
        ],
        emb.name,
    )


@needs_usearch
def test_the_main_file_is_served_mapped_or_loaded(
    con: sqlite3.Connection, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``vectors.serve``: ``view`` maps the main file, ``memory`` loads it;
    a search answers the same either way, and the merge's fresh view
    honours the setting."""
    from prax.store import base

    monkeypatch.setenv("PRAX_EMBED", "hash")
    a = store.ingest_text(con, "feedback delay network reverberation " * 20, title="A")[
        "doc_id"
    ]
    _embed_all(con)
    mapped = store.search(con, "feedback delay reverberation", mode="vec")
    assert mapped and mapped[0]["doc_id"] == a
    main = data_dir / "vectors-hash-test.usearch"
    view = base._open_index(main, writable=False)
    assert view is not None
    mapped_bytes = view._index.memory_usage
    monkeypatch.setenv("PRAX_VEC_SERVE", "memory")
    base._drop_index_views("hash-test")  # the next read reopens under the setting
    loaded = store.search(con, "feedback delay reverberation", mode="vec")
    assert [h["chunk_id"] for h in loaded] == [h["chunk_id"] for h in mapped]
    idx = base._open_index(main, writable=False)
    assert idx is not None and idx is not view
    # a mapped index holds only its header in memory; a loaded one, the graph
    assert idx._index.memory_usage > 10 * mapped_bytes
