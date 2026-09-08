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

from . import chunking, config, embeddings, ontology, vectors
from . import rerank as rerank_mod

MAX_HOPS = 2
RERANK_DEPTH = 30  # hits rescored by the cross-encoder when reranking is on
VEC_DIM = 384  # dimension of the vector index; another dimension is a new index file
RRF_K = 60  # reciprocal rank fusion constant
RRF_DEPTH = 100  # candidates per side before fusion (docs/eval: 30 vs 100 vs 300)
VEC_SEARCH_CAP = 4000  # widest KNN candidate set when post-filtering by kind
_indexes: dict[tuple[str, bool], vectors.VectorIndex] = {}  # (path, writable)
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
    return con


def vectors_available() -> bool:
    """True when the usearch index library is installed. Without it the
    store works FTS-only."""
    return vectors.available()


def _index_path(model: str) -> Path:
    return config.data_dir() / f"vectors-{model}.usearch"


def _index(model: str, *, writable: bool) -> vectors.VectorIndex | None:
    """The index for ``model``: a memory-mapped view for reads (None when no
    file exists yet), or the in-memory writable copy for batch jobs."""
    path = _index_path(model)
    key = (str(path), writable)
    idx = _indexes.get(key)
    if idx is None:
        if not writable and not path.exists():
            return None
        idx = vectors.VectorIndex(path, VEC_DIM, writable=writable)
        _indexes[key] = idx
    return idx


def _drop_index_views(model: str) -> None:
    """Forget the read-only view so the next read reopens the saved file."""
    key = (str(_index_path(model)), False)
    idx = _indexes.pop(key, None)
    if idx is not None:
        idx.close()


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
    _drop_legacy_vec_table(con)
    return current


def _drop_legacy_vec_table(con: sqlite3.Connection) -> None:
    """Stage 2 first kept vectors in a sqlite-vec table; the index file
    replaced it. Drop the table when the extension is still around to do
    so (it cannot be dropped without its module); run VACUUM afterwards to
    reclaim the space (docs/howto.md)."""
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chunks_vec'"
    ).fetchone()
    if row is None:
        return
    try:
        import sqlite_vec

        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        con.execute("DROP TABLE chunks_vec")
        con.commit()
    except (ImportError, AttributeError, sqlite3.OperationalError):
        pass


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
    # chunk ids change: their chunk_embeddings rows cascade away, the index
    # keeps stale keys that queries filter out and compact_vectors() removes
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
    """KNN over the usearch index (cosine distance), joined to live chunks.

    The index has no filter of its own, so ``kind`` is applied after the
    search over a wider candidate set (chunks of one kind are a few percent
    of the index). Keys whose chunk no longer exists are dropped here.
    """
    model = embeddings.current().name  # type: ignore[union-attr]
    idx = _index(model, writable=False)
    if idx is None:
        return []
    want = limit * (25 if kind is not None else 3)
    found = idx.search(vector, min(max(want, limit), VEC_SEARCH_CAP))
    if not found:
        return []
    distance = dict(found)
    ids = list(distance)
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), 500):
        part = ids[i : i + 500]
        marks = ",".join("?" * len(part))
        kind_clause = "AND c.kind = ?" if kind is not None else ""
        args: tuple[Any, ...] = (*part, kind) if kind is not None else tuple(part)
        rows = con.execute(
            f"""
            SELECT c.id AS chunk_id, c.doc_id, d.title, c.kind, c.locator,
                   c.heading, substr(c.text, 1, 160) AS head
            FROM chunks c JOIN documents d ON d.id = c.doc_id
            WHERE c.id IN ({marks}) {kind_clause}
            """,
            args,
        ).fetchall()
        for r in rows:
            hit = {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "title": r["title"],
                "snippet": r["head"],
                "score": distance[r["chunk_id"]],
            }
            hit.update(_chunk_shape(r))
            out.append(hit)
    out.sort(key=lambda h: h["score"])
    return out[:limit]


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
    """Whether vectors are usable and how many exist, per model, plus the
    state of the current model's index file."""
    status: dict[str, Any] = {"available": vectors_available(), "rows": 0, "models": {}}
    rows = con.execute(
        "SELECT model, count(*) AS n FROM chunk_embeddings GROUP BY model"
    ).fetchall()
    status["models"] = {r["model"]: r["n"] for r in rows}
    status["rows"] = sum(status["models"].values())
    emb = embeddings.current()
    status["embedder"] = emb.name if emb else None
    status["index"] = None
    if emb is not None and vectors_available():
        idx = _index(emb.name, writable=False)
        if idx is not None:
            status["index"] = idx.stats()
    return status


@_serialized
def search(
    con: sqlite3.Connection,
    query: str,
    limit: int = 10,
    *,
    kind: str | None = None,
    mode: str = "hybrid",
    rerank: bool | None = None,
) -> list[dict[str, Any]]:
    """Search returning compact snippets + ids (agent-shaped).

    ``hybrid`` (default) runs FTS5/BM25 and vector KNN in parallel and fuses
    them with reciprocal rank fusion; each hit carries ``score`` (the fused
    score), ``fts_rank`` and ``vec_rank`` (None when absent from that list).
    It degrades to FTS-only when embeddings are disabled, no index exists
    or no vectors exist yet. ``fts`` and ``vec`` force one side.
    Every hit carries the chunk's ``kind``, section ``heading`` path and
    ``page``; ``kind`` filters to one kind.

    ``rerank`` rescores the top ``RERANK_DEPTH`` hits with the configured
    cross-encoder (``prax.rerank``; None follows ``PRAX_RERANK``, which is
    off by default) and adds ``rerank_score``.
    """
    if kind is not None and kind not in chunking.KINDS:
        raise ValueError(f"kind must be one of {chunking.KINDS}")
    if mode not in SEARCH_MODES:
        raise ValueError(f"mode must be one of {SEARCH_MODES}")
    reranker = rerank_mod.current() if rerank is None or rerank else None
    if rerank and reranker is None:
        raise ValueError("rerank requested but PRAX_RERANK names no model")
    fetch = max(limit, RERANK_DEPTH) if reranker else limit
    hits = _search_hits(con, query, fetch, kind, mode)
    if reranker is not None and hits:
        hits = _apply_rerank(con, reranker, query, hits)
    return hits[:limit]


def _apply_rerank(
    con: sqlite3.Connection,
    reranker: rerank_mod.Reranker,
    query: str,
    hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ids = [h["chunk_id"] for h in hits]
    marks = ",".join("?" * len(ids))
    texts = {
        r["id"]: r["text"]
        for r in con.execute(f"SELECT id, text FROM chunks WHERE id IN ({marks})", ids)
    }
    scores = reranker.score(query, [texts.get(i, "") for i in ids])
    for h, s in zip(hits, scores, strict=True):
        h["rerank_score"] = float(s)
    return sorted(hits, key=lambda h: -h["rerank_score"])


def _search_hits(
    con: sqlite3.Connection, query: str, limit: int, kind: str | None, mode: str
) -> list[dict[str, Any]]:
    emb = embeddings.current() if mode != "fts" else None
    vectors_ready = (
        emb is not None
        and vectors_available()
        and _index(emb.name, writable=False) is not None
        and _vec_count(con) > 0
    )
    if mode == "vec" and not vectors_ready:
        raise ValueError("vector search unavailable: no embedder, index or vectors")
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
    """Add ``(chunk_id, kind, vector)`` rows for ``model`` to its index
    (in memory until ``save_vectors``) and record them in ``chunk_embeddings``.
    Requires the usearch library; the batch job calls this."""
    if not vectors_available():
        raise RuntimeError("usearch is not installed; vectors cannot be stored")
    if not items:
        return 0
    idx = _index(model, writable=True)
    assert idx is not None
    ids = [cid for cid, _, _ in items]
    import numpy as np

    idx.add(ids, np.vstack([np.asarray(v, dtype=np.float32) for _, _, v in items]))
    con.executemany(
        "INSERT INTO chunk_embeddings (chunk_id, model) VALUES (?, ?)"
        " ON CONFLICT(chunk_id) DO UPDATE SET model = excluded.model,"
        f" embedded_at = {_NOW}",
        [(cid, model) for cid in ids],
    )
    con.commit()
    return len(items)


@_serialized
def save_vectors(model: str) -> dict[str, Any]:
    """Write ``model``'s index to disk and drop cached read views so readers
    in this process reopen the new file. The bookkeeping rows are committed
    as they are written, so a crash between two saves leaves rows that the
    next ``pending_embeddings`` run will not repeat; ``compact_vectors``
    reconciles the two."""
    idx = _index(model, writable=True)
    assert idx is not None
    _drop_index_views(model)  # a mapped view blocks the replace on Windows
    idx.save()
    return idx.stats()


@_serialized
def compact_vectors(con: sqlite3.Connection, model: str) -> dict[str, int]:
    """Reconcile the index with the bookkeeping: drop keys whose chunk is
    gone, and forget bookkeeping rows whose vector is missing from the
    index (so they get embedded again). Saves the index."""
    idx = _index(model, writable=True)
    assert idx is not None
    live = {r[0] for r in con.execute("SELECT id FROM chunks")}
    booked = {
        r[0]
        for r in con.execute(
            "SELECT chunk_id FROM chunk_embeddings WHERE model = ?", (model,)
        )
    }
    keys = {int(k) for k in idx.all_keys()}
    stale = keys - live
    removed = idx.remove(stale)
    missing = booked - keys
    if missing:
        ids = list(missing)
        for i in range(0, len(ids), 500):
            part = ids[i : i + 500]
            marks = ",".join("?" * len(part))
            con.execute(
                f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({marks})", part
            )
        con.commit()
    _drop_index_views(model)
    idx.save()
    return {"removed_stale": removed, "forgot_missing": len(missing), "count": len(idx)}


@_serialized
def list_documents(
    con: sqlite3.Connection,
    *,
    limit: int = 50,
    offset: int = 0,
    title: str | None = None,
    source: str | None = None,
    mime_prefix: str | None = None,
) -> dict[str, Any]:
    """Documents without their text, newest first, for browsing.

    ``title`` is a case-insensitive substring; ``source`` matches
    ``meta.source``; ``mime_prefix`` a MIME type prefix. Returns
    ``{"total", "items"}`` where each item carries the row, its decoded
    ``meta`` and its chunk count.
    """
    clauses: list[str] = []
    args: list[Any] = []
    if title:
        clauses.append("lower(d.title) LIKE ? ESCAPE '!'")
        args.append("%" + _like_prefix(title.lower())[:-1] + "%")
    if source:
        clauses.append("json_extract(d.meta, '$.source') = ?")
        args.append(source)
    if mime_prefix:
        clauses.append("d.mime LIKE ? ESCAPE '!'")
        args.append(_like_prefix(mime_prefix))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = con.execute(f"SELECT count(*) FROM documents d {where}", args).fetchone()[0]
    rows = con.execute(
        f"""
        SELECT d.id, d.title, d.mime, d.source_url, d.added_at, d.parsed_at, d.meta,
               (SELECT count(*) FROM chunks c WHERE c.doc_id = d.id) AS n_chunks
        FROM documents d {where}
        ORDER BY d.added_at DESC, d.id DESC LIMIT ? OFFSET ?
        """,
        (*args, max(1, min(limit, 500)), max(0, offset)),
    ).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["meta"] = json.loads(item["meta"]) if item["meta"] else {}
        items.append(item)
    return {"total": total, "items": items}


@_serialized
def list_chunks(con: sqlite3.Connection, doc_id: int) -> list[dict[str, Any]]:
    """A document as its chunks in order, with text and structure (the
    document view renders from this)."""
    rows = con.execute(
        "SELECT id AS chunk_id, doc_id, seq, text, kind, locator, heading, data"
        " FROM chunks WHERE doc_id = ? ORDER BY seq",
        (doc_id,),
    ).fetchall()
    out = []
    for r in rows:
        c = {k: r[k] for k in ("chunk_id", "doc_id", "seq", "text")}
        c.update(_chunk_shape(r))
        c["locator"] = json.loads(r["locator"]) if r["locator"] else None
        c["data"] = json.loads(r["data"]) if r["data"] else None
        out.append(c)
    return out


@_serialized
def find_entities(
    con: sqlite3.Connection, q: str, *, limit: int = 20
) -> list[dict[str, Any]]:
    """Entities whose name contains ``q`` (case-insensitive), with their
    number of currently valid edges, most connected first."""
    pattern = "%" + _like_prefix(q.lower())[:-1] + "%"
    rows = con.execute(
        """
        SELECT e.id, e.name, e.type,
               (SELECT count(*) FROM edges x
                WHERE (x.src = e.id OR x.dst = e.id) AND x.valid_to IS NULL) AS degree
        FROM entities e WHERE lower(e.name) LIKE ? ESCAPE '!'
          AND e.canonical_id IS NULL
        ORDER BY degree DESC, e.name LIMIT ?
        """,
        (pattern, max(1, min(limit, 200))),
    ).fetchall()
    return [dict(r) for r in rows]


@_serialized
def original_info(con: sqlite3.Connection, doc_id: int) -> dict[str, Any] | None:
    """MIME type, title and archive path of a document's original, for
    serving it; None when the document does not exist."""
    row = con.execute(
        "SELECT hash, mime, title, original_path FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "path": _archive_path(row["hash"]),
        "mime": row["mime"] or "application/octet-stream",
        "title": row["title"],
        "original_path": row["original_path"],
    }


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
    evidence: str | None = None,
) -> int:
    """Insert a currently-valid edge; entities are created on demand.

    Types are validated against the current ontology (invariant 9); the edge
    is stamped with that ontology's version unless one is given. ``evidence``
    is a short quote from ``source_doc`` that supports the edge.
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
        f" ontology_version, evidence, valid_from) VALUES (?,?,?,?,?,?,?, {_NOW})",
        (src, dst, edge.rel, confidence, source_doc, ontology_version, evidence),
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
def merge_entities(con: sqlite3.Connection, duplicate_id: int, into_id: int) -> None:
    """Record that ``duplicate_id`` is the same thing as ``into_id``.

    Nothing is deleted or rewritten: the duplicate keeps its name and its
    edges (they are evidence), and gets ``canonical_id`` pointing at the
    survivor; ``traverse`` and lookups follow the pointer. Chains are
    flattened so every alias points straight at the final survivor.
    """
    if duplicate_id == into_id:
        raise ValueError("an entity cannot be merged into itself")
    rows = {
        r["id"]: r
        for r in con.execute(
            "SELECT id, type, canonical_id FROM entities WHERE id IN (?, ?)",
            (duplicate_id, into_id),
        )
    }
    if len(rows) != 2:
        raise KeyError("no such entity")
    if rows[duplicate_id]["type"] != rows[into_id]["type"]:
        raise ValueError("entities of different types cannot be merged")
    survivor = rows[into_id]["canonical_id"] or into_id
    if survivor == duplicate_id:
        raise ValueError("that merge would form a cycle")
    con.execute(
        "UPDATE entities SET canonical_id = ? WHERE id = ? OR canonical_id = ?",
        (survivor, duplicate_id, duplicate_id),
    )
    con.commit()


@_serialized
def canonical_entity(con: sqlite3.Connection, entity_id: int) -> int:
    row = con.execute(
        "SELECT canonical_id FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such entity: {entity_id}")
    return row["canonical_id"] or entity_id


@_serialized
def invalidate_edge(
    con: sqlite3.Connection,
    edge_id: int,
    *,
    successor: Edge | None = None,
    confidence: str = "EXTRACTED",
    source_doc: int | None = None,
    evidence: str | None = None,
) -> int | None:
    """End an edge's validity now (``valid_to``), optionally inserting the
    edge that supersedes it. The old edge stays as history (invariant 8).
    Returns the successor's id."""
    cur = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE id = ? AND valid_to IS NULL",
        (edge_id,),
    )
    if cur.rowcount == 0:
        raise KeyError(f"no such currently valid edge: {edge_id}")
    con.commit()
    if successor is None:
        return None
    return link(
        con,
        successor,
        confidence=confidence,
        source_doc=source_doc,
        evidence=evidence,
    )


@_serialized
def queue_review(
    con: sqlite3.Connection,
    *,
    src: str,
    src_type: str | None,
    rel: str,
    dst: str,
    dst_type: str | None,
    reason: str,
    source_doc: int | None = None,
    evidence: str | None = None,
    ontology_version: str | None = None,
) -> int:
    """Park a triple that does not fit the ontology (invariant 9): it is
    kept for a person to decide, never written to the graph."""
    version = ontology_version or ontology.current().version
    cur = con.execute(
        "INSERT INTO review_queue (source_doc, src, src_type, rel, dst, dst_type,"
        " evidence, reason, ontology_version) VALUES (?,?,?,?,?,?,?,?,?)",
        (source_doc, src, src_type, rel, dst, dst_type, evidence, reason, version),
    )
    con.commit()
    return cur.lastrowid


@_serialized
def list_review(
    con: sqlite3.Connection,
    *,
    open_only: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    where = "WHERE resolved_at IS NULL" if open_only else ""
    rows = con.execute(
        f"SELECT * FROM review_queue {where} ORDER BY id LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


@_serialized
def count_review(con: sqlite3.Connection, *, open_only: bool = True) -> int:
    where = "WHERE resolved_at IS NULL" if open_only else ""
    return int(con.execute(f"SELECT count(*) FROM review_queue {where}").fetchone()[0])


@_serialized
def get_review(con: sqlite3.Connection, review_id: int) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT * FROM review_queue WHERE id = ?", (review_id,)
    ).fetchone()
    return dict(row) if row else None


@_serialized
def resolve_review(con: sqlite3.Connection, review_id: int, resolution: str) -> None:
    """Close a review item: ``linked`` (written as an edge by hand),
    ``dropped`` or ``ontology`` (the ontology grew to fit it)."""
    if resolution not in ("linked", "dropped", "ontology"):
        raise ValueError("resolution must be linked, dropped or ontology")
    cur = con.execute(
        f"UPDATE review_queue SET resolved_at = {_NOW}, resolution = ? WHERE id = ?",
        (resolution, review_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"no such review item: {review_id}")
    con.commit()


@_serialized
def select_for_extraction(
    con: sqlite3.Connection,
    *,
    ontology_version: str,
    limit: int | None = None,
    mime_prefix: str | None = None,
    min_chars: int = 0,
) -> list[int]:
    """Indexed documents not yet extracted under ``ontology_version``
    (``meta.extraction.ontology_version``), oldest first. ``min_chars``
    skips documents whose chunks hold less text than that (Zotero notes,
    scans without a text layer): nothing to extract, a call wasted."""
    sql = (
        "SELECT id FROM documents WHERE text_hash IS NOT NULL"
        " AND (json_extract(meta, '$.extraction.ontology_version') IS NULL"
        "      OR json_extract(meta, '$.extraction.ontology_version') != ?)"
    )
    args: list[Any] = [ontology_version]
    if min_chars > 0:
        sql += (
            " AND (SELECT coalesce(sum(length(text)), 0) FROM chunks"
            "      WHERE chunks.doc_id = documents.id) >= ?"
        )
        args.append(min_chars)
    if mime_prefix:
        sql += " AND mime LIKE ? ESCAPE '!'"
        args.append(_like_prefix(mime_prefix))
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return [r["id"] for r in con.execute(sql, args)]


@_serialized
def traverse(
    con: sqlite3.Connection, entity_name: str, hops: int = 1
) -> list[dict[str, Any]]:
    """Currently-valid edges within ``hops`` (max MAX_HOPS) of an entity.

    An edge is returned only when both of its endpoints are reachable within
    the hop limit; ``hop`` is the distance of its farther endpoint.
    """
    hops = max(0, min(hops, MAX_HOPS))
    # Every entity id is mapped to its canonical id first, so a merged alias
    # and its survivor are one node: the walk runs over canonical ids, and
    # edges are reported under the canonical names (invariant 8: the edge
    # rows themselves keep the alias ids they were written with).
    rows = con.execute(
        """
        WITH RECURSIVE canon(id, cid) AS (
            SELECT id, COALESCE(canonical_id, id) FROM entities
        ),
        cedges(id, src, dst) AS (
            SELECT e.id, cs.cid, cd.cid FROM edges e
            JOIN canon cs ON cs.id = e.src JOIN canon cd ON cd.id = e.dst
            WHERE e.valid_to IS NULL
        ),
        walk(entity_id, depth) AS (
            SELECT DISTINCT c.cid, 0 FROM entities n JOIN canon c ON c.id = n.id
            WHERE n.name = ?
            UNION
            SELECT CASE WHEN e.src = w.entity_id THEN e.dst ELSE e.src END,
                   w.depth + 1
            FROM cedges e JOIN walk w
                 ON (e.src = w.entity_id OR e.dst = w.entity_id)
            WHERE w.depth < ?
        ),
        reach(entity_id, depth) AS (
            SELECT entity_id, MIN(depth) FROM walk GROUP BY entity_id
        )
        SELECT e.id AS edge_id,
               s.name AS src, s.type AS src_type, e.rel,
               t.name AS dst, t.type AS dst_type,
               e.confidence, e.source_doc, e.evidence, e.ontology_version,
               e.valid_from,
               MAX(rs.depth, rt.depth) AS hop
        FROM cedges ce
        JOIN edges e ON e.id = ce.id
        JOIN reach rs ON rs.entity_id = ce.src
        JOIN reach rt ON rt.entity_id = ce.dst
        JOIN entities s ON s.id = ce.src
        JOIN entities t ON t.id = ce.dst
        ORDER BY hop, e.id
        """,
        (entity_name, hops),
    ).fetchall()
    return [dict(r) for r in rows]
