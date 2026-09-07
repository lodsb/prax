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

from . import chunking, config, embeddings, ontology

MAX_HOPS = 2
VEC_DIM = 384  # baked into chunks_vec; another dimension is a migration
RRF_K = 60  # reciprocal rank fusion constant
RRF_DEPTH = 100  # candidates per side before fusion (docs/eval: 30 vs 100 vs 300)
_vec_loaded: dict[int, bool] = {}  # id(con) -> sqlite-vec available on it
CONFIDENCE_LEVELS = ("EXTRACTED", "INFERRED", "AMBIGUOUS")

_LOCK = threading.RLock()
_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"
_TOKEN = re.compile(r"\w+", re.UNICODE)
_SURROGATE = re.compile(r"[\ud800-\udfff]")

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
    _vec_loaded[id(con)] = _load_vec(con)
    return con


def _load_vec(con: sqlite3.Connection) -> bool:
    """Load sqlite-vec into the connection; False when it is not installed
    or this Python cannot load extensions. The store works without it
    (FTS-only search); vectors need it."""
    try:
        import sqlite_vec
    except ImportError:
        return False
    try:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
    except (AttributeError, sqlite3.OperationalError):
        return False
    return True


def has_vec(con: sqlite3.Connection) -> bool:
    """True when sqlite-vec is loaded on this connection."""
    return _vec_loaded.get(id(con), False)


def migrations() -> list[tuple[int, Path]]:
    """Numbered ``NNNN_name.sql`` files in ``prax/migrations``, ascending."""
    found = []
    for path in config.MIGRATIONS_DIR.glob("*.sql"):
        head = path.name.split("_", 1)[0]
        if not head.isdigit():
            raise ValueError(f"migration file without a number prefix: {path.name}")
        found.append((int(head), path))
    found.sort()
    numbers = [n for n, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(
            f"migrations must be numbered 1..N without gaps, got {numbers}"
        )
    return found


def schema_version(con: sqlite3.Connection) -> int:
    """The last migration applied (``PRAGMA user_version``); 0 = empty DB."""
    return int(con.execute("PRAGMA user_version").fetchone()[0])


@_serialized
def init_db(con: sqlite3.Connection) -> int:
    """Bring the database up to the latest migration; return its version.

    Each pending migration runs in its own transaction and stamps
    ``user_version`` on success, so an interrupted upgrade resumes cleanly.
    Applied migrations are never re-run; extend the schema by adding a new
    numbered file, never by editing an old one.
    """
    current = schema_version(con)
    for number, path in migrations():
        if number <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            con.executescript(
                "BEGIN;\n" + sql + f"\nPRAGMA user_version = {number};\nCOMMIT;"
            )
        except Exception:
            con.execute("ROLLBACK")
            raise
        current = number
    if has_vec(con):
        con.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0("
            f" chunk_id INTEGER PRIMARY KEY, embedding float[{VEC_DIM}]"
            " distance_metric=cosine, kind TEXT, +model TEXT)"
        )
        con.commit()
    return current


# ---------------------------------------------------------------- archive


def _archive_path(digest: str) -> Path:
    return config.archive_dir() / digest[:2] / digest


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """sha256 of a file read in chunks (for inventories; no archiving)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


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
def index_text(
    con: sqlite3.Connection,
    doc_id: int,
    text: str,
    *,
    text_source: str | None = None,
) -> dict[str, Any]:
    """Store parsed text as its own artifact, (re)build chunks and FTS rows.

    ``text_source`` names what produced the text (an extractor stamp such as
    ``"pymupdf4llm/0.0.27"`` or ``"zotero-ft-cache"``) and is written to
    ``meta.text_source`` in the same transaction.
    """
    exists = con.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if exists is None:
        raise KeyError(f"no such document: {doc_id}")
    # NUL bytes (pdftotext emits them for some page numbers) truncate SQLite's
    # text functions and the FTS tokenizer; lone surrogates (MuPDF, broken
    # fonts) cannot be encoded at all. Neither carries content.
    text = _SURROGATE.sub("�", text.replace("\x00", ""))
    data = text.encode("utf-8")
    text_hash = _archive_bytes(data)
    n_chunks = _write_chunks(con, doc_id, text)
    con.execute(
        f"UPDATE documents SET text_hash = ?, parsed_at = {_NOW} WHERE id = ?",
        (text_hash, doc_id),
    )
    if text_source is not None:
        con.execute(
            "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'),"
            " '$.text_source', ?) WHERE id = ?",
            (text_source, doc_id),
        )
    con.commit()
    return {"doc_id": doc_id, "text_hash": text_hash, "n_chunks": n_chunks}


def _write_chunks(con: sqlite3.Connection, doc_id: int, text: str) -> int:
    """Replace a document's chunks with structure-aware ones (prax.chunking)."""
    rows = chunking.rows(chunking.chunk(text))
    if has_vec(con):  # chunk ids change; their vectors must not outlive them
        old = [
            r[0]
            for r in con.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,))
        ]
        for i in range(0, len(old), 500):
            ids = old[i : i + 500]
            marks = ",".join("?" * len(ids))
            con.execute(f"DELETE FROM chunks_vec WHERE chunk_id IN ({marks})", ids)
    con.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    con.executemany(
        "INSERT INTO chunks (doc_id, seq, text, kind, locator, heading, data)"
        " VALUES (?,?,?,?,?,?,?)",
        [(doc_id, i, *r) for i, r in enumerate(rows)],
    )
    return len(rows)


@_serialized
def rechunk(con: sqlite3.Connection, doc_id: int) -> int:
    """Rebuild a document's chunks from its text artifact; return the count.

    The artifact and everything else stay untouched (chunks are disposable,
    rationale R3). Used after a chunker change or a schema migration that
    added chunk columns.
    """
    row = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    if not row["text_hash"]:
        return 0
    text = _read_archive(row["text_hash"]).decode("utf-8")
    n = _write_chunks(con, doc_id, text)
    con.commit()
    return n


def _is_indexed(con: sqlite3.Connection, doc_id: int) -> bool:
    row = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    return bool(row and row["text_hash"])


@_serialized
def is_indexed(con: sqlite3.Connection, doc_id: int) -> bool:
    """True once ``index_text`` has stored a text artifact for the document."""
    return _is_indexed(con, doc_id)


@_serialized
def get_meta(con: sqlite3.Connection, doc_id: int) -> dict[str, Any]:
    """The document's ``meta`` JSON without touching the text artifact."""
    row = con.execute("SELECT meta FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    return json.loads(row["meta"]) if row["meta"] else {}


@_serialized
def set_meta(
    con: sqlite3.Connection,
    doc_id: int,
    meta: dict[str, Any],
    *,
    title: str | None = None,
    source_url: str | None = None,
) -> None:
    """Replace ``meta`` (and optionally title / source_url) of a document.

    Importers use this to merge provenance when a known hash turns up again
    under another source record.
    """
    cur = con.execute(
        "UPDATE documents SET meta = ?, title = COALESCE(?, title),"
        " source_url = COALESCE(?, source_url) WHERE id = ?",
        (json.dumps(meta), title, source_url, doc_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"no such document: {doc_id}")
    con.commit()


@_serialized
def get_original(con: sqlite3.Connection, doc_id: int) -> bytes:
    """The archived original bytes of a document (what its hash names)."""
    row = con.execute("SELECT hash FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    return _read_archive(row["hash"])


def _like_prefix(prefix: str) -> str:
    """A LIKE pattern matching strings that start with ``prefix`` literally."""
    escaped = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return escaped + "%"


@_serialized
def select_documents(
    con: sqlite3.Connection,
    *,
    pending: bool = False,
    text_source_prefix: str | None = None,
    mime_prefix: str | None = None,
    limit: int | None = None,
) -> list[int]:
    """Document ids for batch jobs, oldest first.

    ``pending`` selects never-indexed documents (``parsed_at IS NULL``);
    ``text_source_prefix`` selects indexed ones whose ``meta.text_source``
    starts with the prefix (``"zotero-ft-cache"``, ``"pymupdf/"``). The two
    are OR-ed when both are given. ``mime_prefix`` narrows either.
    """
    clauses: list[str] = []
    args: list[Any] = []
    if pending:
        clauses.append("parsed_at IS NULL")
    if text_source_prefix is not None:
        clauses.append("json_extract(meta, '$.text_source') LIKE ? ESCAPE '!'")
        args.append(_like_prefix(text_source_prefix))
    if not clauses:
        return []
    sql = f"SELECT id FROM documents WHERE ({' OR '.join(clauses)})"
    if mime_prefix is not None:
        sql += " AND mime LIKE ? ESCAPE '!'"
        args.append(_like_prefix(mime_prefix))
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return [r["id"] for r in con.execute(sql, args)]


@_serialized
def meta_index(con: sqlite3.Connection, json_path: str) -> dict[str, int]:
    """Map every value found at ``json_path`` in any document's meta to its id.

    Array values are expanded, so ``"$.zotero.keys"`` yields one entry per
    key. Lets importers decide what is already imported without re-reading
    or re-hashing source files.
    """
    rows = con.execute(
        "SELECT d.id AS id, j.value AS value"
        " FROM documents d, json_each(json_extract(d.meta, ?)) j"
        " WHERE json_extract(d.meta, ?) IS NOT NULL",
        (json_path, json_path),
    ).fetchall()
    return {str(r["value"]): r["id"] for r in rows}


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
    doc_id = result["doc_id"]
    if text is not None and (result["created"] or not _is_indexed(con, doc_id)):
        index_text(con, doc_id, text)
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


def _chunk_shape(row: sqlite3.Row) -> dict[str, Any]:
    """The structural fields of a chunk row, decoded (None for legacy rows)."""
    loc = json.loads(row["locator"]) if row["locator"] else {}
    return {
        "kind": row["kind"],
        "heading": json.loads(row["heading"]) if row["heading"] else [],
        "page": loc.get("page"),
    }


SEARCH_MODES = ("hybrid", "fts", "vec")


def _fts_search(
    con: sqlite3.Connection,
    query: str,
    limit: int,
    kind: str | None,
    *,
    snippets: bool = True,
) -> list[dict[str, Any]]:
    """BM25 over chunks. ``snippets=False`` skips the snippet() call, which
    reads every matched chunk's text and dominates the cost of deep lists;
    ``_fts_snippets`` fills them in for the few hits that survive fusion."""
    expr = _fts_query(query)
    if expr is None:
        return []
    kind_clause = "AND c.kind = ?" if kind is not None else ""
    args: tuple[Any, ...] = (expr, kind, limit) if kind is not None else (expr, limit)
    snippet_col = (
        "snippet(chunks_fts, 0, '[', ']', '…', 12)"
        if snippets
        else "substr(c.text, 1, 160)"
    )
    rows = con.execute(
        f"""
        SELECT c.id AS chunk_id, c.doc_id, d.title,
               {snippet_col} AS snippet,
               bm25(chunks_fts) AS score,
               c.kind, c.locator, c.heading
        FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
                        JOIN documents d ON d.id = c.doc_id
        WHERE chunks_fts MATCH ? {kind_clause}
        ORDER BY score LIMIT ?
        """,
        args,
    ).fetchall()
    out = []
    for r in rows:
        hit = {k: r[k] for k in ("chunk_id", "doc_id", "title", "snippet", "score")}
        hit.update(_chunk_shape(r))
        out.append(hit)
    return out


def _fts_snippets(
    con: sqlite3.Connection, query: str, chunk_ids: list[int]
) -> dict[int, str]:
    """Match-marked snippets for a handful of chunks (the fused hits)."""
    expr = _fts_query(query)
    if expr is None or not chunk_ids:
        return {}
    marks = ",".join("?" * len(chunk_ids))
    rows = con.execute(
        f"""
        SELECT rowid, snippet(chunks_fts, 0, '[', ']', '…', 12) AS snippet
        FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid IN ({marks})
        """,
        (expr, *chunk_ids),
    ).fetchall()
    return {r["rowid"]: r["snippet"] for r in rows}


def _vec_search(
    con: sqlite3.Connection, vector: Any, limit: int, kind: str | None
) -> list[dict[str, Any]]:
    """KNN over chunks_vec (cosine); ``kind`` uses the vec0 metadata column."""
    from sqlite_vec import serialize_float32

    kind_clause = "AND kind = ?" if kind is not None else ""
    args: tuple[Any, ...] = (serialize_float32(list(map(float, vector))), limit)
    if kind is not None:
        args = (args[0], kind, limit)
    rows = con.execute(
        f"""
        SELECT v.chunk_id, v.distance, c.doc_id, d.title, c.kind, c.locator,
               c.heading, substr(c.text, 1, 160) AS head
        FROM (SELECT chunk_id, distance FROM chunks_vec
              WHERE embedding MATCH ? {kind_clause} AND k = ?
              ORDER BY distance) v
        JOIN chunks c ON c.id = v.chunk_id
        JOIN documents d ON d.id = c.doc_id
        ORDER BY v.distance
        """,
        args,
    ).fetchall()
    out = []
    for r in rows:
        hit = {
            "chunk_id": r["chunk_id"],
            "doc_id": r["doc_id"],
            "title": r["title"],
            "snippet": r["head"],
            "score": r["distance"],
        }
        hit.update(_chunk_shape(r))
        out.append(hit)
    return out


def _rrf(
    ranked: list[list[dict[str, Any]]], names: list[str], limit: int
) -> list[dict[str, Any]]:
    """Reciprocal rank fusion at document level.

    Each side contributes a document's best chunk rank: score(doc) = sum over
    sides of 1/(RRF_K + rank of its first chunk in that list). Fusing chunk
    ids instead lets a long document's many chunks crowd each list and never
    adds one document's evidence across sides; measured on the library set
    that was worse than FTS alone (docs/eval/). One hit per document is
    returned, carrying the chunk that ranked best on the side that found it
    first, plus ``fts_rank`` / ``vec_rank`` (document ranks, None if absent).
    """
    fused: dict[int, dict[str, Any]] = {}
    for hits, name in zip(ranked, names, strict=True):
        seen: set[int] = set()
        rank = 0
        for hit in hits:
            doc = hit["doc_id"]
            if doc in seen:
                continue
            seen.add(doc)
            rank += 1
            entry = fused.get(doc)
            if entry is None:
                entry = {**hit, "score": 0.0, "fts_rank": None, "vec_rank": None}
                fused[doc] = entry
            entry["score"] += 1.0 / (RRF_K + rank)
            entry[f"{name}_rank"] = rank
    out = sorted(fused.values(), key=lambda h: -h["score"])
    return out[:limit]


@_serialized
def vec_status(con: sqlite3.Connection) -> dict[str, Any]:
    """Whether vectors are usable on this connection and how many exist."""
    status: dict[str, Any] = {"available": has_vec(con), "rows": 0, "models": {}}
    rows = con.execute(
        "SELECT model, count(*) AS n FROM chunk_embeddings GROUP BY model"
    ).fetchall()
    status["models"] = {r["model"]: r["n"] for r in rows}
    status["rows"] = sum(status["models"].values())
    emb = embeddings.current()
    status["embedder"] = emb.name if emb else None
    return status


@_serialized
def search(
    con: sqlite3.Connection,
    query: str,
    limit: int = 10,
    *,
    kind: str | None = None,
    mode: str = "hybrid",
) -> list[dict[str, Any]]:
    """Search returning compact snippets + ids (agent-shaped).

    ``hybrid`` (default) runs FTS5/BM25 and vector KNN in parallel and fuses
    them with reciprocal rank fusion; each hit carries ``score`` (the fused
    score), ``fts_rank`` and ``vec_rank`` (None when absent from that list).
    It degrades to FTS-only when embeddings are disabled, sqlite-vec is
    missing or no vectors exist yet. ``fts`` and ``vec`` force one side.
    Every hit carries the chunk's ``kind``, section ``heading`` path and
    ``page``; ``kind`` filters to one kind.
    """
    if kind is not None and kind not in chunking.KINDS:
        raise ValueError(f"kind must be one of {chunking.KINDS}")
    if mode not in SEARCH_MODES:
        raise ValueError(f"mode must be one of {SEARCH_MODES}")
    emb = embeddings.current() if mode != "fts" else None
    vectors_ready = emb is not None and has_vec(con) and _vec_count(con) > 0
    if mode == "vec" and not vectors_ready:
        raise ValueError("vector search unavailable: no embedder, extension or vectors")
    if not vectors_ready:
        return _fts_search(con, query, limit, kind)
    assert emb is not None
    vector = emb.embed_query(query)
    if mode == "vec":
        return _vec_search(con, vector, limit, kind)
    depth = max(limit * 3, RRF_DEPTH)
    fused = _rrf(
        [
            _fts_search(con, query, depth, kind, snippets=False),
            _vec_search(con, vector, depth, kind),
        ],
        ["fts", "vec"],
        limit,
    )
    marked = _fts_snippets(con, query, [h["chunk_id"] for h in fused])
    for h in fused:
        if h["chunk_id"] in marked:
            h["snippet"] = marked[h["chunk_id"]]
    return fused


def _vec_count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]


# ------------------------------------------------------------- embeddings


@_serialized
def pending_embeddings(
    con: sqlite3.Connection, model: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Chunks without a vector from ``model``: ``{chunk_id, kind, text}``."""
    sql = (
        "SELECT c.id AS chunk_id, c.kind, c.text FROM chunks c"
        " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id"
        " WHERE e.chunk_id IS NULL OR e.model != ? ORDER BY c.id"
    )
    args: tuple[Any, ...] = (model,)
    if limit is not None:
        sql += " LIMIT ?"
        args = (model, limit)
    return [dict(r) for r in con.execute(sql, args)]


@_serialized
def count_pending_embeddings(con: sqlite3.Connection, model: str) -> int:
    """How many chunks ``pending_embeddings`` would return, without the text."""
    return con.execute(
        "SELECT count(*) FROM chunks c"
        " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id"
        " WHERE e.chunk_id IS NULL OR e.model != ?",
        (model,),
    ).fetchone()[0]


@_serialized
def store_embeddings(
    con: sqlite3.Connection,
    items: list[tuple[int, str | None, Any]],
    model: str,
) -> int:
    """Write ``(chunk_id, kind, vector)`` rows for ``model``; replaces any
    earlier vector of the chunk. Requires sqlite-vec on the connection."""
    if not has_vec(con):
        raise RuntimeError("sqlite-vec is not loaded on this connection")
    from sqlite_vec import serialize_float32

    ids = [cid for cid, _, _ in items]
    for i in range(0, len(ids), 500):
        part = ids[i : i + 500]
        con.execute(
            f"DELETE FROM chunks_vec WHERE chunk_id IN ({','.join('?' * len(part))})",
            part,
        )
    con.executemany(
        "INSERT INTO chunks_vec (chunk_id, embedding, kind, model) VALUES (?,?,?,?)",
        [
            (cid, serialize_float32(list(map(float, vec))), kind, model)
            for cid, kind, vec in items
        ],
    )
    con.executemany(
        "INSERT INTO chunk_embeddings (chunk_id, model) VALUES (?, ?)"
        " ON CONFLICT(chunk_id) DO UPDATE SET model = excluded.model,"
        f" embedded_at = {_NOW}",
        [(cid, model) for cid in ids],
    )
    con.commit()
    return len(items)


@_serialized
def get_chunk(con: sqlite3.Connection, chunk_id: int) -> dict[str, Any] | None:
    """One chunk in full: text, kind, heading, locator and table ``data``."""
    r = con.execute(
        "SELECT id AS chunk_id, doc_id, seq, text, kind, locator, heading, data"
        " FROM chunks WHERE id = ?",
        (chunk_id,),
    ).fetchone()
    if r is None:
        return None
    out = {k: r[k] for k in ("chunk_id", "doc_id", "seq", "text")}
    out.update(_chunk_shape(r))
    out["locator"] = json.loads(r["locator"]) if r["locator"] else None
    out["data"] = json.loads(r["data"]) if r["data"] else None
    return out


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
    """Insert a currently-valid edge; entities are created on demand.

    Types are validated against the current ontology (invariant 9); the edge
    is stamped with that ontology's version unless one is given.
    """
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}")
    onto = ontology.current()
    onto.check_edge(edge.src_type, edge.rel, edge.dst_type)
    if ontology_version is None:
        ontology_version = onto.version
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
def find_edges(con: sqlite3.Connection, edge: Edge) -> list[int]:
    """Ids of currently-valid edges with exactly this src, rel and dst.

    Lets importers seed edges idempotently without touching SQL themselves.
    """
    rows = con.execute(
        """
        SELECT e.id FROM edges e
        JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst
        WHERE s.name = ? AND s.type = ? AND e.rel = ?
          AND t.name = ? AND t.type = ? AND e.valid_to IS NULL
        ORDER BY e.id
        """,
        (edge.src, edge.src_type, edge.rel, edge.dst, edge.dst_type),
    ).fetchall()
    return [r["id"] for r in rows]


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
