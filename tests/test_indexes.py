"""Migration 0010: the JSON-path filters use their expression indexes."""

from __future__ import annotations

import sqlite3

from prax import inbox, store


def _plan(con: sqlite3.Connection, sql: str, *args: object) -> str:
    return " | ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, args))


def test_capture_filters_use_the_source_index(con: sqlite3.Connection) -> None:
    store.ingest_text(con, "x " * 200, title="T", meta={"source": "capture"})
    plan = _plan(
        con,
        "SELECT id FROM documents WHERE text_hash IS NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
        " AND json_extract(meta, '$.source') IN ('upload', 'capture', 'inbox')",
    )
    assert "idx_documents_source" in plan
    assert inbox.pending_captures(con) == []


def test_open_review_items_by_document_use_their_index(con: sqlite3.Connection) -> None:
    plan = _plan(
        con,
        "SELECT id FROM review_queue WHERE source_doc = ? AND resolution IS NULL",
        1,
    )
    assert "idx_review_open_doc" in plan


def test_retired_and_promote_lists_use_their_indexes(con: sqlite3.Connection) -> None:
    assert "idx_documents_retired" in _plan(
        con,
        "SELECT id FROM documents WHERE json_extract(meta, '$.retired') IS NOT NULL",
    )
    assert "idx_documents_promote" in _plan(
        con,
        "SELECT id FROM documents WHERE json_extract(meta, '$.promote') IS NOT NULL"
        " ORDER BY json_extract(meta, '$.promote.at'), id",
    )


def test_select_for_extraction_sources_and_mime(con: sqlite3.Connection) -> None:
    cap = store.ingest_text(con, "c " * 300, title="C", meta={"source": "capture"})[
        "doc_id"
    ]
    store.ingest_text(con, "z " * 300, title="Z", meta={"source": "zotero"})
    img = store.ingest_text(
        con, "i " * 300, title="I", meta={"source": "upload"}, mime="image/png"
    )["doc_id"]
    got = store.select_for_extraction(
        con, ontology_version="v", sources=("capture", "upload")
    )
    assert got == [cap, img]
    got = store.select_for_extraction(
        con,
        ontology_version="v",
        sources=("capture", "upload"),
        skip_mime_prefix="image/",
    )
    assert got == [cap]
