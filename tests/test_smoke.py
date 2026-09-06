"""Stage 0 smoke tests: ingest→search, link→traverse, hash dedupe."""
import pytest

from prax import store


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ARCHIVE_DIR", tmp_path / "archive")
    c = store.connect(tmp_path / "test.db")
    store.init_db(c)
    yield c
    c.close()


def test_ingest_search_roundtrip(con):
    r = store.ingest_text(con, "Granular synthesis smears transients.",
                          title="granular note")
    assert r["created"]
    hits = store.search(con, "granular")
    assert hits and hits[0]["doc_id"] == r["doc_id"]


def test_duplicate_ingest_is_noop(con):
    a = store.ingest_text(con, "same content")
    b = store.ingest_text(con, "same content")
    assert a["doc_id"] == b["doc_id"] and not b["created"]


def test_link_traverse_roundtrip(con):
    store.link(con, store.Edge("phase vocoder", "method",
                               "implements", "STFT", "concept"))
    store.link(con, store.Edge("STFT", "concept",
                               "extends", "Fourier transform", "concept"))
    one_hop = store.traverse(con, "phase vocoder", hops=1)
    assert any(e["dst"] == "STFT" for e in one_hop)
    two_hop = store.traverse(con, "phase vocoder", hops=2)
    assert any(e["dst"] == "Fourier transform" for e in two_hop)
