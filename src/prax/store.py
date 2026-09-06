"""prax.store — the ONLY module that touches SQLite.

Everything (FastAPI, MCP server, cron jobs) calls these functions.
Stage 0: text ingest, FTS5 search, link, 1-2 hop traverse.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import ARCHIVE_DIR, DB_PATH, SCHEMA_PATH

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
MAX_HOPS = 2


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_PATH.read_text())
    con.commit()


# ---------------------------------------------------------------- ingest

def _archive_bytes(data: bytes, archive_dir: Path = ARCHIVE_DIR) -> str:
    digest = hashlib.sha256(data).hexdigest()
    dest = archive_dir / digest[:2] / digest
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return digest


def _chunk(text: str) -> list[str]:
    step = CHUNK_SIZE - CHUNK_OVERLAP
    return [text[i : i + CHUNK_SIZE] for i in range(0, max(len(text), 1), step)]


def ingest_text(
    con: sqlite3.Connection,
    text: str,
    *,
    title: str | None = None,
    source_url: str | None = None,
    mime: str = "text/plain",
    meta: dict | None = None,
) -> dict:
    """Ingest raw text. Returns {'doc_id', 'hash', 'created'}; dedupe by hash."""
    raw = text.encode("utf-8")
    digest = _archive_bytes(raw)
    row = con.execute("SELECT id FROM documents WHERE hash = ?", (digest,)).fetchone()
    if row:
        return {"doc_id": row["id"], "hash": digest, "created": False}

    cur = con.execute(
        "INSERT INTO documents (hash, mime, title, source_url, meta, parsed_at)"
        " VALUES (?,?,?,?,?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
        (digest, mime, title, source_url, json.dumps(meta or {})),
    )
    doc_id = cur.lastrowid
    con.executemany(
        "INSERT INTO chunks (doc_id, seq, text) VALUES (?,?,?)",
        [(doc_id, i, c) for i, c in enumerate(_chunk(text))],
    )
    con.commit()
    return {"doc_id": doc_id, "hash": digest, "created": True}


# ---------------------------------------------------------------- read

def get_document(con: sqlite3.Connection, doc_id: int) -> dict | None:
    doc = con.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        return None
    chunks = con.execute(
        "SELECT seq, text FROM chunks WHERE doc_id = ? ORDER BY seq", (doc_id,)
    ).fetchall()
    return {**dict(doc), "text": "".join(c["text"] for c in chunks)}


def search(con: sqlite3.Connection, query: str, limit: int = 10) -> list[dict]:
    """FTS5 search returning compact snippets + ids (agent-shaped).

    Stage 2 turns this into hybrid FTS+vec with RRF; keep the signature.
    """
    rows = con.execute(
        """
        SELECT c.id AS chunk_id, c.doc_id, d.title,
               snippet(chunks_fts, 0, '[', ']', '…', 12) AS snippet,
               bm25(chunks_fts) AS score
        FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
                        JOIN documents d ON d.id = c.doc_id
        WHERE chunks_fts MATCH ?
        ORDER BY score LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- graph

@dataclass
class Edge:
    src: str
    src_type: str
    rel: str
    dst: str
    dst_type: str


def _entity_id(con: sqlite3.Connection, name: str, etype: str) -> int:
    con.execute(
        "INSERT OR IGNORE INTO entities (name, type) VALUES (?,?)", (name, etype)
    )
    return con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
    ).fetchone()["id"]


def link(
    con: sqlite3.Connection,
    edge: Edge,
    *,
    confidence: str = "EXTRACTED",
    source_doc: int | None = None,
    ontology_version: str | None = None,
) -> int:
    src = _entity_id(con, edge.src, edge.src_type)
    dst = _entity_id(con, edge.dst, edge.dst_type)
    cur = con.execute(
        "INSERT INTO edges (src, dst, rel, confidence, source_doc,"
        " ontology_version, valid_from)"
        " VALUES (?,?,?,?,?,?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
        (src, dst, edge.rel, confidence, source_doc, ontology_version),
    )
    con.commit()
    return cur.lastrowid


def traverse(
    con: sqlite3.Connection, entity_name: str, hops: int = 1
) -> list[dict]:
    """Expand up to MAX_HOPS from an entity over currently-valid edges."""
    hops = min(hops, MAX_HOPS)
    rows = con.execute(
        """
        WITH RECURSIVE walk(entity_id, depth) AS (
            SELECT id, 0 FROM entities WHERE name = ?
            UNION
            SELECT CASE WHEN e.src = w.entity_id THEN e.dst ELSE e.src END,
                   w.depth + 1
            FROM edges e JOIN walk w
                 ON (e.src = w.entity_id OR e.dst = w.entity_id)
            WHERE w.depth < ? AND e.valid_to IS NULL
        )
        SELECT DISTINCT s.name AS src, e.rel, t.name AS dst,
                        e.confidence, e.source_doc
        FROM edges e
        JOIN walk w ON (e.src = w.entity_id OR e.dst = w.entity_id)
        JOIN entities s ON s.id = e.src
        JOIN entities t ON t.id = e.dst
        WHERE e.valid_to IS NULL
        """,
        (entity_name, hops),
    ).fetchall()
    return [dict(r) for r in rows]
