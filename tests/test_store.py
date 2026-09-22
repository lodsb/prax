"""prax.store: roundtrips, the two-step ingest, and regression tests for the
four bugs found in the Stage 0 skeleton (thread affinity, FTS syntax crash,
traverse off-by-one, chunk reassembly)."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from pathlib import Path

import pytest

from prax import store

LONG_TEXT = "".join(f"w{i} " for i in range(600))  # ~2900 chars, unique tokens


def _chain(con: sqlite3.Connection, *names: str) -> None:
    for a, b in pairwise(names):
        store.link(con, store.Edge(a, "concept", "extends", b, "concept"))


def _pairs(rows: list[dict]) -> set[tuple[str, str]]:
    return {(r["src"], r["dst"]) for r in rows}


# ------------------------------------------------------------- connection


def test_wal_and_foreign_keys_on(con: sqlite3.Connection) -> None:
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_store_usable_from_worker_thread(con: sqlite3.Connection) -> None:
    store.ingest_text(con, "threads share one connection", title="t")
    with ThreadPoolExecutor(max_workers=2) as pool:
        hits = pool.submit(store.search, con, "connection").result()
        ingested = pool.submit(store.ingest_text, con, "second from worker").result()
    assert hits and hits[0]["title"] == "t"
    assert ingested["created"]


def test_a_read_does_not_wait_for_the_writer(con: sqlite3.Connection) -> None:
    """The door's reads run on their own connections and WAL gives them a
    snapshot, so a search must not queue behind a write in progress —
    every read took the writers' lock until 2026-09-19, and "loading
    takes ages" was a seventeen-second scan holding it each worker cycle.
    Here a writer holds the lock for a second: the reads return at once,
    the next write waits its turn."""
    import threading
    import time

    from prax.store import base

    store.ingest_text(con, "granular synthesis of clouds " * 20, title="G")
    doc = store.ingest_text(con, "a second note about reverb " * 20, title="R")
    other = store.connect()  # a reader's own connection, as a request thread has
    held = threading.Event()
    release = threading.Event()

    def slow_write() -> None:
        with base._LOCK:
            held.set()
            release.wait(5)

    t = threading.Thread(target=slow_write)
    t.start()
    assert held.wait(2)
    t0 = time.monotonic()
    hits = store.search(other, "granular synthesis", 5)
    meta = store.get_meta(other, doc["doc_id"])
    chunks = store.list_chunks(other, doc["doc_id"])
    read_seconds = time.monotonic() - t0
    assert hits and hits[0]["title"] == "G" and meta is not None and chunks
    assert read_seconds < 0.5, f"reads waited {read_seconds:.2f} s for the writer"
    # a write does wait for the lock
    done = threading.Event()

    def write() -> None:
        store.retitle(con, doc["doc_id"], "Renamed", source="test")
        done.set()

    w = threading.Thread(target=write)
    w.start()
    assert not done.wait(0.3)  # still waiting for the writer
    release.set()
    t.join()
    assert done.wait(5)
    w.join()
    assert store.get_document(other, doc["doc_id"], max_chars=0)["title"] == "Renamed"
    other.close()


# ----------------------------------------------------------------- ingest


def test_ingest_search_roundtrip(con: sqlite3.Connection) -> None:
    r = store.ingest_text(
        con, "Granular synthesis smears transients.", title="granular note"
    )
    assert r["created"]
    hits = store.search(con, "granular")
    assert hits and hits[0]["doc_id"] == r["doc_id"]
    assert "[Granular]" in hits[0]["snippet"]


def test_duplicate_ingest_is_noop(con: sqlite3.Connection) -> None:
    a = store.ingest_text(con, "same content")
    b = store.ingest_text(con, "same content")
    assert a["doc_id"] == b["doc_id"] and not b["created"]
    assert con.execute("SELECT count(*) FROM documents").fetchone()[0] == 1


def test_archive_is_written_under_data_dir(
    con: sqlite3.Connection, data_dir: Path
) -> None:
    r = store.ingest_text(con, "archived bytes")
    path = data_dir / "archive" / r["hash"][:2] / r["hash"]
    assert path.read_bytes() == b"archived bytes"


def test_register_then_index(con: sqlite3.Connection) -> None:
    r = store.register(con, b"%PDF-1.4 fake", mime="application/pdf", title="p")
    assert r["created"]
    doc = store.get_document(con, r["doc_id"])
    assert doc is not None
    assert doc["parsed_at"] is None and doc["text_hash"] is None
    assert doc["text"] == "" and doc["text_len"] == 0
    assert store.search(con, "fake") == []
    assert con.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0

    idx = store.index_text(con, r["doc_id"], "Extracted text about vocoders.")
    assert idx["n_chunks"] == 1
    doc = store.get_document(con, r["doc_id"])
    assert doc is not None
    assert doc["parsed_at"] is not None and doc["text_hash"] == idx["text_hash"]
    assert doc["hash"] != doc["text_hash"]
    assert store.search(con, "vocoders")[0]["doc_id"] == r["doc_id"]


def test_register_dedupes_on_original_bytes(con: sqlite3.Connection) -> None:
    a = store.register(con, b"same pdf bytes", mime="application/pdf", title="a")
    b = store.register(con, b"same pdf bytes", mime="application/pdf", title="b")
    assert a["doc_id"] == b["doc_id"] and not b["created"]


def test_index_text_on_unknown_document_raises(con: sqlite3.Connection) -> None:
    with pytest.raises(KeyError):
        store.index_text(con, 999, "nothing")


def test_reindex_replaces_chunks(con: sqlite3.Connection) -> None:
    r = store.register(con, b"orig", mime="application/pdf")
    store.index_text(con, r["doc_id"], "first parse mentions alpha")
    store.index_text(con, r["doc_id"], "second parse mentions beta")
    assert store.search(con, "alpha") == []
    assert store.search(con, "beta")[0]["doc_id"] == r["doc_id"]
    assert con.execute("SELECT count(*) FROM chunks").fetchone()[0] == 1


def test_ingest_file_text_is_indexed_binary_is_deferred(
    con: sqlite3.Connection,
) -> None:
    md = store.ingest_file(con, b"# Notes\nabout granular", mime="text/markdown")
    assert store.search(con, "granular")[0]["doc_id"] == md["doc_id"]
    pdf = store.ingest_file(con, b"%PDF binary", mime="application/pdf")
    doc = store.get_document(con, pdf["doc_id"])
    assert doc is not None and doc["parsed_at"] is None


def test_ingest_file_with_supplied_text(con: sqlite3.Connection) -> None:
    r = store.ingest_file(
        con, b"%PDF binary", mime="application/pdf", text="parser output here"
    )
    assert store.search(con, "parser")[0]["doc_id"] == r["doc_id"]


def test_meta_roundtrips_as_dict(con: sqlite3.Connection) -> None:
    r = store.ingest_text(con, "meta test", meta={"zotero": {"key": "ABCD1234"}})
    doc = store.get_document(con, r["doc_id"])
    assert doc is not None and doc["meta"] == {"zotero": {"key": "ABCD1234"}}


# ------------------------------------------------------------------- get


def test_get_reassembles_long_text_exactly(con: sqlite3.Connection) -> None:
    r = store.ingest_text(con, LONG_TEXT, title="long")
    assert con.execute("SELECT count(*) FROM chunks").fetchone()[0] > 1
    doc = store.get_document(con, r["doc_id"])
    assert doc is not None
    assert doc["text"] == LONG_TEXT
    assert doc["text_len"] == len(LONG_TEXT) and not doc["truncated"]


def test_get_offset_and_max_chars(con: sqlite3.Connection) -> None:
    r = store.ingest_text(con, LONG_TEXT)
    doc = store.get_document(con, r["doc_id"], offset=100, max_chars=50)
    assert doc is not None
    assert doc["text"] == LONG_TEXT[100:150]
    assert doc["truncated"] and doc["offset"] == 100
    tail = store.get_document(con, r["doc_id"], offset=len(LONG_TEXT) - 5)
    assert tail is not None and tail["text"] == LONG_TEXT[-5:] and not tail["truncated"]


def test_get_missing_returns_none(con: sqlite3.Connection) -> None:
    assert store.get_document(con, 42) is None


# ----------------------------------------------------------------- chunks


def test_index_text_strips_nul_bytes(con: sqlite3.Connection) -> None:
    text = "page \x003\x005 marker\n\nreal words here"
    doc_id = store.ingest_text(con, text)["doc_id"]
    doc = store.get_document(con, doc_id)
    assert "\x00" not in doc["text"] and doc["text"].startswith("page 35 marker")
    assert store.search(con, "real words")[0]["doc_id"] == doc_id
    assert con.execute("SELECT min(length(text)) FROM chunks").fetchone()[0] > 0


def test_index_text_survives_lone_surrogates(con: sqlite3.Connection) -> None:
    doc_id = store.register(con, b"orig", mime="application/pdf")["doc_id"]
    store.index_text(con, doc_id, "broken font \ud83d glyph then text")
    doc = store.get_document(con, doc_id)
    assert doc["text"] == "broken font � glyph then text"
    assert store.search(con, "glyph")[0]["doc_id"] == doc_id


def test_chunk_windows() -> None:
    """The fixed-window fallback lives in prax.chunking now (long paragraphs)."""
    from prax import chunking

    assert chunking.windows("") == []
    assert chunking.windows("x" * chunking.WINDOW) == [(0, chunking.WINDOW)]
    chunks = [LONG_TEXT[a:b] for a, b in chunking.windows(LONG_TEXT)]
    joined = "".join(c[chunking.OVERLAP :] if i else c for i, c in enumerate(chunks))
    assert joined == LONG_TEXT
    assert chunks[1][: chunking.OVERLAP] == chunks[0][-chunking.OVERLAP :]
    assert all(len(c) <= chunking.WINDOW for c in chunks)


# ----------------------------------------------------------------- search


@pytest.mark.parametrize(
    "query",
    ["STFT-based", "note: vocoder", "vocoder's", "(vocoder)", 'say "vocoder"', "AND"],
)
def test_search_tolerates_punctuation_and_operators(
    con: sqlite3.Connection, query: str
) -> None:
    store.ingest_text(con, "The phase vocoder uses STFT-based analysis.", title="t")
    store.search(con, query)  # must not raise


def test_search_matches_through_punctuation(con: sqlite3.Connection) -> None:
    r = store.ingest_text(con, "The phase vocoder uses STFT-based analysis.")
    assert store.search(con, "STFT-based")[0]["doc_id"] == r["doc_id"]
    assert store.search(con, "phase, vocoder!")[0]["doc_id"] == r["doc_id"]


def test_search_ors_tokens_and_ranks_fuller_matches_first(
    con: sqlite3.Connection,
) -> None:
    both = store.ingest_text(con, "granular synthesis of textures")
    store.ingest_text(con, "granular materials in geology")
    hits = store.search(con, "granular synthesis nonexistentterm")
    assert hits[0]["doc_id"] == both["doc_id"]
    assert len(hits) == 2


def test_search_with_no_tokens_is_empty(con: sqlite3.Connection) -> None:
    store.ingest_text(con, "anything")
    assert store.search(con, "") == []
    assert store.search(con, "?!*") == []


def test_search_limit(con: sqlite3.Connection) -> None:
    for i in range(5):
        store.ingest_text(con, f"common word number {i}")
    assert len(store.search(con, "common", limit=2)) == 2


# ------------------------------------------------------------------ graph


def test_link_traverse_roundtrip(con: sqlite3.Connection) -> None:
    store.link(
        con, store.Edge("phase vocoder", "method", "implements", "STFT", "concept")
    )
    store.link(
        con, store.Edge("STFT", "concept", "extends", "Fourier transform", "concept")
    )
    one_hop = store.traverse(con, "phase vocoder", hops=1)
    assert any(e["dst"] == "STFT" for e in one_hop)
    two_hop = store.traverse(con, "phase vocoder", hops=2)
    assert any(e["dst"] == "Fourier transform" for e in two_hop)


def test_traverse_respects_hop_limit(con: sqlite3.Connection) -> None:
    _chain(con, "A", "B", "C", "D")
    assert _pairs(store.traverse(con, "A", hops=1)) == {("A", "B")}
    assert _pairs(store.traverse(con, "A", hops=2)) == {("A", "B"), ("B", "C")}
    assert _pairs(store.traverse(con, "A", hops=5)) == {("A", "B"), ("B", "C")}
    assert store.traverse(con, "A", hops=0) == []


def test_traverse_is_undirected_and_reports_hop(con: sqlite3.Connection) -> None:
    _chain(con, "A", "B", "C")
    rows = store.traverse(con, "C", hops=2)
    by_pair = {(r["src"], r["dst"]): r for r in rows}
    assert by_pair[("B", "C")]["hop"] == 1
    assert by_pair[("A", "B")]["hop"] == 2
    assert rows[0]["src_type"] == "concept" and rows[0]["rel"] == "extends"
    assert {"edge_id", "confidence", "source_doc"} <= rows[0].keys()


def test_traverse_ignores_invalidated_edges(con: sqlite3.Connection) -> None:
    _chain(con, "A", "B", "C")
    con.execute("UPDATE edges SET valid_to = '2026-01-01T00:00:00Z' WHERE src = 1")
    con.commit()
    assert _pairs(store.traverse(con, "A", hops=2)) == set()
    assert _pairs(store.traverse(con, "C", hops=2)) == {("B", "C")}


def test_traverse_unknown_entity_is_empty(con: sqlite3.Connection) -> None:
    assert store.traverse(con, "nobody") == []


def test_link_rejects_bad_confidence(con: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        store.link(con, store.Edge("a", "x", "r", "b", "x"), confidence="GUESS")


def test_link_records_provenance(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "evidence")
    store.link(
        con,
        store.Edge("paper X", "paper", "authored_by", "A. Author", "author"),
        source_doc=doc["doc_id"],
        ontology_version="0",
    )
    row = store.traverse(con, "paper X")[0]
    assert row["source_doc"] == doc["doc_id"] and row["confidence"] == "EXTRACTED"


def test_the_row_knows_its_text_length(con: sqlite3.Connection) -> None:
    """``index_text`` writes ``text_len``; ``get_document(max_chars=0)``
    answers from the row without opening the artifact; a row indexed
    before the column (NULL) is read once, and the ``lengths`` pass
    fills it."""
    from prax import config

    text = "héllo wörld " * 100
    doc = store.ingest_text(con, text, title="T")["doc_id"]
    row = con.execute("SELECT text_hash, text_len FROM documents WHERE id = ?", (doc,))
    text_hash, n = row.fetchone()
    assert n == len(text.strip()) or n == len(text)
    path = config.archive_dir() / text_hash[:2] / text_hash
    kept = path.read_bytes()
    path.unlink()  # no artifact: the row alone must do
    got = store.get_document(con, doc, max_chars=0)
    assert got["text"] == "" and got["text_len"] == n and got["truncated"] is True
    assert store.get_document(con, doc, offset=n, max_chars=0)["truncated"] is False
    path.write_bytes(kept)
    # a text from before the column: read from the artifact, then filled once
    con.execute("UPDATE documents SET text_len = NULL WHERE id = ?", (doc,))
    con.commit()
    assert store.get_document(con, doc, max_chars=0)["text_len"] == n
    assert store.fill_text_lengths(con) == 1
    assert (
        con.execute("SELECT text_len FROM documents WHERE id = ?", (doc,)).fetchone()[0]
        == n
    )
    assert store.fill_text_lengths(con) == 0
    assert store.maintain(con, only=["lengths"])["lengths"]["filled"] == 0
    # windows still read the artifact
    assert store.get_document(con, doc, max_chars=5)["text"] == text[:5]
