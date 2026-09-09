"""Extractors and the parse queue, against the Zotero fixture files.

Docling is exercised only when ``PRAX_TEST_DOCLING=1`` (first run downloads
models and takes minutes); everything else runs on every checkout with the
``ingest`` extra installed.
"""

from __future__ import annotations

import importlib.util
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


def test_plain_fences_source_files_by_extension_or_content() -> None:
    plain = parsers.by_name("plain")
    assert plain.stamp == "plain/1-r2" and plain.hints
    code = b"function y = lim(x, t)\n  y = min(max(x, -t), t);\nend\n"
    out = plain(code, filename="getFilename.m")
    assert out.startswith("```matlab\n") and out.endswith("\nend\n```")
    prose = b"Comment: 11 pages. In Advances in Neural Information Processing Systems"
    assert plain(prose, filename=None) == prose.decode()
    assert (
        plain(code, filename="notes.txt") == code.decode()
    )  # a telling extension wins
    assert plain(b"```python\nx = 1\n```", filename="a.py").startswith("```python\nx")
    assert parsers.code_language("", "x.py") == "python"
    assert parsers.code_language("hello there", None) is None


@pytest.mark.skipif(
    not parsers.by_name("plain").available()
    or importlib.util.find_spec("magika") is None,
    reason="magika not installed",
)
def test_plain_detects_code_without_an_extension() -> None:
    py = (
        b"import numpy as np\n\ndef stft(x, n=1024):\n"
        b"    return np.fft.rfft(x[:n] * np.hanning(n))\n"
    )
    assert parsers.by_name("plain")(py).startswith("```python\n")
    text = b"Time-scale modification of audio is an essential tool in music production."
    assert parsers.by_name("plain")(text) == text.decode()
    note = (  # bibliographic notes look like YAML to a classifier: not code
        b'Comment: "Highlights of Spanish Astrophysics V", Proceedings of the VIII'
        b" Scientific Meeting of the Spanish Astronomical Society (SEA) held in"
        b" Santander, 7-11 July, 2008. Edited by J. Gorgas, L. J. Goicoechea."
    )
    assert parsers.by_name("plain")(note) == note.decode()
    assert parsers.code_language("a: 1", "conf.yaml") == "yaml"  # extension wins


def test_claude_vision_describes_an_image(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from prax.parsers import vision

    seen: dict[str, object] = {}

    class FakeMessages:
        def create(self, **kw):
            seen.update(kw)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="text",
                        text="## What it shows\nA compressor schematic. "
                        + "Detail. " * 60
                        + "\n\n"
                        "## Text in the image\nR12 100k\ngain (handwritten)",
                    )
                ]
            )

    monkeypatch.setattr(
        vision, "CLIENT_FACTORY", lambda: SimpleNamespace(messages=FakeMessages())
    )
    monkeypatch.setenv("PRAX_VISION_MODEL", "claude-test")
    gif = b"GIF89a" + bytes(40)
    ext = parsers.by_name("claude-vision")
    assert ext.explicit_only and ext.accepts("image/gif")
    assert "claude-vision" not in [e.name for e in parsers.candidates("image/gif")]
    out = ext(gif, filename="1176sch.gif")
    assert out.startswith("# 1176sch.gif\n\n*Image described by claude-test.*")
    assert "gain (handwritten)" in out
    assert seen["model"] == "claude-test"
    block = seen["messages"][0]["content"][0]
    assert block["type"] == "image" and block["source"]["media_type"] == "image/gif"
    with pytest.raises(parsers.ExtractionError, match="not a"):
        ext(b"plain text", filename="x.txt")
    # through the queue: the image gets a text artifact and chunks
    doc_id = store.register(con, gif, mime="image/gif", title="1176sch.gif")["doc_id"]
    assert queue.run(con, [doc_id], extractor="claude-vision").actions == {"created": 1}
    text = store.get_document(con, doc_id, max_chars=5000)["text"]
    assert "compressor schematic" in text
    assert store.get_meta(con, doc_id)["text_source"].startswith("claude-vision/")
    assert queue.run(con, [doc_id]).actions == {"skipped": 1}  # never by default


def test_registry_dispatch() -> None:
    assert parsers.for_mime("text/plain").name == "plain"
    assert parsers.for_mime("text/markdown").name == "plain"
    assert parsers.for_mime("image/png") is None
    assert parsers.for_mime("text/plain", preferred="pymupdf") is None  # wrong type
    with pytest.raises(KeyError):
        parsers.by_name("nope")
    plain = parsers.by_name("plain")
    assert plain.stamp == "plain/1-r2"
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
    data = YOSHII_PDF.read_bytes()
    monkeypatch.setenv("PRAX_MAX_LAYOUT_PAGES", "2")  # the 5-page fixture is "too long"
    with pytest.raises(parsers.ExtractionError, match="PRAX_MAX_LAYOUT_PAGES"):
        parsers.by_name("pymupdf4llm")(data)
    monkeypatch.delenv("PRAX_MAX_LAYOUT_PAGES")
    monkeypatch.setenv("PRAX_MAX_LAYOUT_MB", "0.001")  # the 8 KB fixture is "too big"
    with pytest.raises(parsers.ExtractionError, match="PRAX_MAX_LAYOUT_MB"):
        parsers.by_name("pymupdf4llm")(data)
    doc_id = store.register(con, data, mime="application/pdf", title="big")["doc_id"]
    assert queue.run(con, [doc_id]).actions == {"created": 1}
    assert store.get_meta(con, doc_id)["text_source"].startswith("pymupdf/")


@needs_trafilatura
def test_trafilatura_fences_code_blocks() -> None:
    html = (
        b"<html><head><title>Reverb tricks</title></head><body><article>"
        b"<h1>Reverb tricks</h1><p>Some prose about feedback delay networks that"
        b" is long enough to be kept by the extractor as the main content.</p>"
        b"<pre><code>def fdn(x, delays):\n    return sum(x[-d] for d in delays)\n"
        b"</code></pre><p>More prose after the code block, again long enough to"
        b" count as content for the extraction step.</p></article></body></html>"
    )
    text = parsers.by_name("trafilatura")(html)
    assert "```\ndef fdn(x, delays):" in text and "# Reverb tricks" in text
    assert parsers.by_name("trafilatura").stamp.endswith("-r2")


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
    assert meta["text_source"] == "plain/1-r2"
    assert [h.get("error", h.get("outcome")) for h in meta["parse_history"]] == [
        "RuntimeError: markdown path failed",
        "created",
    ]
    # an explicit extractor never falls back
    other = store.register(con, b"more body text " * 20, mime="text/plain")["doc_id"]
    report = queue.run(con, [other], extractor="boom")
    assert report.actions == {"error": 1}


def test_seen_documents_are_not_retried_and_limit_counts_work(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = parsers.Extractor("stub", ("text/",), lambda data: "tiny")
    monkeypatch.setattr(parsers, "REGISTRY", [stub, *parsers.REGISTRY])
    ids = [store.ingest_text(con, f"doc {i} " + "x " * 300)["doc_id"] for i in range(3)]
    assert queue.run(con, ids).actions == {"kept": 3}
    # a second pass over the same (still cache-like) selection does no work
    assert queue.run(con, ids).actions == {"seen": 3}
    assert queue.run(con, ids, force=True).actions == {"upgraded": 3}
    # limit counts documents worked on, not rows looked at
    more = [
        store.ingest_text(con, f"fresh {i} " + "y " * 300)["doc_id"] for i in range(2)
    ]
    stub2 = parsers.Extractor("stub2", ("text/",), lambda data: "z " * 200)
    monkeypatch.setattr(parsers, "REGISTRY", [stub2, *parsers.REGISTRY])
    seen_first = [store.ingest_text(con, "seen one " + "w " * 300)["doc_id"]]
    queue.run(con, seen_first)  # stub2 upgrades it... make it 'seen' via kept:
    monkeypatch.setattr(
        parsers,
        "REGISTRY",
        [parsers.Extractor("stub3", ("text/",), lambda data: "q"), *parsers.REGISTRY],
    )
    queue.run(con, seen_first)  # kept by stub3 -> seen next time
    report = queue.run(con, seen_first + more, limit=1)
    assert (
        report.actions["seen"] == 1
        and sum(v for k, v in report.actions.items() if k != "seen") == 1
    )


def test_queue_skips_unsupported_mime(con: sqlite3.Connection) -> None:
    doc_id = store.register(con, b"\x89PNG", mime="image/png")["doc_id"]
    assert queue.run(con, [doc_id]).actions == {"skipped": 1}
