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
    """The fixed-window fallback lives in prax.text.chunking now (long paragraphs)."""
    from prax.text import chunking

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
    # the second hop is a map of what is around, not more edges
    around = store.traverse_map(con, "phase vocoder", hops=2)
    assert [n["name"] for n in around["neighbours"]] == ["Fourier transform"]
    assert around["neighbours"][0]["via"] == ["extends"]


def test_traverse_respects_hop_limit(con: sqlite3.Connection) -> None:
    _chain(con, "A", "B", "C", "D")
    # traverse is the entity's own edges however many hops are asked for
    assert _pairs(store.traverse(con, "A", hops=1)) == {("A", "B")}
    assert _pairs(store.traverse(con, "A", hops=2)) == {("A", "B")}
    assert store.traverse(con, "A", hops=0) == []
    # the neighbourhood reaches one further, and no further than that
    assert [n["name"] for n in store.traverse_map(con, "A", hops=2)["neighbours"]] == [
        "C"
    ]
    far = store.traverse_map(con, "A", hops=5)
    assert far["hops"] == 2 and [n["name"] for n in far["neighbours"]] == ["C"]
    assert store.traverse_map(con, "A", hops=1)["neighbours"] == []


def test_traverse_is_undirected_and_reports_hop(con: sqlite3.Connection) -> None:
    _chain(con, "A", "B", "C")
    answer = store.traverse_map(con, "C", hops=2)
    rows = answer["edges"]
    # C is the far end of B -> C, so the walk goes backwards as well
    assert {(r["src"], r["dst"]): r["hop"] for r in rows} == {("B", "C"): 1}
    assert [n["name"] for n in answer["neighbours"]] == ["A"]
    assert rows[0]["src_type"] == "concept" and rows[0]["rel"] == "extends"
    assert {"edge_id", "confidence", "source_doc"} <= rows[0].keys()


def test_traverse_ignores_invalidated_edges(con: sqlite3.Connection) -> None:
    _chain(con, "A", "B", "C")
    con.execute("UPDATE edges SET valid_to = '2026-01-01T00:00:00Z' WHERE src = 1")
    con.commit()
    assert _pairs(store.traverse(con, "A", hops=2)) == set()
    assert _pairs(store.traverse(con, "C", hops=2)) == {("B", "C")}


def test_a_fact_says_when_it_holds_in_the_world(con: sqlite3.Connection) -> None:
    store.link(
        con,
        store.Edge("Ada", "person", "affiliated_with", "Lab", "organization"),
        world_from="2019-07",
        world_to="unknown",
    )
    store.link(con, store.Edge("Paper", "paper", "authored_by", "Ada", "person"))
    rows = {r["dst"]: r for r in store.traverse(con, "Ada")}
    held = rows["Lab"]
    assert (held["world_from"], held["world_from_precision"]) == ("2019-07", "month")
    # ended, at a date nobody gives
    assert "world_to" not in held and held["world_to_precision"] == "unknown"
    # a fact whose source gave no time says nothing about it
    paper = next(r for r in store.traverse(con, "Ada") if r["src"] == "Paper")
    assert not any(k.startswith("world_") for k in paper)
    with pytest.raises(ValueError, match="world_from"):
        store.link(
            con,
            store.Edge("Ada", "person", "affiliated_with", "Other", "organization"),
            world_from="sometime in spring",
        )


def test_a_walk_as_of_a_day_sees_what_prax_held_then(con: sqlite3.Connection) -> None:
    _chain(con, "A", "B", "C")
    # written in August; the A -> B edge ended on September 15
    con.execute("UPDATE edges SET valid_from = '2026-08-01T00:00:00Z'")
    con.execute("UPDATE edges SET valid_to = '2026-09-15T12:00:00Z' WHERE src = 1")
    con.commit()
    assert _pairs(store.traverse(con, "A")) == set()
    assert _pairs(store.traverse(con, "A", as_of="2026-09-01")) == {("A", "B")}
    assert _pairs(store.traverse(con, "A", as_of="2026-09-15T11:59:59Z")) == {
        ("A", "B")
    }
    # the end of the month is after the end of the edge
    assert _pairs(store.traverse(con, "A", as_of="2026-09")) == set()
    # before it was written, there was nothing
    assert _pairs(store.traverse(con, "A", as_of="2026-07")) == set()
    near = store.traverse_map(con, "A", hops=2, as_of="2026-09-01")
    assert [n["name"] for n in near["neighbours"]] == ["C"]
    with pytest.raises(ValueError, match="as_of"):
        store.traverse(con, "A", as_of="yesterday")


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


def test_adopt_vectors_is_the_way_back_from_a_model_change(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`chunk_embeddings` remembers one model per chunk, so re-embedding
    overwrites the record that the old model's vectors exist. The vectors
    do not go anywhere, so going back is bookkeeping (2026-09-25)."""
    pytest.importorskip("usearch")
    monkeypatch.setenv("PRAX_EMBED", "hash")
    doc = store.ingest_text(con, "wave digital filters and their practice " * 40)
    ids = [
        r[0]
        for r in con.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc["doc_id"],))
    ]
    assert ids
    import numpy as np

    old = "old-model"
    store.store_embeddings(
        con, [(i, "text", np.ones(384, dtype="float32")) for i in ids], old
    )
    store.save_vectors(old)
    assert {r[0] for r in con.execute("SELECT model FROM chunk_embeddings")} == {old}

    # a switch: the same chunks re-embedded under a new name
    new = "new-model"
    store.store_embeddings(
        con, [(i, "text", np.ones(384, dtype="float32")) for i in ids], new
    )
    assert {r[0] for r in con.execute("SELECT model FROM chunk_embeddings")} == {new}

    # and back, without recomputing anything
    got = store.adopt_vectors(con, old)
    assert got["chunks"] == len(ids)
    assert {r[0] for r in con.execute("SELECT model FROM chunk_embeddings")} == {old}


def test_adopt_vectors_claims_nothing_from_a_model_with_no_index(
    con: sqlite3.Connection,
) -> None:
    pytest.importorskip("usearch")
    assert store.adopt_vectors(con, "never-used") == {
        "chunks": 0,
        "documents": 0,
        "missing": 0,
    }


def test_a_failed_store_call_takes_back_its_writes_and_lets_the_lock_go(
    data_dir: Path,
) -> None:
    """A store function that fails half way (an IntegrityError after a
    write) is atomic: its write is undone and the connection holds no
    transaction, so another connection writes at once. Left open, the
    failed call's lock held every other writer of the door for 200 s a
    time, and the next commit saved its half (2026-09-30)."""
    import sqlite3

    from prax.store.base import _serialized

    a = store.connect()
    store.init_db(a)
    b = store.connect()
    b.execute("PRAGMA busy_timeout = 200")

    @_serialized
    def half(con: sqlite3.Connection) -> None:
        con.execute(
            "INSERT INTO acronyms (acronym, expansion, docs) VALUES ('X', 'x y', 1)"
        )
        con.execute(
            "INSERT INTO acronyms (acronym, expansion, docs) VALUES ('X', 'x y', 1)"
        )

    with pytest.raises(sqlite3.IntegrityError):
        half(a)
    assert not a.in_transaction
    assert a.execute("SELECT count(*) FROM acronyms").fetchone()[0] == 0
    b.execute("INSERT INTO acronyms (acronym, expansion, docs) VALUES ('Y', 'y z', 1)")
    b.commit()

    # work an earlier call left uncommitted survives a later failure
    @_serialized
    def pending(con: sqlite3.Connection) -> None:
        con.execute(
            "INSERT INTO acronyms (acronym, expansion, docs) VALUES ('P', 'p q', 1)"
        )

    pending(a)
    with pytest.raises(sqlite3.IntegrityError):
        half(a)
    a.commit()
    got = {r[0] for r in a.execute("SELECT acronym FROM acronyms")}
    assert got == {"Y", "P"}
    a.close()
    b.close()


def test_a_label_already_held_in_the_librarys_language_is_not_moved_onto(
    con: sqlite3.Connection,
) -> None:
    """``label_in_language``: an entity with its name as an English
    preferred label and again without a language (entity 179263) no
    longer fails the one-label-per-language index."""
    store.link(
        con,
        store.Edge("A manual", "paper", "about", "Authorization Wizard", "concept"),
    )
    eid = con.execute(
        "SELECT id FROM entities WHERE name = 'Authorization Wizard'"
    ).fetchone()[0]
    # the live entity's labels: its name as the English preferred one, and
    # again without a language (what resolution had written)
    con.execute("DELETE FROM entity_labels WHERE entity_id = ?", (eid,))
    con.executemany(
        "INSERT INTO entity_labels (entity_id, label, lang, kind, producer)"
        " VALUES (?, 'Authorization Wizard', ?, ?, ?)",
        [(eid, "en", "pref", "vocabulary"), (eid, None, "alt", "resolution")],
    )
    con.commit()
    assert (
        store.label_in_language(con, eid, "Autorisierungsassistent", lang="de")
        == "labelled"
    )
    labels = {
        (r[0], r[1])
        for r in con.execute(
            "SELECT label, lang FROM entity_labels WHERE entity_id = ?", (eid,)
        )
    }
    assert ("Autorisierungsassistent", "de") in labels


def test_a_changed_passage_may_hand_its_chunk_id_to_another(
    con: sqlite3.Connection,
) -> None:
    """A chunk id is not a passage's name. A re-index keeps the ids of
    unchanged chunks, deletes the changed ones and inserts their
    successors, and SQLite gives a new row the highest id plus one: a
    document's own newest chunks come back under the same ids with other
    text (2026-10-03, the first client's question about its links). A
    link to a passage says its words too (``#doc/N?chunk=M&find=…``), and
    the UI goes by the words when the chunk no longer holds them."""

    def para(word: str) -> str:
        return " ".join([word] * 400)

    text = f"# One\n\n{para('alpha')}\n\n# Two\n\n{para('gamma')}\n"
    doc = store.ingest_text(con, text, title="synced")["doc_id"]
    before = {c["chunk_id"]: c["text"] for c in store.list_chunks(con, doc)}
    store.index_text(con, doc, text.replace(para("gamma"), para("delta")))
    after = {c["chunk_id"]: c["text"] for c in store.list_chunks(con, doc)}
    kept = [i for i in before if "alpha" in before[i]]
    assert all(after[i] == before[i] for i in kept)  # unchanged: same id
    moved = [i for i in before if "gamma" in before[i]]
    assert moved and all(i in after and "delta" in after[i] for i in moved)


def test_core_four_moves_the_core_three_stamps(con: sqlite3.Connection) -> None:
    """Migration 39: a bump that only adds relations moves the extraction
    stamps instead of making the whole library due again."""
    from importlib import resources

    doc = store.ingest_text(con, "a document read under core three " * 20)["doc_id"]
    meta = store.get_meta(con, doc)
    meta["extraction"] = {"ontology_version": "core3+research9"}
    meta["extraction_history"] = [{"ontology_version": "core3"}]
    meta["note"] = "score3+ is not a version"
    store.set_meta(con, doc, meta)
    sql = (
        resources.files("prax.migrations")
        .joinpath("0039_core4.sql")
        .read_text(encoding="utf-8")
    )
    con.executescript(sql)
    got = store.get_meta(con, doc)
    assert got["extraction"]["ontology_version"] == "core4+research9"
    assert got["extraction_history"][0]["ontology_version"] == "core4"
    assert got["note"] == "score3+ is not a version"


def test_what_is_written_once_stays_as_written(con: sqlite3.Connection) -> None:
    """The database refuses what would change history (migration 35): an
    edge deleted or its fact changed, a page revision touched, a spend row
    rewritten. Ending an edge, and moving it to a duplicate's survivor,
    still work."""
    store.link(con, store.Edge("Paper A", "paper", "about", "onsets", "concept"))
    edge = con.execute("SELECT id FROM edges").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        con.execute("DELETE FROM edges WHERE id = ?", (edge,))
    with pytest.raises(sqlite3.IntegrityError, match="fact does not change"):
        con.execute("UPDATE edges SET rel = 'cites' WHERE id = ?", (edge,))
    con.execute(
        "UPDATE edges SET valid_to = '2026-10-03T00:00:00Z' WHERE id = ?", (edge,)
    )
    con.rollback()
    store.write_page(con, "kept", "# Kept\n\nOnce.", title="Kept")
    with pytest.raises(sqlite3.IntegrityError, match="history"):
        con.execute("UPDATE page_revisions SET note = 'rewritten'")
    with pytest.raises(sqlite3.IntegrityError, match="history"):
        con.execute("DELETE FROM page_revisions")
    store.record_spend(
        con, step="ask", model="claude-sonnet-5", usage={"input_tokens": 10}, usd=0.01
    )
    with pytest.raises(sqlite3.IntegrityError, match="ledger"):
        con.execute("UPDATE spend SET usd = 0")
    with pytest.raises(sqlite3.IntegrityError, match="ledger"):
        con.execute("DELETE FROM spend")


def test_the_languages_pass_outlives_a_label_with_a_twin(
    con: sqlite3.Connection,
) -> None:
    """A label without a language whose twin already has the one the
    pass would give it (same entity, same words) failed the whole pass
    every night on the unique index (2026-10-04); it keeps no language."""
    doc = store.ingest_text(con, "Der Apfel ist rot und süß. " * 20, title="Apfel")
    meta = store.get_meta(con, doc["doc_id"])
    meta["lang"] = "de"
    store.set_meta(con, doc["doc_id"], meta)
    store.link(
        con,
        store.Edge("Apfel", "document", "authored_by", "Bauer", "person"),
        source_doc=doc["doc_id"],
    )
    bauer = con.execute("SELECT id FROM entities WHERE name = 'Bauer'").fetchone()[0]
    con.execute(
        "INSERT INTO entity_labels (entity_id, label, lang, kind)"
        " VALUES (?, 'Bauer', 'de', 'alt')",
        (bauer,),
    )
    con.commit()
    out = store.maintain(con, only=["languages"])
    got = out.get("languages", out)
    assert got["labels_twinned"] >= 1
    langs = [
        r[0]
        for r in con.execute(
            "SELECT lang FROM entity_labels WHERE entity_id = ? AND label = 'Bauer'",
            (bauer,),
        )
    ]
    assert sorted(langs, key=str) == sorted([None, "de"], key=str)


def test_a_document_has_the_shape_its_table_gives(con: sqlite3.Connection) -> None:
    """``store.Document`` is the ``documents`` row and the text window: a
    migration that adds a column fails here until the shape says it, since
    ``get_document`` hands the row out under that type."""
    doc_id = store.ingest_text(con, "Some text to read.", title="A note")["doc_id"]
    for chars in (None, 0, 4):
        doc = store.get_document(con, doc_id, max_chars=chars)
        assert doc is not None
        assert set(doc) == set(store.Document.__annotations__)
        assert isinstance(doc["text_len"], int)
    columns = {r[1] for r in con.execute("PRAGMA table_info(documents)")}
    window = {"text", "offset", "truncated"}
    assert set(store.Document.__annotations__) == columns | window
