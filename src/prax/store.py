"""prax.store — the ONLY module that touches SQLite.

Everything (FastAPI, MCP server, importers, cron jobs) calls these functions.
Stage 0: two-step ingest (register, index_text), FTS5 search, get, link,
1–2 hop traverse.

Concurrency: FastAPI and FastMCP run sync handlers on worker threads, so the
connection is created with ``check_same_thread=False`` and every public
function is serialized behind one process-wide re-entrant lock.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from . import config

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
MAX_HOPS = 2
CONFIDENCE_LEVELS = ("EXTRACTED", "INFERRED", "AMBIGUOUS")

_LOCK = threading.RLock()
_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"
_TOKEN = re.compile(r"\w+", re.UNICODE)

P = ParamSpec("P")
R = TypeVar("R")


def _serialized(fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with _LOCK:
            return fn(*args, **kwargs)

    return wrapper


# ------------------------------------------------------------- connection


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    con.commit()


# ---------------------------------------------------------------- archive


def _archive_path(digest: str) -> Path:
    return config.archive_dir() / digest[:2] / digest


def _archive_bytes(data: bytes) -> str:
    """Content-address ``data`` into the archive; return its sha256 hex."""
    digest = hashlib.sha256(data).hexdigest()
    dest = _archive_path(digest)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(data)
        part.replace(dest)
    return digest


def _read_archive(digest: str) -> bytes:
    return _archive_path(digest).read_bytes()


# --------------------------------------------------------------- chunking


def _chunk(text: str) -> list[str]:
    """Fixed windows of CHUNK_SIZE chars overlapping by CHUNK_OVERLAP."""
    if not text:
        return []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    chunks: list[str] = []
    start = 0
    while True:
        chunks.append(text[start : start + CHUNK_SIZE])
        if start + CHUNK_SIZE >= len(text):
            return chunks
        start += step


def _fts_query(query: str) -> str | None:
    """Build a safe FTS5 MATCH expression: every token quoted, joined by OR.

    User strings are never passed to MATCH raw; punctuation and FTS operators
    in the input cannot raise. OR keeps recall for natural multi-word queries;
    BM25 ranks chunks that match more (and rarer) terms first.
    """
    tokens = _TOKEN.findall(query)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


# ----------------------------------------------------------------- ingest


@_serialized
def register(
    con: sqlite3.Connection,
    data: bytes,
    *,
    mime: str,
    title: str | None = None,
    source_url: str | None = None,
    meta: dict[str, Any] | None = None,
    original_path: str | None = None,
) -> dict[str, Any]:
    """Archive the original bytes and insert the document row.

    The hash is of the original bytes. Nothing is parsed or indexed here;
    ``parsed_at`` stays NULL until ``index_text`` runs.
    Returns ``{'doc_id', 'hash', 'created'}``; a known hash is a no-op.
    """
    digest = _archive_bytes(data)
    row = con.execute("SELECT id FROM documents WHERE hash = ?", (digest,)).fetchone()
    if row:
        return {"doc_id": row["id"], "hash": digest, "created": False}
    cur = con.execute(
        "INSERT INTO documents (hash, mime, title, source_url, original_path, meta)"
        " VALUES (?,?,?,?,?,?)",
        (digest, mime, title, source_url, original_path, json.dumps(meta or {})),
    )
    con.commit()
    return {"doc_id": cur.lastrowid, "hash": digest, "created": True}


@_serialized
def index_text(con: sqlite3.Connection, doc_id: int, text: str) -> dict[str, Any]:
    """Store parsed text as its own artifact, (re)build chunks and FTS rows."""
    if con.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone() is None:
        raise KeyError(f"no such document: {doc_id}")
    text_hash = _archive_bytes(text.encode("utf-8"))
    chunks = _chunk(text)
    con.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    con.executemany(
        "INSERT INTO chunks (doc_id, seq, text) VALUES (?,?,?)",
        [(doc_id, i, c) for i, c in enumerate(chunks)],
    )
    con.execute(
        f"UPDATE documents SET text_hash = ?, parsed_at = {_NOW} WHERE id = ?",
        (text_hash, doc_id),
    )
    con.commit()
    return {"doc_id": doc_id, "text_hash": text_hash, "n_chunks": len(chunks)}


def _is_indexed(con: sqlite3.Connection, doc_id: int) -> bool:
    row = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    return bool(row and row["text_hash"])


@_serialized
def ingest_file(
    con: sqlite3.Connection,
    data: bytes,
    *,
    mime: str,
    title: str | None = None,
    source_url: str | None = None,
    meta: dict[str, Any] | None = None,
    original_path: str | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    """Register a file; index it too when its text is known.

    ``text/*`` files are their own text. Anything else is left for the parse
    queue unless ``text`` is supplied by the caller (a parser).
    """
    result = register(
        con,
        data,
        mime=mime,
        title=title,
        source_url=source_url,
        meta=meta,
        original_path=original_path,
    )
    if text is None and mime.startswith("text/"):
        text = data.decode("utf-8", errors="replace")
    if text is not None and (result["created"] or not _is_indexed(con, result["doc_id"])):
        index_text(con, result["doc_id"], text)
    return result


@_serialized
def ingest_text(
    con: sqlite3.Connection,
    text: str,
    *,
    title: str | None = None,
    source_url: str | None = None,
    mime: str = "text/plain",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest raw text: register its bytes and index it in one step."""
    return ingest_file(
        con,
        text.encode("utf-8"),
        mime=mime,
        title=title,
        source_url=source_url,
        meta=meta,
        text=text,
    )


# ------------------------------------------------------------------- read


@_serialized
def get_document(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    offset: int = 0,
    max_chars: int | None = None,
) -> dict[str, Any] | None:
    """One document with its text read from the parsed-text artifact.

    ``text`` is the window ``[offset, offset + max_chars)``; ``text_len`` and
    ``truncated`` tell the caller whether more remains.
    """
    doc = con.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        return None
    out = dict(doc)
    out["meta"] = json.loads(out["meta"]) if out["meta"] else {}
    full = _read_archive(doc["text_hash"]).decode("utf-8") if doc["text_hash"] else ""
    offset = max(0, offset)
    end = len(full) if max_chars is None else min(len(full), offset + max(0, max_chars))
    out.update(
        text=full[offset:end],
        text_len=len(full),
        offset=offset,
        truncated=end < len(full),
    )
    return out


@_serialized
def search(
    con: sqlite3.Connection, query: str, limit: int = 10
) -> list[dict[str, Any]]:
    """FTS5 search returning compact snippets + ids (agent-shaped).

    Stage 2 turns this into hybrid FTS+vec with RRF; keep the signature.
    """
    expr = _fts_query(query)
    if expr is None:
        return []
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
        (expr, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ graph


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


@_serialized
def link(
    con: sqlite3.Connection,
    edge: Edge,
    *,
    confidence: str = "EXTRACTED",
    source_doc: int | None = None,
    ontology_version: str | None = None,
) -> int:
    """Insert a currently-valid edge; entities are created on demand."""
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}")
    src = _entity_id(con, edge.src, edge.src_type)
    dst = _entity_id(con, edge.dst, edge.dst_type)
    cur = con.execute(
        "INSERT INTO edges (src, dst, rel, confidence, source_doc,"
        f" ontology_version, valid_from) VALUES (?,?,?,?,?,?, {_NOW})",
        (src, dst, edge.rel, confidence, source_doc, ontology_version),
    )
    con.commit()
    return cur.lastrowid


@_serialized
def traverse(
    con: sqlite3.Connection, entity_name: str, hops: int = 1
) -> list[dict[str, Any]]:
    """Currently-valid edges within ``hops`` (max MAX_HOPS) of an entity.

    An edge is returned only when both of its endpoints are reachable within
    the hop limit; ``hop`` is the distance of its farther endpoint.
    """
    hops = max(0, min(hops, MAX_HOPS))
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
        ),
        reach(entity_id, depth) AS (
            SELECT entity_id, MIN(depth) FROM walk GROUP BY entity_id
        )
        SELECT e.id AS edge_id,
               s.name AS src, s.type AS src_type, e.rel,
               t.name AS dst, t.type AS dst_type,
               e.confidence, e.source_doc,
               MAX(rs.depth, rt.depth) AS hop
        FROM edges e
        JOIN reach rs ON rs.entity_id = e.src
        JOIN reach rt ON rt.entity_id = e.dst
        JOIN entities s ON s.id = e.src
        JOIN entities t ON t.id = e.dst
        WHERE e.valid_to IS NULL
        ORDER BY hop, e.id
        """,
        (entity_name, hops),
    ).fetchall()
    return [dict(r) for r in rows]
