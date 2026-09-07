"""Extractors and the parse queue, against the Zotero fixture files.

Docling is exercised only when ``PRAX_TEST_DOCLING=1`` (first run downloads
models and takes minutes); everything else runs on every checkout with the
``ingest`` extra installed.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from prax import parsers, store
from prax.importers import zotero
from prax.parsers import queue

FIXTURE = Path(__file__).parent / "fixtures" / "zotero"
YOSHII_PDF = next((FIXTURE / "storage" / "T7VVNPCK").glob("*.pdf"))
SNAPSHOT_HTML = FIXTURE / "storage" / "97KAI26I" / "1804.html"

needs_pymupdf = pytest.mark.skipif(
    not parsers.by_name("pymupdf4llm").available(), reason="pymupdf4llm not installed"
)
needs_trafilatura = pytest.mark.skipif(
    not parsers.by_name("trafilatura").available(), reason="trafilatura not installed"
)


# ---------------------------------------------------------------- registry


def test_registry_dispatch() -> None:
    assert parsers.for_mime("text/plain").name == "plain"
    assert parsers.for_mime("text/markdown").name == "plain"
    assert parsers.for_mime("image/png") is None
    assert parsers.for_mime("text/plain", preferred="pymupdf") is None  # wrong type
    with pytest.raises(KeyError):
        parsers.by_name("nope")
    plain = parsers.by_name("plain")
    assert plain.stamp == "plain/1"
    assert plain(b"h\xc3\xa9llo") == "héllo"
    # explicit-only extractors are never in the default chain, only when named
    chain = [e.name for e in parsers.candidates("application/pdf")]
    assert "docling" not in chain and "pymupdf4llm-ocr" not in chain
    if parsers.by_name("docling").available():
        assert [e.name for e in parsers.candidates("application/pdf", "docling")] == [
            "docling"
        ]


@needs_pymupdf
def test_pymupdf_extractors_read_the_fixture_pdf() -> None:
    data = YOSHII_PDF.read_bytes()
    md = parsers.by_name("pymupdf4llm")(data)
    plain = parsers.by_name("pymupdf")(data)
    for text in (md, plain):
        assert "Correlated Tensor Factorization" in text
        assert "nonnegative matrix factorization" in text.lower()
    assert parsers.by_name("pymupdf4llm").stamp.startswith("pymupdf4llm/")
    assert parsers.for_mime("application/pdf").name == "pymupdf4llm"


@needs_pymupdf
def test_scanned_pdf_is_refused_by_markdown_and_left_empty(
    con: sqlite3.Connection,
) -> None:
    import pymupdf

    with pymupdf.open() as doc:  # pages with no text layer, like a scan
        for _ in range(3):
            doc.new_page()
        scan = doc.tobytes()
    with pytest.raises(parsers.ExtractionError, match="needs OCR"):
        parsers.by_name("pymupdf4llm")(scan)
    doc_id = store.register(con, scan, mime="application/pdf", title="scan")["doc_id"]
    assert queue.run(con, [doc_id]).actions == {"empty": 1}
    history = store.get_meta(con, doc_id)["parse_history"]
    assert [h.get("outcome", "error") for h in history] == ["error", "empty"]
    assert store.select_documents(con, pending=True) == [doc_id]  # still pending


@needs_pymupdf
def test_oversized_pdf_falls_back_to_plain_extraction(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_MAX_LAYOUT_MB", "0.001")  # the 8 KB fixture is "too big"
    data = YOSHII_PDF.read_bytes()
    with pytest.raises(parsers.ExtractionError, match="PRAX_MAX_LAYOUT_MB"):
        parsers.by_name("pymupdf4llm")(data)
    doc_id = store.register(con, data, mime="application/pdf", title="big")["doc_id"]
    assert queue.run(con, [doc_id]).actions == {"created": 1}
    assert store.get_meta(con, doc_id)["text_source"].startswith("pymupdf/")


@needs_trafilatura
def test_trafilatura_strips_page_chrome() -> None:
    text = parsers.by_name("trafilatura")(SNAPSHOT_HTML.read_bytes())
    assert text.startswith("# Estimation with Low-Rank Time-Frequency Synthesis Models")
    assert "signal decomposition" in text
    assert "Skip to main content" not in text
    assert "We gratefully acknowledge support" not in text


@needs_trafilatura
def test_trafilatura_raises_on_empty_page() -> None:
    with pytest.raises(parsers.ExtractionError):
        parsers.by_name("trafilatura")(b"<html><body></body></html>")


@pytest.mark.skipif(
    not os.environ.get("PRAX_TEST_DOCLING"), reason="PRAX_TEST_DOCLING unset"
)
def test_docling_reads_the_fixture_pdf() -> None:
    text = parsers.by_name("docling")(YOSHII_PDF.read_bytes())
    assert "Correlated Tensor Factorization" in text


# ------------------------------------------------------------------- queue


def _register_pdf(con: sqlite3.Connection) -> int:
    r = store.register(
        con, YOSHII_PDF.read_bytes(), mime="application/pdf", title="Yoshii"
    )
    return r["doc_id"]


@needs_pymupdf
def test_queue_indexes_pending_pdf(con: sqlite3.Connection) -> None:
    doc_id = _register_pdf(con)
    assert store.select_documents(con, pending=True) == [doc_id]
    report = queue.run(con, [doc_id])
    assert report.actions == {"created": 1} and report.errors == []
    doc = store.get_document(con, doc_id, max_chars=100)
    assert doc["parsed_at"] and doc["meta"]["text_source"].startswith("pymupdf4llm/")
    assert doc["meta"]["parse_history"][0]["outcome"] == "created"
    assert store.search(con, "tensor factorization")[0]["doc_id"] == doc_id
    assert store.select_documents(con, pending=True) == []
    assert store.select_documents(con, text_source_prefix="pymupdf4llm/") == [doc_id]
    assert (
        store.select_documents(con, text_source_prefix="pymupdf/") == []
    )  # no prefix bleed


@needs_pymupdf
def test_queue_upgrades_zotero_cache_text(
    con: sqlite3.Connection, tmp_path: Path
) -> None:
    lib = zotero.open_library(FIXTURE, tmp_path / "w")
    zotero.run(lib, con)
    lib.close()
    doc_id = store.meta_index(con, "$.zotero.keys")["T7VVNPCK"]
    before = store.get_document(con, doc_id, max_chars=0)
    assert before["meta"]["text_source"] == "zotero-ft-cache"
    n_cache = len(store.select_documents(con, text_source_prefix="zotero-ft-cache"))
    assert n_cache == 8  # 11 attachments, the four Birbaumer twins are one document
    assert (
        len(
            store.select_documents(
                con, text_source_prefix="zotero-ft-cache", mime_prefix="text/html"
            )
        )
        == 1
    )

    report = queue.run(con, [doc_id])
    assert report.actions == {"upgraded": 1}
    after = store.get_document(con, doc_id, max_chars=0)
    assert after["meta"]["text_source"].startswith("pymupdf4llm/")
    assert after["text_hash"] != before["text_hash"]
    assert after["meta"]["zotero"]["keys"] == ["T7VVNPCK"]  # rest of meta untouched
    assert (
        len(store.select_documents(con, text_source_prefix="zotero-ft-cache"))
        == n_cache - 1
    )
    assert store.search(con, "correlated tensor")[0]["doc_id"] == doc_id


def test_queue_keeps_old_text_when_new_is_short(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id = store.ingest_text(con, "x " * 500, title="long")["doc_id"]
    stub = parsers.Extractor("stub", ("text/",), lambda data: "tiny")
    monkeypatch.setattr(parsers, "REGISTRY", [stub, *parsers.REGISTRY])
    assert queue.run(con, [doc_id]).actions == {"kept": 1}
    doc = store.get_document(con, doc_id, max_chars=0)
    assert doc["text_len"] == 1000 and doc["meta"].get("text_source") is None
    assert doc["meta"]["parse_history"][-1]["outcome"] == "kept"
    assert queue.run(con, [doc_id], force=True).actions == {"upgraded": 1}
    assert store.get_document(con, doc_id)["text"] == "tiny"


def test_queue_records_errors_and_continues(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(data: bytes) -> str:
        raise RuntimeError("no parser today")

    monkeypatch.setattr(
        parsers,
        "REGISTRY",
        [parsers.Extractor("boom", ("text/",), boom), *parsers.REGISTRY],
    )
    a = store.ingest_text(con, "first")["doc_id"]
    b = store.ingest_text(con, "second")["doc_id"]
    report = queue.run(con, [a, b], extractor="boom")
    assert report.actions == {"error": 2} and len(report.errors) == 2
    assert "no parser today" in store.get_meta(con, a)["parse_history"][-1]["error"]
    assert store.get_document(con, a)["text"] == "first"  # untouched


def test_queue_falls_back_to_next_extractor(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(data: bytes) -> str:
        raise RuntimeError("markdown path failed")

    monkeypatch.setattr(
        parsers,
        "REGISTRY",
        [parsers.Extractor("boom", ("text/",), boom), *parsers.REGISTRY],
    )
    doc_id = store.register(con, b"plain body text " * 20, mime="text/plain")["doc_id"]
    assert queue.run(con, [doc_id]).actions == {"created": 1}
    meta = store.get_meta(con, doc_id)
    assert meta["text_source"] == "plain/1"
    assert [h.get("error", h.get("outcome")) for h in meta["parse_history"]] == [
        "RuntimeError: markdown path failed",
        "created",
    ]
    # an explicit extractor never falls back
    other = store.register(con, b"more body text " * 20, mime="text/plain")["doc_id"]
    report = queue.run(con, [other], extractor="boom")
    assert report.actions == {"error": 1}


def test_queue_skips_unsupported_mime(con: sqlite3.Connection) -> None:
    doc_id = store.register(con, b"\x89PNG", mime="image/png")["doc_id"]
    assert queue.run(con, [doc_id]).actions == {"skipped": 1}
