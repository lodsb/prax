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
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, ParamSpec, Self, TypeVar

from . import chunking, config, embeddings, ontology, vectors
from . import rerank as rerank_mod

MAX_HOPS = 2
RERANK_DEPTH = 30  # hits rescored by the cross-encoder when reranking is on
VEC_DIM = 384  # dimension of the vector index; another dimension is a new index file
RRF_K = 60  # reciprocal rank fusion constant
RRF_DEPTH = 100  # candidates per side before fusion (docs/eval: 30 vs 100 vs 300)
# The document-field BM25 list is short and precise (a field names what a
# document is); at equal weight a lone rank-1 field hit loses to any document
# two chunk lists agree on. A short query names a thing, so the field gets
# FIELD_WEIGHT there; a long paraphrase is about content, and the field's
# incidental word matches would mislead, so the weight fades to 1 by
# FIELD_WEIGHT_WORDS words (docs/eval/retrieval-field-2026-09-10.md).
FIELD_WEIGHT = 2.0
FIELD_WEIGHT_WORDS = 7
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


def _doc_index_path(model: str) -> Path:
    return config.data_dir() / f"vectors-doc-{model}.usearch"


def _open_index(path: Path, *, writable: bool) -> vectors.VectorIndex | None:
    key = (str(path), writable)
    idx = _indexes.get(key)
    if idx is None:
        if not writable and not path.exists():
            return None
        idx = vectors.VectorIndex(path, VEC_DIM, writable=writable)
        _indexes[key] = idx
    return idx


def _index(model: str, *, writable: bool) -> vectors.VectorIndex | None:
    """The chunk index for ``model``: a memory-mapped view for reads (None
    when no file exists yet), or the in-memory writable copy for batch jobs."""
    return _open_index(_index_path(model), writable=writable)


def _doc_index(model: str, *, writable: bool) -> vectors.VectorIndex | None:
    """The document-field index for ``model`` (keys are document ids)."""
    return _open_index(_doc_index_path(model), writable=writable)


def _drop_index_views(model: str) -> None:
    """Forget the read-only views so the next read reopens the saved files."""
    for path in (_index_path(model), _doc_index_path(model)):
        idx = _indexes.pop((str(path), False), None)
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
    known = migrations()
    latest = known[-1][0] if known else 0
    if current > latest:
        raise RuntimeError(
            f"the store is at schema version {current}, this code knows {latest}:"
            " update prax before opening it"
        )
    for number, path in known:
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


ACRONYM_MIN_DOCS = 1  # one definition is enough: the phrase only adds an alternative
ACRONYM_EXPANSIONS = 2
RARE_CHUNKS = 50  # a token in fewer chunks than this decides the ranking
RARE_MAX_LEN = 6  # longer tokens are words, not acronyms, unless the table knows them
# Measured on the 62 library queries (docs/eval/retrieval-acronyms-2026-09-12.md):
# keyword-side expansion alone lifts MRR 0.89 -> 0.905; expanding the embedder's
# input and a rank list of chunks holding every term both cost; the rare-terms
# list costs one query and is what makes "adaa iir" find the ADAA papers.
ALL_TERMS_WEIGHT = 0.0  # the rank list of chunks holding every query term; 0 = off
RARE_TERMS_WEIGHT = 3.0  # the rank list of chunks holding the rare terms
VEC_EXPAND = False  # embed the query as typed; expansions only on the keyword side


@_serialized
def replace_acronyms(con: sqlite3.Connection, rows: list[tuple[str, str, int]]) -> int:
    """Replace the acronyms table (``scripts/build_acronyms.py``):
    ``(acronym, expansion, documents)`` rows, lowercased."""
    con.execute("DELETE FROM acronyms")
    con.executemany(
        "INSERT INTO acronyms (acronym, expansion, docs) VALUES (?, ?, ?)",
        [(a.lower(), e.lower(), int(n)) for a, e, n in rows],
    )
    con.commit()
    return len(rows)


def acronym_expansions(
    con: sqlite3.Connection,
    token: str,
    *,
    min_docs: int = ACRONYM_MIN_DOCS,
    limit: int = ACRONYM_EXPANSIONS,
) -> list[str]:
    """The phrases the library defines ``token`` as, best attested first."""
    return [
        r[0]
        for r in con.execute(
            "SELECT expansion FROM acronyms WHERE acronym = ? AND docs >= ?"
            " ORDER BY docs DESC, expansion LIMIT ?",
            (token.lower(), min_docs, limit),
        )
    ]


def expand_query(con: sqlite3.Connection, query: str) -> list[list[str]]:
    """The query as terms, each a list of alternatives: the token itself and
    the phrases the library defines it as (``[["adaa", "antiderivative
    antialiasing"], ["iir"]]``). Tokens of nine or more characters, and
    digits, are never acronyms."""
    terms: list[list[str]] = []
    for tok in _TOKEN.findall(query):
        alts = [tok.lower()]
        if 2 <= len(tok) <= 8 and tok.isalpha():
            alts += [e for e in acronym_expansions(con, tok) if e != tok.lower()]
        terms.append(alts)
    return terms


def expanded_text(terms: list[list[str]]) -> str:
    """The query with its expansions, for the embedder."""
    return " ".join(alt for term in terms for alt in term)


def _expr(terms: list[list[str]], *, all_terms: bool) -> str | None:
    """MATCH expression: every alternative quoted (a phrase stays a phrase),
    alternatives OR-ed within a term, terms OR-ed (recall) or AND-ed (the
    tier that wants every term present)."""
    if not terms:
        return None
    groups = ["(" + " OR ".join(f'"{a}"' for a in term) + ")" for term in terms]
    return (" AND " if all_terms else " OR ").join(groups)


def _rare_terms(con: sqlite3.Connection, terms: list[list[str]]) -> list[list[str]]:
    """The acronym-shaped terms that match fewer than ``RARE_CHUNKS`` chunks
    (and at least one): a rare exact token like "adaa" should decide the
    ranking, not the common words around it, so those terms get a rank list
    of their own. Only short tokens, tokens with digits and known acronyms
    qualify: a rare inflection of an ordinary word ("reassigning",
    "upmixing") pulled paraphrase queries towards the wrong documents."""
    out = []
    for term in terms:
        tok = term[0]
        acronym_shaped = (
            len(tok) <= RARE_MAX_LEN or any(ch.isdigit() for ch in tok) or len(term) > 1
        )
        if not acronym_shaped:
            continue
        expr = _expr([term], all_terms=False)
        n = con.execute(
            "SELECT count(*) FROM (SELECT rowid FROM chunks_fts WHERE chunks_fts"
            " MATCH ? LIMIT ?)",
            (expr, RARE_CHUNKS),
        ).fetchone()[0]
        if 0 < n < RARE_CHUNKS:
            out.append(term)
    return out


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
    _refresh_document_field(con, doc_id)
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
    _refresh_document_field(con, doc_id)
    con.commit()


@_serialized
def retitle(
    con: sqlite3.Connection,
    doc_id: int,
    title: str,
    *,
    source: str,
    run: str | None = None,
    confidence: str | None = None,
) -> dict[str, Any]:
    """Change a document's title, keeping the old one.

    ``meta.title_history`` accumulates the replaced titles with their source;
    ``meta.title_source`` names who wrote the current one (an importer keeps
    its hands off a title it did not write). The ``paper`` entity carrying
    the old title follows: renamed when it is this document's alone, merged
    into the entity of the new title when one exists, left alone when other
    documents share the old title. The document field is refreshed, so the
    new title is searchable and the document vector is embedded again.
    """
    title = " ".join(title.split())
    if not title:
        raise ValueError("a title cannot be empty")
    row = con.execute(
        "SELECT title, meta FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    old = row["title"] or ""
    meta = json.loads(row["meta"] or "{}")
    if old == title:
        return {"doc_id": doc_id, "title": title, "changed": False, "entity": None}
    history = list(meta.get("title_history") or [])
    history.append(
        {
            "title": old,
            "source": meta.get("title_source"),
            "until": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    meta["title_history"] = history
    meta["title_source"] = source
    meta["title_run"] = run
    meta["title_confidence"] = confidence
    con.execute(
        "UPDATE documents SET title = ?, meta = ? WHERE id = ?",
        (title, json.dumps(meta), doc_id),
    )
    entity_action = None
    shared = con.execute(
        "SELECT count(*) FROM documents WHERE title = ? AND id != ?", (old, doc_id)
    ).fetchone()[0]
    if old and not shared:
        e = con.execute(
            "SELECT id FROM entities WHERE name = ? AND type = 'paper'", (old,)
        ).fetchone()
        if e is not None:
            target = con.execute(
                "SELECT id, canonical_id FROM entities"
                " WHERE name = ? AND type = 'paper'",
                (title,),
            ).fetchone()
            if target is None:
                con.execute(
                    "UPDATE entities SET name = ? WHERE id = ?", (title, e["id"])
                )
                entity_action = "renamed"
            elif target["id"] != e["id"]:
                survivor = target["canonical_id"] or target["id"]
                if survivor != e["id"]:
                    con.execute(
                        "UPDATE entities SET canonical_id = ?"
                        " WHERE id = ? OR canonical_id = ?",
                        (survivor, e["id"], e["id"]),
                    )
                    entity_action = "merged"
    _refresh_document_field(con, doc_id)
    con.commit()
    return {
        "doc_id": doc_id,
        "old": old,
        "title": title,
        "changed": True,
        "entity": entity_action,
    }


# ------------------------------------------------------------------- jobs
# What runs on the batch host, for the door and the UI to show: each pass
# is a row with a heartbeat; one that stops beating without finishing is
# reported stale. Bookkeeping only (migration 0009).

JOB_STALE_SECONDS = 600


def _job_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@_serialized
def job_start(
    con: sqlite3.Connection,
    name: str,
    *,
    total: int | None = None,
    note: str | None = None,
) -> int:
    import os
    import socket

    now = _job_now()
    cur = con.execute(
        "INSERT INTO jobs (name, host, pid, started_at, updated_at, status, done,"
        " total, note) VALUES (?,?,?,?,?,'running',0,?,?)",
        (name, socket.gethostname(), os.getpid(), now, now, total, note),
    )
    con.commit()
    return int(cur.lastrowid or 0)


@_serialized
def job_update(
    con: sqlite3.Connection,
    job_id: int,
    *,
    done: int | None = None,
    total: int | None = None,
    note: str | None = None,
) -> None:
    con.execute(
        "UPDATE jobs SET updated_at = ?, done = coalesce(?, done),"
        " total = coalesce(?, total), note = coalesce(?, note) WHERE id = ?",
        (_job_now(), done, total, note, job_id),
    )
    con.commit()


@_serialized
def job_finish(
    con: sqlite3.Connection,
    job_id: int,
    *,
    status: str = "done",
    note: str | None = None,
) -> None:
    now = _job_now()
    con.execute(
        "UPDATE jobs SET status = ?, finished_at = ?, updated_at = ?,"
        " note = coalesce(?, note) WHERE id = ?",
        (status, now, now, note, job_id),
    )
    con.commit()


def list_jobs(con: sqlite3.Connection, *, limit: int = 20) -> dict[str, Any]:
    """``running`` (with ``stale`` when the heartbeat is old) and the last
    ``limit`` finished jobs, newest first."""
    now = datetime.now(UTC)
    running = []
    for r in con.execute(
        "SELECT * FROM jobs WHERE status = 'running' ORDER BY started_at DESC"
    ):
        row = dict(r)
        try:
            beat = datetime.fromisoformat(row["updated_at"])
            age = (now - beat).total_seconds()
        except ValueError:
            age = 0.0
        row["stale"] = age > JOB_STALE_SECONDS
        row["age"] = int(age)
        running.append(row)
    recent = [
        dict(r)
        for r in con.execute(
            "SELECT * FROM jobs WHERE status != 'running'"
            " ORDER BY finished_at DESC, id DESC LIMIT ?",
            (limit,),
        )
    ]
    return {"running": running, "recent": recent}


class Job:
    """``with store.Job(con, "extract", total=n) as job: job.update(done=i)``:
    started on entry, finished on exit (failed with the error's text when
    the block raised)."""

    def __init__(
        self,
        con: sqlite3.Connection,
        name: str,
        *,
        total: int | None = None,
        note: str | None = None,
    ) -> None:
        self.con = con
        self.name = name
        self.id = job_start(con, name, total=total, note=note)

    def update(
        self,
        *,
        done: int | None = None,
        total: int | None = None,
        note: str | None = None,
    ) -> None:
        job_update(self.con, self.id, done=done, total=total, note=note)

    def note(self, text: str) -> None:
        job_update(self.con, self.id, note=text)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None and exc_type is not None:
            job_finish(
                self.con, self.id, status="failed", note=f"{exc_type.__name__}: {exc}"
            )
        else:
            job_finish(self.con, self.id, status="done")


def release_vector_views() -> int:
    """Drop every memory-mapped read view of the index files so a batch job
    on this machine can replace them (Windows refuses otherwise); the next
    query reopens them. Returns how many views were closed."""
    n = 0
    for key in [k for k in _indexes if not k[1]]:
        idx = _indexes.pop(key, None)
        if idx is not None:
            idx.close()
            n += 1
    return n


def data_version(con: sqlite3.Connection) -> int:
    """Changes whenever another connection commits (``PRAGMA data_version``):
    the cheap "did anything change" signal the UI polls."""
    return int(con.execute("PRAGMA data_version").fetchone()[0])


# --------------------------------------------------------------- retiring
# A document that should not be found any more (a duplicate capture, a
# page saved by mistake) is retired, not deleted: the row and the archived
# bytes stay, ``meta.retired`` says why and since when, its chunks and its
# retrieval field go (so search and the batch jobs pass it by), and its
# edges end (invariant 8). ``unretire_document`` brings the index back.


def is_retired(meta: dict[str, Any]) -> bool:
    return bool(meta.get("retired"))


@_serialized
def retire_document(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    reason: str,
    duplicate_of: int | None = None,
    by: str = "human",
) -> dict[str, Any]:
    """Take a document out of the index and the graph, keeping row, bytes
    and text artifact. Returns what went: chunks, edges, review items."""
    meta = get_meta(con, doc_id)
    if duplicate_of is not None and get_meta(con, duplicate_of) is None:
        raise KeyError(f"no such document: {duplicate_of}")
    meta["retired"] = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "reason": reason,
        "of": duplicate_of,
        "by": by,
    }
    chunks = con.execute(
        "SELECT count(*) FROM chunks WHERE doc_id = ?", (doc_id,)
    ).fetchone()[0]
    con.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    con.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
    con.execute("DELETE FROM document_embeddings WHERE doc_id = ?", (doc_id,))
    edges = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE source_doc = ? AND valid_to IS NULL",
        (doc_id,),
    ).rowcount
    items = con.execute(
        f"UPDATE review_queue SET resolution = 'dropped', resolved_at = {_NOW}"
        " WHERE source_doc = ? AND resolution IS NULL",
        (doc_id,),
    ).rowcount
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return {"doc_id": doc_id, "chunks": chunks, "edges": edges, "review_items": items}


@_serialized
def unretire_document(con: sqlite3.Connection, doc_id: int) -> dict[str, Any]:
    """Bring a retired document back into the index (its text artifact is
    re-chunked); the edges it lost stay history and a new extraction pass
    re-reads it."""
    meta = get_meta(con, doc_id)
    meta.pop("retired", None)
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    n = 0
    row = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row and row["text_hash"]:
        text = _read_archive(row["text_hash"]).decode("utf-8")
        n = _write_chunks(con, doc_id, text)
        _refresh_document_field(con, doc_id)
    con.commit()
    return {"doc_id": doc_id, "chunks": n}


def fingerprint_text(text: str) -> frozenset[str]:
    """The chunk fingerprint of a text: a hash per chunk the chunker would
    make, so two captures of one page compare by what they say, not by
    their bytes (a page's markup changes between two visits, its text
    seldom does)."""
    rows = chunking.rows(chunking.chunk(text))
    return frozenset(
        hashlib.sha1(" ".join(str(r[0]).split()).encode("utf-8")).hexdigest()
        for r in rows
        if str(r[0]).strip()
    )


def chunk_fingerprint(con: sqlite3.Connection, doc_id: int) -> frozenset[str]:
    """The fingerprint of an indexed document, from its chunks."""
    return frozenset(
        hashlib.sha1(" ".join(t.split()).encode("utf-8")).hexdigest()
        for (t,) in con.execute(
            "SELECT text FROM chunks WHERE doc_id = ? AND kind != 'figure'", (doc_id,)
        )
        if t.strip()
    )


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two fingerprints; 1.0 for the same text."""
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


DUPLICATE_THRESHOLD = 0.9


def live_captures_of(con: sqlite3.Connection, url: str) -> list[dict[str, Any]]:
    """The live (not retired) captures of one canonical URL, oldest first."""
    out = []
    for r in con.execute(
        "SELECT id, meta FROM documents WHERE source_url = ?"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id",
        (url,),
    ):
        meta = json.loads(r["meta"] or "{}")
        out.append({"doc_id": r["id"], "meta": meta})
    return out


def capture_rank(meta: dict[str, Any]) -> tuple[int, int]:
    """Which of two captures of one page to keep: a snapshot over a bare
    DOM, an extracted one over one not yet read; ties go to the older."""
    mode = (meta.get("capture") or {}).get("mode")
    return (1 if mode == "snapshot" else 0, 1 if meta.get("extraction") else 0)


@_serialized
def note_recapture(
    con: sqlite3.Connection, doc_id: int, *, session: str | None, by: str | None
) -> None:
    """The page was sent again and said the same: remembered on the
    document, no new row."""
    meta = get_meta(con, doc_id)
    again = meta.setdefault("recaptured", [])
    again.append(
        {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "session": session,
            "by": by,
        }
    )
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()


def dedupe_captures(
    con: sqlite3.Connection,
    *,
    threshold: float = DUPLICATE_THRESHOLD,
    commit: bool = True,
) -> dict[str, Any]:
    """Among the live captures of each URL, keep one (``capture_rank``)
    and retire the others whose text fingerprint matches it. Two captures
    of a page that changed in between both stay. Returns the groups."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in con.execute(
        "SELECT id, source_url, meta FROM documents WHERE source_url IS NOT NULL"
        " AND json_extract(meta, '$.source') = 'capture'"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
    ):
        groups.setdefault(r["source_url"], []).append(
            {"doc_id": r["id"], "meta": json.loads(r["meta"] or "{}")}
        )
    report: dict[str, Any] = {"groups": [], "retired": 0, "kept_apart": 0}
    for url, docs in groups.items():
        if len(docs) < 2:
            continue
        keeper = max(docs, key=lambda d: (capture_rank(d["meta"]), -d["doc_id"]))
        kfp = chunk_fingerprint(con, keeper["doc_id"])
        gone, apart = [], []
        for d in docs:
            if d is keeper:
                continue
            s = similarity(kfp, chunk_fingerprint(con, d["doc_id"]))
            if s >= threshold:
                gone.append(d["doc_id"])
                if commit:
                    retire_document(
                        con,
                        d["doc_id"],
                        reason="duplicate capture",
                        duplicate_of=keeper["doc_id"],
                        by="dedupe",
                    )
            else:
                apart.append((d["doc_id"], round(s, 2)))
        report["groups"].append(
            {"url": url, "keep": keeper["doc_id"], "retire": gone, "apart": apart}
        )
        report["retired"] += len(gone)
        report["kept_apart"] += len(apart)
    return report


# ---------------------------------------------------------------- domains
# Which ontology modules a document is read against (``meta.domains``): the
# research papers see the research module, the family photos the family
# module, a document that is both sees both. No domain set means every
# module, which is what the library had before modules existed. Set by a
# rule at assignment time (``assign_domains``, rules in prax.yaml), by hand
# (``set_domains``), or by an importer that knows its source.


def document_domains(con: sqlite3.Connection, doc_id: int) -> list[str] | None:
    """The document's domains, None when it belongs to every module."""
    row = con.execute(
        "SELECT json_extract(meta, '$.domains') FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    return list(json.loads(row[0])) if row[0] else None


def _check_domains(domains: list[str]) -> list[str]:
    modules = ontology.current().modules
    out = []
    for d in domains:
        if d == ontology.CORE or d not in modules:
            raise ValueError(
                f"unknown domain {d!r}; the modules are"
                f" {sorted(m for m in modules if m != ontology.CORE)}"
            )
        if d not in out:
            out.append(d)
    return out


@_serialized
def set_domains(
    con: sqlite3.Connection,
    doc_id: int,
    domains: list[str] | None,
    *,
    by: str = "human",
) -> list[str] | None:
    """Replace a document's domain set (None: every module). Names must be
    modules of the current ontology other than core."""
    meta = get_meta(con, doc_id)
    if domains is None:
        meta.pop("domains", None)
        meta.pop("domains_by", None)
    else:
        meta["domains"] = _check_domains(domains)
        meta["domains_by"] = by
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return meta.get("domains")


def add_domain(
    con: sqlite3.Connection, doc_id: int, domain: str, *, by: str = "human"
) -> list[str]:
    """Add a domain to a document that keeps its others (a family photo
    that also matters to the research)."""
    current = document_domains(con, doc_id) or []
    if domain in current:
        return current
    return set_domains(con, doc_id, [*current, domain], by=by) or []


def remove_domain(
    con: sqlite3.Connection, doc_id: int, domain: str
) -> list[str] | None:
    """Take a domain away; the last one leaves the document in every module."""
    current = document_domains(con, doc_id)
    if not current or domain not in current:
        return current
    rest = [d for d in current if d != domain]
    return set_domains(con, doc_id, rest or None)


def documents_in_domain(con: sqlite3.Connection, domain: str) -> list[int]:
    """Documents whose domain set names ``domain`` (documents without a set
    are in every module but are not listed here: a re-run per domain means
    the documents that were assigned to it)."""
    return [
        r[0]
        for r in con.execute(
            "SELECT d.id FROM documents d, json_each(d.meta, '$.domains') j"
            " WHERE j.value = ? ORDER BY d.id",
            (domain,),
        )
    ]


def _rule_matches(rule: dict[str, Any], doc: dict[str, Any]) -> bool:
    meta = doc["meta"]
    m = rule.get("match") or {}
    if not m:
        return True
    if "source" in m and meta.get("source") != m["source"]:
        return False
    if "mime" in m and not (doc["mime"] or "").startswith(m["mime"]):
        return False
    if "path" in m and not (doc["original_path"] or "").lower().startswith(
        str(m["path"]).lower()
    ):
        return False
    if "collection" in m:
        names = [c.lower() for c in meta.get("collections") or []]
        if str(m["collection"]).lower() not in names:
            return False
    if "tag" in m:
        tags = [t.lower() for t in meta.get("tags") or []]
        if str(m["tag"]).lower() not in tags:
            return False
    return True


@_serialized
def assign_domains(
    con: sqlite3.Connection,
    rules: list[dict[str, Any]],
    *,
    force: bool = False,
    commit: bool = True,
    ids: list[int] | None = None,
) -> dict[str, int]:
    """Give every document without a domain set (all of them with ``force``;
    only ``ids`` when given) the domains of the first rule it matches. A
    rule is ``{match: {source, mime, path, collection, tag}, domains:
    [...]}``; a rule without ``match`` is the default. Documents whose set
    a person wrote by hand (``domains_by: human``) are never touched.
    Returns counts per rule index and ``unmatched``."""
    counts: dict[str, int] = {"unmatched": 0}
    for rule in rules:
        _check_domains(list(rule.get("domains") or []))
    sql = "SELECT id, mime, original_path, meta FROM documents"
    args: tuple[Any, ...] = ()
    if ids is not None:
        sql += f" WHERE id IN ({','.join('?' * len(ids))})"
        args = tuple(ids)
    for r in con.execute(sql, args).fetchall():
        meta = json.loads(r["meta"] or "{}")
        if meta.get("domains_by") == "human":
            continue
        if meta.get("domains") and not force:
            continue
        doc = {"mime": r["mime"], "original_path": r["original_path"], "meta": meta}
        for i, rule in enumerate(rules):
            if _rule_matches(rule, doc):
                key = f"rule {i}"
                counts[key] = counts.get(key, 0) + 1
                if commit:
                    meta["domains"] = list(rule["domains"])
                    meta["domains_by"] = "rule"
                    con.execute(
                        "UPDATE documents SET meta = ? WHERE id = ?",
                        (json.dumps(meta), r["id"]),
                    )
                break
        else:
            counts["unmatched"] += 1
    if commit:
        con.commit()
    return counts


# -------------------------------------------------------------- promotion
# A document worth the expensive model: flagged by a person, by Claude Code
# over MCP, or by the store itself when the document joins a project or
# becomes a synthesis source. The flag lives in ``meta.promote``; the pass
# is ``extract_graph.py --promoted`` with the ``promote`` step's model, and
# a document counts as done when that producer's stamp is in its history.

PROMOTE_WEIGHTS = {"project": 5, "synthesis": 4, "page": 3, "cited": 1}


def _set_promote(
    con: sqlite3.Connection, doc_id: int, *, by: str, reason: str | None
) -> dict[str, Any] | None:
    meta = get_meta(con, doc_id)
    if meta.get("promote"):
        return None
    meta["promote"] = {
        "by": by,
        "reason": reason,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    return meta["promote"]


@_serialized
def promote(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    by: str = "human",
    reason: str | None = None,
) -> dict[str, Any]:
    """Flag a document for the expensive pass. Returns the flag; a document
    already flagged keeps its first flag."""
    if (
        con.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone()
        is None
    ):
        raise KeyError(f"no such document: {doc_id}")
    flag = _set_promote(con, doc_id, by=by, reason=reason)
    con.commit()
    return flag or get_meta(con, doc_id)["promote"]


@_serialized
def unpromote(con: sqlite3.Connection, doc_id: int) -> bool:
    meta = get_meta(con, doc_id)
    if not meta.pop("promote", None):
        return False
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return True


def expected_version(
    meta: dict[str, Any], onto: ontology.Ontology | None = None
) -> str:
    """The ontology version a reading of this document should be stamped
    with: the version of its own domains' subset, or of the whole ontology
    when it has no domain set."""
    onto = onto or ontology.current()
    return onto.for_domains(meta.get("domains") or None).version


def extracted_by(
    meta: dict[str, Any], producer: str, *, ontology_version: str | None = None
) -> bool:
    """Whether ``producer`` has read the document, now or in its history;
    with ``ontology_version``, only a reading under that version counts (a
    pass under an older ontology is not the pass being asked for)."""
    stamps = [meta.get("extraction") or {}, *(meta.get("extraction_history") or [])]
    return any(
        s.get("extractor") == producer
        and (ontology_version is None or s.get("ontology_version") == ontology_version)
        for s in stamps
    )


@_serialized
def promoted_documents(
    con: sqlite3.Connection, *, producer: str | None = None
) -> list[dict[str, Any]]:
    """Flagged documents, oldest flag first; ``done`` says whether
    ``producer`` has read each one under the current ontology."""
    onto = ontology.current()
    out = []
    for r in con.execute(
        "SELECT id, title, meta FROM documents"
        " WHERE json_extract(meta, '$.promote') IS NOT NULL"
        " ORDER BY json_extract(meta, '$.promote.at'), id"
    ):
        meta = json.loads(r["meta"] or "{}")
        out.append(
            {
                "doc_id": r["id"],
                "title": r["title"],
                "promote": meta["promote"],
                "domains": meta.get("domains"),
                "done": bool(producer)
                and extracted_by(
                    meta, producer, ontology_version=expected_version(meta, onto)
                ),
            }
        )
    return out


@_serialized
def promotion_candidates(
    con: sqlite3.Connection, *, limit: int = 30
) -> list[dict[str, Any]]:
    """Documents the library keeps coming back to, not yet flagged: scored
    by project membership, synthesis sources, notes on them, and citations
    from other library documents (weights ``PROMOTE_WEIGHTS``)."""
    titles: dict[str, int] = {}
    promoted: set[int] = set()
    for r in con.execute(
        "SELECT id, title, meta FROM documents WHERE title IS NOT NULL"
        " AND text_hash IS NOT NULL AND json_extract(meta, '$.retired') IS NULL"
        " AND coalesce(json_extract(meta, '$.source'), '') != 'wiki'"
    ):
        titles.setdefault(r["title"], r["id"])
        if json.loads(r["meta"] or "{}").get("promote"):
            promoted.add(r["id"])
    counts: dict[int, dict[str, int]] = {}

    def bump(name: str, key: str, n: int = 1) -> None:
        doc_id = titles.get(name)
        if doc_id is None or doc_id in promoted:
            return
        bucket = counts.setdefault(doc_id, {})
        bucket[key] = bucket.get(key, 0) + n

    for r in con.execute(
        "SELECT t.name AS name, count(DISTINCT x.source_doc) AS n FROM edges x"
        " JOIN entities t ON t.id = x.dst"
        " WHERE x.rel = 'cites' AND x.valid_to IS NULL AND t.type = 'paper'"
        " AND x.source_doc IS NOT NULL GROUP BY t.name"
    ):
        bump(r["name"], "cited", r["n"])
    for r in con.execute(
        "SELECT x.rel AS rel, t.name AS name, count(*) AS n FROM edges x"
        " JOIN entities t ON t.id = x.dst"
        " WHERE x.rel IN ('annotates', 'synthesizes') AND x.valid_to IS NULL"
        " GROUP BY x.rel, t.name"
    ):
        bump(r["name"], "synthesis" if r["rel"] == "synthesizes" else "page", r["n"])
    for r in con.execute(
        "SELECT s.name AS name, count(*) AS n FROM edges x"
        " JOIN entities s ON s.id = x.src"
        " WHERE x.rel = 'part_of' AND x.valid_to IS NULL AND x.producer = 'page'"
        " GROUP BY s.name"
    ):
        bump(r["name"], "project", r["n"])
    ranked = []
    for doc_id, c in counts.items():
        score = sum(PROMOTE_WEIGHTS[k] * v for k, v in c.items())
        ranked.append({"doc_id": doc_id, "score": score, **c})
    ranked.sort(key=lambda d: (-d["score"], d["doc_id"]))
    ranked = ranked[:limit]
    for d in ranked:
        d["title"] = con.execute(
            "SELECT title FROM documents WHERE id = ?", (d["doc_id"],)
        ).fetchone()[0]
    return ranked


@_serialized
def restamp_ontology(
    con: sqlite3.Connection, src: str, dst: str, *, commit: bool = True
) -> int:
    """Rewrite ``meta.extraction.ontology_version`` (and the history entries)
    from ``src`` to ``dst`` on every document: the ontology's version string
    changed shape without a change in what it accepts (the split into
    modules). Edges keep their version. Returns the documents touched."""
    n = 0
    for r in con.execute(
        "SELECT id, meta FROM documents WHERE meta LIKE ?",
        (f'%"ontology_version": "{src}"%',),
    ).fetchall():
        meta = json.loads(r["meta"] or "{}")
        changed = False
        for stamp in [
            meta.get("extraction") or {},
            *(meta.get("extraction_history") or []),
        ]:
            if stamp.get("ontology_version") == src:
                stamp["ontology_version"] = dst
                changed = True
        if changed:
            n += 1
            if commit:
                con.execute(
                    "UPDATE documents SET meta = ? WHERE id = ?",
                    (json.dumps(meta), r["id"]),
                )
    if commit:
        con.commit()
    return n


# ------------------------------------------------------------------ pages
# Living Markdown documents (migration 0006): notes on a document, ongoing
# projects, topic pages. A page is a document, so everything that applies
# to documents applies; its identity is the slug, every save is a new text
# artifact with an append-only revision row, and its relationships to other
# documents are edges. An agent never overwrites human text: ``write_page``
# refuses an agent revision over a human one unless told to; ``append_page``
# adds a section instead.

PAGE_KINDS = ("addendum", "project", "synthesis", "topic")
PAGE_AUTHORS = ("human", "agent")
_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    slug = _SLUG_CHARS.sub("-", text.lower()).strip("-")
    return slug[:80] or "page"


def _page_original(slug: str, text: str) -> bytes:
    """The archived original of a page: the first revision with an identity
    line, so two pages with the same opening text stay distinct documents."""
    return f"<!-- prax page: {slug} -->\n{text}".encode()


@_serialized
def page_titles(con: sqlite3.Connection) -> set[str]:
    """Titles of the documents that are pages: the only names a page or
    project entity may carry."""
    return {
        r[0]
        for r in con.execute(
            "SELECT d.title FROM pages p JOIN documents d ON d.id = p.doc_id"
        )
        if r[0]
    }


@_serialized
def get_page(con: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    """A page with its current text and revision list, or None."""
    row = con.execute(
        "SELECT p.doc_id, p.slug, p.kind, d.title, d.text_hash, d.meta"
        " FROM pages p JOIN documents d ON d.id = p.doc_id WHERE p.slug = ?",
        (slug,),
    ).fetchone()
    if row is None:
        return None
    meta = json.loads(row["meta"] or "{}")
    text = _read_archive(row["text_hash"]).decode("utf-8") if row["text_hash"] else ""
    revisions = [
        dict(r)
        for r in con.execute(
            "SELECT revision, author, note, created_at, text_hash FROM page_revisions"
            " WHERE doc_id = ? ORDER BY revision",
            (row["doc_id"],),
        )
    ]
    return {
        "doc_id": row["doc_id"],
        "slug": row["slug"],
        "kind": row["kind"],
        "title": row["title"],
        "text": text,
        "revision": revisions[-1]["revision"] if revisions else 0,
        "author": revisions[-1]["author"] if revisions else None,
        "revisions": revisions,
        "meta": meta,
    }


@_serialized
def page_revision_text(con: sqlite3.Connection, slug: str, revision: int) -> str:
    row = con.execute(
        "SELECT r.text_hash FROM page_revisions r JOIN pages p ON p.doc_id = r.doc_id"
        " WHERE p.slug = ? AND r.revision = ?",
        (slug, revision),
    ).fetchone()
    if row is None:
        raise KeyError(f"no revision {revision} of page {slug!r}")
    return _read_archive(row["text_hash"]).decode("utf-8")


@_serialized
def list_pages(
    con: sqlite3.Connection, *, kind: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    where = "WHERE p.kind = ?" if kind else ""
    args: tuple[Any, ...] = (kind, limit) if kind else (limit,)
    rows = con.execute(
        f"""
        SELECT p.doc_id, p.slug, p.kind, d.title,
               (SELECT max(revision) FROM page_revisions r WHERE r.doc_id = p.doc_id)
                   AS revision,
               (SELECT author FROM page_revisions r WHERE r.doc_id = p.doc_id
                ORDER BY revision DESC LIMIT 1) AS author,
               (SELECT max(created_at) FROM page_revisions r WHERE r.doc_id = p.doc_id)
                   AS updated_at
        FROM pages p JOIN documents d ON d.id = p.doc_id {where}
        ORDER BY updated_at DESC LIMIT ?
        """,
        args,
    ).fetchall()
    return [dict(r) for r in rows]


def write_page(
    con: sqlite3.Connection,
    slug: str,
    text: str,
    *,
    title: str | None = None,
    kind: str = "topic",
    author: str = "human",
    note: str | None = None,
    annotates: list[int] | None = None,
    part_of: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create or replace a page's text as a new revision.

    ``annotates`` names documents this page is about (``page --annotates-->
    paper`` edges, the page as source document); ``part_of`` names a
    project page by slug (``page --part_of--> project``). An ``agent``
    revision over a ``human`` one is refused unless ``force``; use
    ``append_page`` for agent additions. Returns ``{doc_id, slug, revision,
    created}``.
    """
    if kind not in PAGE_KINDS:
        raise ValueError(f"kind must be one of {PAGE_KINDS}")
    if author not in PAGE_AUTHORS:
        raise ValueError(f"author must be one of {PAGE_AUTHORS}")
    slug = slugify(slug)
    existing = con.execute(
        "SELECT p.doc_id, p.kind, d.title FROM pages p"
        " JOIN documents d ON d.id = p.doc_id"
        " WHERE p.slug = ?",
        (slug,),
    ).fetchone()
    created = existing is None
    if created:
        title = title or slug.replace("-", " ").capitalize()
        reg = register(
            con,
            _page_original(slug, text),
            mime="text/markdown",
            title=title,
            meta={"source": "wiki", "page": {"slug": slug, "kind": kind}},
        )
        doc_id = reg["doc_id"]
        con.execute(
            "INSERT INTO pages (doc_id, slug, kind) VALUES (?, ?, ?)",
            (doc_id, slug, kind),
        )
        revision = 1
    else:
        doc_id = existing["doc_id"]
        kind = existing["kind"]
        last = con.execute(
            "SELECT revision, author FROM page_revisions WHERE doc_id = ?"
            " ORDER BY revision DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        if last and last["author"] == "human" and author == "agent" and not force:
            raise PermissionError(
                f"page {slug!r} was last written by a person; append_page adds"
                " a section, force=True overwrites"
            )
        revision = (last["revision"] if last else 0) + 1
        if title:
            con.execute("UPDATE documents SET title = ? WHERE id = ?", (title, doc_id))
    indexed = index_text(con, doc_id, text, text_source=f"page/{author}")
    con.execute(
        "INSERT INTO page_revisions (doc_id, revision, text_hash, author, note)"
        " VALUES (?, ?, ?, ?, ?)",
        (doc_id, revision, indexed["text_hash"], author, note),
    )
    meta = get_meta(con, doc_id)
    meta["page"] = {
        **(meta.get("page") or {}),
        "slug": slug,
        "kind": kind,
        "revision": revision,
        "author": author,
    }
    set_meta(con, doc_id, meta)
    page_title = con.execute(
        "SELECT title FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()[0]
    page_type = "project" if kind == "project" else "page"
    # a synthesis draws on its sources; any other page annotates one document
    source_rel = "synthesizes" if kind == "synthesis" else "annotates"
    for target in annotates or []:
        t = con.execute(
            "SELECT title FROM documents WHERE id = ?", (target,)
        ).fetchone()
        if t is None or not t[0]:
            raise KeyError(f"no such document to annotate: {target}")
        target_type = (
            "page"
            if con.execute("SELECT 1 FROM pages WHERE doc_id = ?", (target,)).fetchone()
            else "paper"
        )
        edge = Edge(page_title, page_type, source_rel, t[0], target_type)
        if kind == "synthesis" and target_type == "paper":
            _set_promote(con, target, by="page", reason=f"source of synthesis {slug}")
        if not find_edges(con, edge):
            link(
                con,
                edge,
                confidence="EXTRACTED",
                source_doc=doc_id,
                evidence=f"page {slug} revision {revision}",
                producer="page",
                run=f"{slug}@{revision}",
            )
    if part_of:
        project = con.execute(
            "SELECT d.title FROM pages p JOIN documents d ON d.id = p.doc_id"
            " WHERE p.slug = ? AND p.kind = 'project'",
            (slugify(part_of),),
        ).fetchone()
        if project is None:
            raise KeyError(f"no project page {part_of!r}")
        edge = Edge(page_title, page_type, "part_of", project[0], "project")
        if not find_edges(con, edge):
            link(
                con,
                edge,
                confidence="EXTRACTED",
                source_doc=doc_id,
                evidence=f"page {slug} revision {revision}",
                producer="page",
                run=f"{slug}@{revision}",
            )
    con.commit()
    return {"doc_id": doc_id, "slug": slug, "revision": revision, "created": created}


def append_page(
    con: sqlite3.Connection,
    slug: str,
    section: str,
    *,
    heading: str | None = None,
    author: str = "agent",
    note: str | None = None,
    annotates: list[int] | None = None,
) -> dict[str, Any]:
    """Add a section to an existing page as a new revision: the agent's way
    of contributing without touching what a person wrote. ``annotates``
    adds ``annotates`` edges to the documents the section rests on."""
    page = get_page(con, slugify(slug))
    if page is None:
        raise KeyError(f"no page {slug!r}")
    block = section.strip()
    if heading:
        block = f"## {heading}\n\n{block}"
    text = page["text"].rstrip() + "\n\n" + block + "\n"
    return write_page(
        con,
        page["slug"],
        text,
        author=author,
        note=note,
        force=True,
        kind=page["kind"],
        annotates=annotates,
    )


@_serialized
def add_to_project(
    con: sqlite3.Connection, project_slug: str, doc_id: int
) -> int | None:
    """``paper --part_of--> project`` for a library document; the edge id,
    or None when it already exists."""
    project = con.execute(
        "SELECT p.doc_id, d.title FROM pages p JOIN documents d ON d.id = p.doc_id"
        " WHERE p.slug = ? AND p.kind = 'project'",
        (slugify(project_slug),),
    ).fetchone()
    if project is None:
        raise KeyError(f"no project page {project_slug!r}")
    doc = con.execute("SELECT title FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if doc is None or not doc[0]:
        raise KeyError(f"no such document: {doc_id}")
    is_page = con.execute("SELECT 1 FROM pages WHERE doc_id = ?", (doc_id,)).fetchone()
    edge = Edge(
        doc[0], "page" if is_page else "paper", "part_of", project["title"], "project"
    )
    if find_edges(con, edge):
        return None
    if not is_page:
        _set_promote(con, doc_id, by="page", reason=f"member of project {project_slug}")
    return link(
        con,
        edge,
        confidence="EXTRACTED",
        source_doc=project["doc_id"],
        evidence=f"project {project_slug}",
        producer="page",
        run=slugify(project_slug),
    )


# --------------------------------------------------------- document field
# What a document *is*, in a few lines, indexed apart from its chunks
# (migration 0005): title, kind words, creators, venue, the extraction
# summary, an image description's opening paragraph. A short field makes a
# match in it strong under BM25 and gives one vector per document, so a
# query naming a thing finds the document that is that thing, not the
# documents that mention it most.

DOCTYPES: dict[str, str] = {
    "pdf": "d.mime = 'application/pdf'",
    "web": "d.mime IN ('text/html', 'application/xhtml+xml')",
    "image": "d.mime LIKE 'image/%'",
    "text": "d.mime = 'text/plain'",
    "note": "json_extract(d.meta, '$.zotero.kind') = 'note'",
    "page": "json_extract(d.meta, '$.source') = 'wiki'",
}
_KIND_WORDS = {
    "application/pdf": "PDF document",
    "text/html": "web page",
    "application/xhtml+xml": "web page",
    "text/plain": "text",
}


def document_field(con: sqlite3.Connection, doc_id: int) -> str | None:
    """The retrieval field of a document, or None when it does not exist."""
    row = con.execute(
        """
        SELECT d.title, d.mime, d.meta,
               (SELECT kind FROM chunks c WHERE c.doc_id = d.id ORDER BY seq LIMIT 1)
                   AS first_kind
        FROM documents d WHERE d.id = ?
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        return None
    meta = json.loads(row["meta"] or "{}")
    if meta.get("retired"):
        return None  # a retired document has no retrieval field
    mime = row["mime"] or ""
    words: list[str] = []
    if mime.startswith("image/"):
        words.append("image")
    elif mime in _KIND_WORDS:
        words.append(_KIND_WORDS[mime])
    z = meta.get("zotero") or {}
    if z.get("kind") == "note":
        words.append("note")
    page = meta.get("page") or {}
    if page.get("kind"):
        words.append(
            {
                "addendum": "note page",
                "project": "project page",
                "synthesis": "synthesis page",
            }.get(page["kind"], "wiki page")
        )
    if row["first_kind"] == "code":
        words.append("source code")
    source = str(meta.get("text_source") or "")
    if source.startswith("claude-vision"):
        words.append("image description")
    parts = [row["title"] or "", " ".join(words)]
    creators = [c.get("name") for c in meta.get("creators", []) if c.get("name")]
    if creators:
        parts.append("by " + ", ".join(creators[:6]))
    fields = meta.get("fields") or {}
    venue = fields.get("publicationTitle") or fields.get("proceedingsTitle")
    bits = [b for b in (venue, str(meta.get("date") or "")[:4]) if b]
    if bits:
        parts.append(" ".join(bits))
    if meta.get("summary"):
        parts.append(str(meta["summary"]))
    if source.startswith("claude-vision"):
        shows = con.execute(
            "SELECT text FROM chunks WHERE doc_id = ?"
            " AND heading LIKE '%What it shows%' ORDER BY seq LIMIT 1",
            (doc_id,),
        ).fetchone()
        if shows:
            lines = [ln for ln in shows["text"].splitlines() if not ln.startswith("#")]
            parts.append(" ".join(lines).strip()[:1200])
    return "\n".join(p for p in parts if p.strip())


def _refresh_document_field(con: sqlite3.Connection, doc_id: int) -> bool:
    """Rewrite the field row when it changed; a changed field also drops the
    document's vector bookkeeping so it is embedded again. No commit."""
    field = document_field(con, doc_id)
    if field is None:
        return False
    old = con.execute(
        "SELECT field FROM documents_fts WHERE rowid = ?", (doc_id,)
    ).fetchone()
    if old is not None and old[0] == field:
        return False
    con.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
    con.execute(
        "INSERT INTO documents_fts(rowid, field) VALUES (?, ?)", (doc_id, field)
    )
    con.execute("DELETE FROM document_embeddings WHERE doc_id = ?", (doc_id,))
    return True


@_serialized
def refresh_document_fields(
    con: sqlite3.Connection, doc_ids: list[int] | None = None
) -> int:
    """Rebuild the field of the given documents (all when None); returns
    how many changed. The backfill after the migration, and the repair
    after a change to ``document_field``."""
    ids = doc_ids or [r[0] for r in con.execute("SELECT id FROM documents ORDER BY id")]
    changed = 0
    for i, doc_id in enumerate(ids, 1):
        if _refresh_document_field(con, doc_id):
            changed += 1
        if i % 500 == 0:
            con.commit()
    con.commit()
    return changed


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
    sql = (
        f"SELECT id FROM documents WHERE ({' OR '.join(clauses)})"
        " AND json_extract(meta, '$.retired') IS NULL"
    )
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
    expr: str | None = None,
) -> list[dict[str, Any]]:
    """BM25 over chunks. ``snippets=False`` skips the snippet() call, which
    reads every matched chunk's text and dominates the cost of deep lists;
    ``_fts_snippets`` fills them in for the few hits that survive fusion."""
    expr = expr or _fts_query(query)
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
    con: sqlite3.Connection, query: str, chunk_ids: list[int], expr: str | None = None
) -> dict[int, str]:
    """Match-marked snippets for a handful of chunks (the fused hits)."""
    expr = expr or _fts_query(query)
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


def _field_fts_search(
    con: sqlite3.Connection, query: str, limit: int, expr: str | None = None
) -> list[dict[str, Any]]:
    """BM25 over the document field; hits carry no chunk yet."""
    expr = expr or _fts_query(query)
    if expr is None:
        return []
    rows = con.execute(
        """
        SELECT f.rowid AS doc_id, d.title,
               snippet(documents_fts, 0, '[', ']', '…', 14) AS snippet,
               bm25(documents_fts) AS score
        FROM documents_fts f JOIN documents d ON d.id = f.rowid
        WHERE documents_fts MATCH ? ORDER BY score LIMIT ?
        """,
        (expr, limit),
    ).fetchall()
    return [_field_hit(r["doc_id"], r["title"], r["snippet"], r["score"]) for r in rows]


def _field_vec_search(
    con: sqlite3.Connection, model: str, vector: Any, limit: int
) -> list[dict[str, Any]]:
    """KNN over the document-field index."""
    idx = _doc_index(model, writable=False)
    if idx is None:
        return []
    found = idx.search(vector, limit)
    if not found:
        return []
    distance = dict(found)
    ids = list(distance)
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT d.id, d.title, f.field FROM documents d"
        f" JOIN documents_fts f ON f.rowid = d.id WHERE d.id IN ({marks})",
        ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    return [
        _field_hit(i, by_id[i]["title"], by_id[i]["field"][:160], distance[i])
        for i in ids
        if i in by_id
    ]


def _field_hit(doc_id: int, title: str, snippet: str, score: float) -> dict[str, Any]:
    return {
        "chunk_id": None,
        "doc_id": doc_id,
        "title": title,
        "snippet": snippet,
        "score": score,
        "kind": None,
        "heading": [],
        "page": None,
    }


def _fill_chunks(
    con: sqlite3.Connection,
    hits: list[dict[str, Any]],
    query: str,
    expr: str | None = None,
) -> None:
    """A hit that came from the document field alone gets the document's
    chunk that matches the query best, else its first chunk, so every hit
    opens somewhere; the field snippet stays, it says why the document
    matched."""
    expr = expr or _fts_query(query)
    for h in hits:
        if h.get("chunk_id") is not None:
            continue
        row = None
        if expr is not None:
            row = con.execute(
                """
                SELECT c.id, c.kind, c.locator, c.heading
                FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
                WHERE chunks_fts MATCH ? AND c.doc_id = ?
                ORDER BY bm25(chunks_fts) LIMIT 1
                """,
                (expr, h["doc_id"]),
            ).fetchone()
        if row is None:
            row = con.execute(
                "SELECT id, kind, locator, heading FROM chunks WHERE doc_id = ?"
                " ORDER BY seq LIMIT 1",
                (h["doc_id"],),
            ).fetchone()
        if row is None:
            continue
        h["chunk_id"] = row["id"]
        h.update(_chunk_shape(row))


def _filter_doctype(
    con: sqlite3.Connection, hits: list[dict[str, Any]], doctype: str
) -> list[dict[str, Any]]:
    if not hits:
        return hits
    ids = list({h["doc_id"] for h in hits})
    marks = ",".join("?" * len(ids))
    keep = {
        r[0]
        for r in con.execute(
            f"SELECT d.id FROM documents d WHERE d.id IN ({marks})"
            f" AND {DOCTYPES[doctype]}",
            ids,
        )
    }
    return [h for h in hits if h["doc_id"] in keep]


def _rrf(
    ranked: list[list[dict[str, Any]]],
    names: list[str],
    limit: int,
    weights: dict[str, float] | None = None,
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
        weight = (weights or {}).get(name, 1.0)
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
                entry = {**hit, "score": 0.0, **{f"{n}_rank": None for n in names}}
                fused[doc] = entry
            elif entry.get("chunk_id") is None and hit.get("chunk_id") is not None:
                # a field-only entry adopts the first chunk another side found
                for k in ("chunk_id", "kind", "heading", "page"):
                    entry[k] = hit[k]
            entry["score"] += weight / (RRF_K + rank)
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
    status["documents"] = {
        r[0]: r[1]
        for r in con.execute(
            "SELECT model, count(*) FROM document_embeddings GROUP BY model"
        )
    }
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
    doctype: str | None = None,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """Search returning compact snippets + ids (agent-shaped).

    ``hybrid`` fuses four rank lists at document level: chunk BM25, chunk
    KNN, and BM25 and KNN over the document field (title, kind, summary;
    ``documents_fts``), so a query that names what a document *is* finds it
    even when other documents mention the term more. ``doctype`` keeps
    documents of one type: ``pdf``, ``web``, ``image``, ``text``, ``note``.

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
    if doctype is not None and doctype not in DOCTYPES:
        raise ValueError(f"doctype must be one of {tuple(DOCTYPES)}")
    reranker = rerank_mod.current() if rerank is None or rerank else None
    if rerank and reranker is None:
        raise ValueError("rerank requested but PRAX_RERANK names no model")
    if domain is not None and domain not in ontology.current().modules:
        raise ValueError(f"unknown domain {domain!r}")
    fetch = max(limit, RERANK_DEPTH) if reranker else limit
    if domain:
        fetch *= 3
    hits = _search_hits(con, query, fetch, kind, mode, doctype)
    if domain:
        hits = _filter_domain(con, hits, domain)
    if reranker is not None and hits:
        hits = _apply_rerank(con, reranker, query, hits)
    return hits[:limit]


def _filter_domain(
    con: sqlite3.Connection, hits: list[dict[str, Any]], domain: str
) -> list[dict[str, Any]]:
    """Keep hits whose document is in ``domain``; a document without a
    domain set is in every module and stays."""
    ids = {h["doc_id"] for h in hits}
    if not ids:
        return hits
    marks = ",".join("?" * len(ids))
    rows = con.execute(
        "SELECT id, json_extract(meta, '$.domains') FROM documents"
        f" WHERE id IN ({marks})",
        tuple(ids),
    ).fetchall()
    allowed = {r[0] for r in rows if not r[1] or domain in json.loads(r[1])}
    return [h for h in hits if h["doc_id"] in allowed]


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
    con: sqlite3.Connection,
    query: str,
    limit: int,
    kind: str | None,
    mode: str,
    doctype: str | None = None,
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
    fetch = limit * 4 if doctype else limit
    # the query as terms with the library's own expansions of its acronyms;
    # one OR expression for recall, one AND expression for the tier that
    # wants every term present (only when there is more than one term)
    terms = expand_query(con, query)
    or_expr = _expr(terms, all_terms=False)
    and_expr = _expr(terms, all_terms=True) if len(terms) > 1 else None
    if mode == "fts":  # the raw chunk list, expanded but not fused
        hits = _fts_search(con, query, fetch, kind, expr=or_expr)
        return _finish(con, hits, query, limit, doctype, expr=or_expr)
    depth = max(limit * 3, RRF_DEPTH)
    # keyword lists: any term (recall), optionally every term, and the rare
    # terms alone: "adaa iir" must not be decided by the thousands of chunks
    # that say "iir"
    weights: dict[str, float] = {
        "fts_all": ALL_TERMS_WEIGHT,
        "fts_rare": RARE_TERMS_WEIGHT,
    }
    lists = [_fts_search(con, query, depth, kind, snippets=False, expr=or_expr)]
    names = ["fts"]
    if and_expr and ALL_TERMS_WEIGHT > 0:
        lists.append(
            _fts_search(con, query, depth, kind, snippets=False, expr=and_expr)
        )
        names.append("fts_all")
    rare = _rare_terms(con, terms) if RARE_TERMS_WEIGHT > 0 else []
    if rare and len(rare) == len(terms):
        # the whole query is rare tokens ("adaa"): the keyword list carries
        # the weight itself, vector neighbours of letters must not outvote it
        weights["fts"] = RARE_TERMS_WEIGHT
    if rare and len(rare) < len(terms):
        rare_expr = _expr(rare, all_terms=True)
        lists.append(
            _fts_search(con, query, depth, kind, snippets=False, expr=rare_expr)
        )
        names.append("fts_rare")
    if not vectors_ready:
        if kind is None:  # hybrid without vectors: the field too
            lists.append(_field_fts_search(con, query, depth, expr=or_expr))
            names.append("field")
            weights["field"] = _field_weight(query)
        fused = _rrf(lists, names, fetch, weights)
        return _finish(con, fused, query, limit, doctype, expr=or_expr)
    assert emb is not None
    vector = emb.embed_query(expanded_text(terms) if VEC_EXPAND else query)
    if mode == "vec":
        return _finish(
            con,
            _vec_search(con, vector, fetch, kind),
            query,
            limit,
            doctype,
            expr=or_expr,
        )
    lists.append(_vec_search(con, vector, depth, kind))
    names.append("vec")
    if kind is None:  # the field has no chunk kind to filter by
        lists.append(_field_fts_search(con, query, depth, expr=or_expr))
        names.append("field")
        weights["field"] = _field_weight(query)
        lists.append(_field_vec_search(con, emb.name, vector, depth))
        names.append("dvec")
    fused = _rrf(lists, names, fetch, weights)
    return _finish(con, fused, query, limit, doctype, expr=or_expr)


def _field_weight(query: str) -> float:
    words = len(query.split())
    if words <= 3:
        return FIELD_WEIGHT
    span = max(1, FIELD_WEIGHT_WORDS - 3)
    return max(1.0, FIELD_WEIGHT - (FIELD_WEIGHT - 1.0) * (words - 3) / span)


def _finish(
    con: sqlite3.Connection,
    hits: list[dict[str, Any]],
    query: str,
    limit: int,
    doctype: str | None,
    expr: str | None = None,
) -> list[dict[str, Any]]:
    if doctype:
        hits = _filter_doctype(con, hits, doctype)
    hits = hits[:limit]
    field_only = {h["doc_id"] for h in hits if h.get("chunk_id") is None}
    _fill_chunks(con, hits, query, expr=expr)
    chunk_ids = [
        h["chunk_id"]
        for h in hits
        if h.get("chunk_id") is not None
        and h["doc_id"] not in field_only
        and h.get("fts_rank") is not None
    ]
    marked = _fts_snippets(con, query, chunk_ids, expr=expr) if chunk_ids else {}
    for h in hits:
        if h.get("chunk_id") in marked:
            h["snippet"] = marked[h["chunk_id"]]
    return hits


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
def pending_document_embeddings(
    con: sqlite3.Connection, model: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Document fields without a vector from ``model``: ``{doc_id, text}``."""
    sql = (
        "SELECT f.rowid AS doc_id, f.field AS text FROM documents_fts f"
        " LEFT JOIN document_embeddings e ON e.doc_id = f.rowid"
        " WHERE e.doc_id IS NULL OR e.model != ? ORDER BY f.rowid"
    )
    args: tuple[Any, ...] = (model,)
    if limit is not None:
        sql += " LIMIT ?"
        args = (model, limit)
    return [dict(r) for r in con.execute(sql, args)]


@_serialized
def count_pending_document_embeddings(con: sqlite3.Connection, model: str) -> int:
    return con.execute(
        "SELECT count(*) FROM documents_fts f"
        " LEFT JOIN document_embeddings e ON e.doc_id = f.rowid"
        " WHERE e.doc_id IS NULL OR e.model != ?",
        (model,),
    ).fetchone()[0]


@_serialized
def store_document_embeddings(
    con: sqlite3.Connection, items: list[tuple[int, Any]], model: str
) -> int:
    """Add ``(doc_id, vector)`` rows to the document index of ``model`` (in
    memory until ``save_document_vectors``) and record them."""
    if not vectors_available():
        raise RuntimeError("usearch is not installed; vectors cannot be stored")
    if not items:
        return 0
    idx = _doc_index(model, writable=True)
    assert idx is not None
    import numpy as np

    ids = [d for d, _ in items]
    idx.remove([d for d in ids if d in idx])
    idx.add(ids, np.vstack([np.asarray(v, dtype=np.float32) for _, v in items]))
    con.executemany(
        "INSERT INTO document_embeddings (doc_id, model) VALUES (?, ?)"
        " ON CONFLICT(doc_id) DO UPDATE SET model = excluded.model,"
        f" embedded_at = {_NOW}",
        [(d, model) for d in ids],
    )
    con.commit()
    return len(items)


@_serialized
def save_document_vectors(model: str) -> dict[str, Any]:
    idx = _doc_index(model, writable=True)
    assert idx is not None
    _drop_index_views(model)
    idx.save()
    return idx.stats()


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
    retired: bool = False,
) -> dict[str, Any]:
    """Documents without their text, newest first, for browsing.

    ``title`` is a case-insensitive substring; ``source`` matches
    ``meta.source``; ``mime_prefix`` a MIME type prefix; ``retired`` lists
    the retired documents instead of the live ones. Returns
    ``{"total", "items"}`` where each item carries the row, its decoded
    ``meta`` and its chunk count.
    """
    clauses: list[str] = [
        "json_extract(d.meta, '$.retired') IS " + ("NOT NULL" if retired else "NULL")
    ]
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


HUB_TYPES = ("concept", "method", "tool", "dataset")
CONTEXT_LIMIT = 8
CENTROID_CHUNKS = 64  # chunk vectors averaged for a document's similarity query
CENTROID_MIN_CHARS = 120  # shorter chunks are headers and template lines


@_serialized
def similar_documents(
    con: sqlite3.Connection, doc_id: int, *, limit: int = CONTEXT_LIMIT
) -> list[dict[str, Any]]:
    """Documents nearest to this one in vector space: the centroid of up to
    ``CENTROID_CHUNKS`` of its text chunks' vectors (spread over the
    document), one KNN, grouped by document, scored by the best hit."""
    return _similar_documents(con, doc_id, limit=limit)


def _similar_documents(
    con: sqlite3.Connection, doc_id: int, *, limit: int = CONTEXT_LIMIT
) -> list[dict[str, Any]]:
    """Two views fused by reciprocal rank: the document-field vector's
    neighbours (what the document is; one KNN over the document index) and
    the chunk-centroid neighbours (what it says; one KNN over the chunk
    index, grouped by document). The centroid skips chunks under
    ``CENTROID_MIN_CHARS`` (boilerplate headers) that pull it toward every
    document sharing the same template."""
    emb = embeddings.current()
    if emb is None:
        return []
    import numpy as np  # the embed extra; only reachable when an index exists

    depth = max(40, 5 * limit)
    lists: list[list[int]] = []
    doc_idx = _doc_index(emb.name, writable=False)
    if doc_idx is not None and (fv := doc_idx.get(doc_id)) is not None:
        found = doc_idx.search(fv, depth + 1)
        lists.append([int(k) for k, _ in found if int(k) != doc_id][:depth])
    idx = _index(emb.name, writable=False)
    if idx is not None:
        rows = con.execute(
            "SELECT c.id FROM chunks c JOIN chunk_embeddings e ON e.chunk_id = c.id"
            " WHERE c.doc_id = ? AND e.model = ? AND c.kind = 'text'"
            " AND length(c.text) >= ? ORDER BY c.seq",
            (doc_id, emb.name, CENTROID_MIN_CHARS),
        ).fetchall()
        ids = [r["id"] for r in rows]
        step = max(1, len(ids) // CENTROID_CHUNKS)
        vecs = [
            v for k in ids[::step][:CENTROID_CHUNKS] if (v := idx.get(k)) is not None
        ]
        if vecs:
            centroid = np.mean(np.stack(vecs), axis=0)
            norm = float(np.linalg.norm(centroid)) or 1.0
            found = idx.search(
                (centroid / norm).astype(np.float32), min(VEC_SEARCH_CAP, 40 * limit)
            )
            keys = [int(k) for k, _ in found]
            order: list[int] = []
            for i in range(0, len(keys), 500):
                part = keys[i : i + 500]
                marks = ",".join("?" * len(part))
                by_chunk = {
                    r["id"]: r["doc_id"]
                    for r in con.execute(
                        f"SELECT id, doc_id FROM chunks WHERE id IN ({marks})", part
                    )
                }
                for k in part:
                    d = by_chunk.get(k)
                    if d is not None and d != doc_id and d not in order:
                        order.append(d)
            lists.append(order[:depth])
    if not lists:
        return []
    scores: dict[int, float] = {}
    for ranked in lists:
        for rank, d in enumerate(ranked, 1):
            scores[d] = scores.get(d, 0.0) + 1.0 / (RRF_K + rank)
    top = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
    out = []
    for did, score in top:
        d = con.execute(
            "SELECT title, mime FROM documents WHERE id = ?", (did,)
        ).fetchone()
        if d is not None:
            out.append(
                {
                    "doc_id": did,
                    "title": d["title"],
                    "mime": d["mime"],
                    "score": round(score, 4),
                }
            )
    return out


def _doc_ids_by_title(con: sqlite3.Connection, titles: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(0, len(titles), 400):
        part = titles[i : i + 400]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            f"SELECT id, title FROM documents WHERE title IN ({marks}) ORDER BY id",
            part,
        ):
            out.setdefault(r["title"], r["id"])
    return out


def _docs_by_zotero_key(con: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, title, json_extract(meta, '$.zotero.kind') AS kind FROM documents
        WHERE EXISTS (SELECT 1 FROM json_each(meta, '$.zotero.keys') WHERE value = ?)
           OR EXISTS (SELECT 1 FROM json_each(meta, '$.zotero.items') WHERE value = ?)
           OR json_extract(meta, '$.zotero.parent') = ?
        ORDER BY id
        """,
        (key, key, key),
    ).fetchall()
    return [dict(r) for r in rows]


ASK_FACT_RELS_SKIPPED = ("cites",)  # dozens per paper; the passages carry them


@_serialized
def document_facts(
    con: sqlite3.Connection, doc_ids: list[int], *, limit: int = 8
) -> dict[int, list[dict[str, Any]]]:
    """What the graph records about each document, as its own edges: the
    relations from the document's entity, ``{doc_id: [{rel, name, type}]}``,
    at most ``limit`` per document, canonical entity names, ``cites``
    left out. The "what the library knows" part of an ask bundle."""
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in doc_ids}
    if not doc_ids:
        return out
    marks = ",".join("?" * len(doc_ids))
    skip = ",".join("?" * len(ASK_FACT_RELS_SKIPPED))
    rows = con.execute(
        f"""
        SELECT x.source_doc AS doc_id, x.rel, t.name, t.type FROM edges x
        JOIN documents d ON d.id = x.source_doc
        JOIN entities s ON s.id = x.src
        JOIN entities t0 ON t0.id = x.dst
        JOIN entities t ON t.id = COALESCE(t0.canonical_id, t0.id)
        WHERE x.source_doc IN ({marks}) AND x.valid_to IS NULL
          AND s.name = d.title AND x.rel NOT IN ({skip})
        ORDER BY x.source_doc, x.rel, t.name
        """,
        (*doc_ids, *ASK_FACT_RELS_SKIPPED),
    )
    seen: set[tuple[int, str, str]] = set()
    for r in rows:
        key = (r["doc_id"], r["rel"], r["name"])
        if key in seen or len(out[r["doc_id"]]) >= limit:
            continue
        seen.add(key)
        out[r["doc_id"]].append({"rel": r["rel"], "name": r["name"], "type": r["type"]})
    return out


@_serialized
def document_context(
    con: sqlite3.Connection, doc_id: int, *, limit: int = CONTEXT_LIMIT
) -> dict[str, Any] | None:
    """Everything that places a document in the library, for its page: the
    extraction summary and entities, citations in and out of the library,
    the nearest documents by vector, documents sharing its entities or its
    authors, and its Zotero neighbours (parent item, siblings, collections,
    tags). None when the document does not exist."""
    doc = con.execute(
        "SELECT id, title, meta FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if doc is None:
        return None
    title = doc["title"] or ""
    meta = json.loads(doc["meta"] or "{}")

    # the document's own edges, by the entity they point at
    entities: list[dict[str, Any]] = []
    author_ids: list[int] = []
    entity_ids: list[int] = []
    for r in con.execute(
        """
        SELECT x.rel, x.confidence, t.id AS tid, t.name, t.type FROM edges x
        JOIN entities s ON s.id = x.src JOIN entities t ON t.id = x.dst
        WHERE x.source_doc = ? AND x.valid_to IS NULL AND s.name = ?
        ORDER BY t.type, t.name
        """,
        (doc_id, title),
    ):
        if r["rel"] == "authored_by":
            author_ids.append(r["tid"])
            continue
        if r["rel"] in ("cites", "published_in"):
            continue
        entity_ids.append(r["tid"])
        entities.append(
            {
                "name": r["name"],
                "type": r["type"],
                "rel": r["rel"],
                "confidence": r["confidence"],
            }
        )

    # citations: what this document cites, and what cites it (source_doc is
    # the citing document); titles resolved to library documents where present
    cites_rows = con.execute(
        """
        SELECT DISTINCT t.name FROM edges x
        JOIN entities s ON s.id = x.src JOIN entities t ON t.id = x.dst
        WHERE x.rel = 'cites' AND x.valid_to IS NULL AND s.name = ? AND s.type = 'paper'
        ORDER BY t.name
        """,
        (title,),
    ).fetchall()
    cited_titles = [r["name"] for r in cites_rows]
    in_library = _doc_ids_by_title(con, cited_titles) if cited_titles else {}
    cites = sorted(
        ({"title": t, "doc_id": in_library.get(t)} for t in cited_titles),
        key=lambda c: (c["doc_id"] is None, c["title"].lower()),
    )
    cited_by = [
        dict(r)
        for r in con.execute(
            """
            SELECT DISTINCT x.source_doc AS doc_id, d.title FROM edges x
            JOIN entities t ON t.id = x.dst JOIN documents d ON d.id = x.source_doc
            WHERE x.rel = 'cites' AND x.valid_to IS NULL AND t.name = ?
              AND t.type = 'paper'
              AND x.source_doc != ?
            ORDER BY d.title
            """,
            (title, doc_id),
        )
    ]

    # documents sharing this one's entities, most shared first
    shared: list[dict[str, Any]] = []
    if entity_ids:
        marks = ",".join("?" * len(entity_ids))
        for r in con.execute(
            f"""
            SELECT x.source_doc AS doc_id, d.title, count(DISTINCT x.dst) AS n,
                   group_concat(DISTINCT t.name) AS names
            FROM edges x JOIN entities t ON t.id = x.dst
            JOIN documents d ON d.id = x.source_doc
            WHERE x.dst IN ({marks}) AND x.valid_to IS NULL AND x.source_doc != ?
            GROUP BY x.source_doc ORDER BY n DESC, d.title LIMIT ?
            """,
            (*entity_ids, doc_id, limit),
        ):
            shared.append(
                {
                    "doc_id": r["doc_id"],
                    "title": r["title"],
                    "count": r["n"],
                    "entities": (r["names"] or "").split(",")[:4],
                }
            )

    # other documents by the same authors
    same_authors: list[dict[str, Any]] = []
    if author_ids:
        marks = ",".join("?" * len(author_ids))
        for r in con.execute(
            f"""
            SELECT x.source_doc AS doc_id, d.title,
                   group_concat(DISTINCT a.name) AS authors
            FROM edges x JOIN entities a ON a.id = x.dst
            JOIN documents d ON d.id = x.source_doc
            WHERE x.rel = 'authored_by' AND x.dst IN ({marks}) AND x.valid_to IS NULL
              AND x.source_doc != ?
            GROUP BY x.source_doc ORDER BY count(*) DESC, d.title LIMIT ?
            """,
            (*author_ids, doc_id, limit),
        ):
            same_authors.append(
                {
                    "doc_id": r["doc_id"],
                    "title": r["title"],
                    "authors": (r["authors"] or "").split(","),
                }
            )

    # Zotero neighbours
    z = meta.get("zotero") or {}
    parent = None
    if z.get("parent"):
        found = [d for d in _docs_by_zotero_key(con, z["parent"]) if d["id"] != doc_id]
        parent = {
            "key": z["parent"],
            "doc_id": found[0]["id"] if found else None,
            "title": found[0]["title"] if found else None,
        }
    siblings: list[dict[str, Any]] = []
    seen = {doc_id}
    for key in list(z.get("items") or []) + ([z["parent"]] if z.get("parent") else []):
        for d in _docs_by_zotero_key(con, key):
            if d["id"] not in seen:
                seen.add(d["id"])
                siblings.append(
                    {"doc_id": d["id"], "title": d["title"], "kind": d["kind"]}
                )

    # pages: notes written about this document, and a project's members
    notes = [
        dict(r)
        for r in con.execute(
            """
            SELECT DISTINCT x.source_doc AS doc_id, d.title, p.slug, p.kind
            FROM edges x JOIN entities t ON t.id = x.dst
            JOIN documents d ON d.id = x.source_doc JOIN pages p ON p.doc_id = d.id
            WHERE x.rel = 'annotates' AND x.valid_to IS NULL AND t.name = ?
              AND x.source_doc != ?
            ORDER BY d.title
            """,
            (title, doc_id),
        )
    ]
    members: list[dict[str, Any]] = []
    page_row = con.execute(
        "SELECT slug, kind FROM pages WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    if page_row and page_row["kind"] == "project":
        members = [
            dict(r)
            for r in con.execute(
                """
                SELECT DISTINCT s.name AS title, s.type,
                       (SELECT id FROM documents WHERE title = s.name
                        ORDER BY id LIMIT 1)
                           AS doc_id
                FROM edges x JOIN entities s ON s.id = x.src
                JOIN entities t ON t.id = x.dst
                WHERE x.rel = 'part_of' AND x.valid_to IS NULL AND t.name = ?
                ORDER BY s.name
                """,
                (title,),
            )
        ]
    return {
        "doc_id": doc_id,
        "page": dict(page_row) if page_row else None,
        "notes": notes,
        "members": members,
        "summary": meta.get("summary") or "",
        "extraction": meta.get("extraction"),
        "citations": meta.get("citations"),
        "entities": entities,
        "cites": cites,
        "cited_by": cited_by,
        "similar": _similar_documents(con, doc_id, limit=limit),
        "shared": shared,
        "same_authors": same_authors,
        "zotero": {
            "parent": parent,
            "siblings": siblings[:limit],
            "collections": meta.get("collections") or [],
            "tags": meta.get("tags") or [],
        },
    }


@_serialized
def hub_graph(
    con: sqlite3.Connection,
    *,
    limit: int = 30,
    types: tuple[str, ...] = HUB_TYPES,
    min_shared: int = 2,
) -> dict[str, Any]:
    """The most connected entities of the given types, the currently valid
    edges among them, and ``links``: pairs of hubs that share at least
    ``min_shared`` source documents (co-occurrence, the topic map). Degrees
    and edges are counted over canonical ids, like ``traverse``."""
    limit = max(1, min(limit, 200))
    marks = ",".join("?" * len(types))
    nodes = con.execute(
        f"""
        WITH canon(id, cid) AS (SELECT id, COALESCE(canonical_id, id) FROM entities),
        deg(cid, degree) AS (
            SELECT c.cid, count(*) FROM edges x
            JOIN canon c ON c.id = x.src OR c.id = x.dst
            WHERE x.valid_to IS NULL GROUP BY c.cid
        )
        SELECT e.id, e.name, e.type, d.degree FROM deg d JOIN entities e ON e.id = d.cid
        WHERE e.type IN ({marks}) ORDER BY d.degree DESC, e.name LIMIT ?
        """,
        (*types, limit),
    ).fetchall()
    ids = [r["id"] for r in nodes]
    edges: list[dict[str, Any]] = []
    if ids:
        idmarks = ",".join("?" * len(ids))
        edges = [
            dict(r)
            for r in con.execute(
                f"""
                WITH canon(id, cid) AS (
                    SELECT id, COALESCE(canonical_id, id) FROM entities
                )
                SELECT x.id AS edge_id, s.name AS src, s.type AS src_type, x.rel,
                       t.name AS dst, t.type AS dst_type, x.confidence,
                       x.source_doc, x.evidence, x.producer, x.run
                FROM edges x
                JOIN canon cs ON cs.id = x.src JOIN canon cd ON cd.id = x.dst
                JOIN entities s ON s.id = cs.cid JOIN entities t ON t.id = cd.cid
                WHERE x.valid_to IS NULL
                  AND cs.cid IN ({idmarks}) AND cd.cid IN ({idmarks})
                ORDER BY x.id
                """,
                (*ids, *ids),
            ).fetchall()
        ]
    links: list[dict[str, Any]] = []
    if ids:
        idmarks = ",".join("?" * len(ids))
        links = [
            dict(r)
            for r in con.execute(
                f"""
                WITH canon(id, cid) AS (
                    SELECT id, COALESCE(canonical_id, id) FROM entities
                ),
                touch(cid, doc) AS (
                    SELECT DISTINCT c.cid, x.source_doc FROM edges x
                    JOIN canon c ON c.id = x.src OR c.id = x.dst
                    WHERE x.valid_to IS NULL AND x.source_doc IS NOT NULL
                      AND c.cid IN ({idmarks})
                )
                SELECT ea.name AS a, ea.type AS a_type, eb.name AS b, eb.type AS b_type,
                       count(*) AS weight
                FROM touch ta JOIN touch tb ON ta.doc = tb.doc AND ta.cid < tb.cid
                JOIN entities ea ON ea.id = ta.cid JOIN entities eb ON eb.id = tb.cid
                GROUP BY ta.cid, tb.cid HAVING weight >= ?
                ORDER BY weight DESC LIMIT 300
                """,
                (*ids, max(1, min_shared)),
            ).fetchall()
        ]
    return {
        "nodes": [
            {"name": r["name"], "type": r["type"], "degree": r["degree"]} for r in nodes
        ],
        "edges": edges,
        "links": links,
    }


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
    producer: str | None = None,
    run: str | None = None,
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
        " ontology_version, evidence, producer, run, valid_from)"
        f" VALUES (?,?,?,?,?,?,?,?,?, {_NOW})",
        (
            src,
            dst,
            edge.rel,
            confidence,
            source_doc,
            ontology_version,
            evidence,
            producer,
            run,
        ),
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
def merge_entities(
    con: sqlite3.Connection,
    duplicate_id: int,
    into_id: int,
    *,
    across_types: bool = False,
) -> None:
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
    if rows[duplicate_id]["type"] != rows[into_id]["type"] and not across_types:
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
    producer: str | None = None,
    run: str | None = None,
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
        producer=producer,
        run=run,
    )


@_serialized
def retire_reading(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    producer: str,
    except_version: str,
) -> int:
    """End the live edges ``producer`` wrote from this document under any
    ontology version but ``except_version``: a producer re-reading a
    document under its current subset (another domain, a grown module)
    supersedes its own earlier reading. Other producers' edges stay.
    History is kept (invariant 8); returns how many edges."""
    cur = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE valid_to IS NULL"
        " AND source_doc = ? AND producer = ?"
        " AND coalesce(ontology_version, '') != ?",
        (doc_id, producer, except_version),
    )
    con.commit()
    return cur.rowcount


@_serialized
def retire_run(
    con: sqlite3.Connection,
    *,
    producer: str | None = None,
    run: str | None = None,
) -> int:
    """End every live edge a producer or a run wrote (both when both are
    given): what "upgrade" means once a better extractor has re-read the
    documents. History is kept (invariant 8); returns how many edges."""
    if producer is None and run is None:
        raise ValueError("retire_run needs a producer or a run")
    clauses = ["valid_to IS NULL"]
    args: list[Any] = []
    if producer is not None:
        clauses.append("producer = ?")
        args.append(producer)
    if run is not None:
        clauses.append("run = ?")
        args.append(run)
    cur = con.execute(
        f"UPDATE edges SET valid_to = {_NOW} WHERE " + " AND ".join(clauses), args
    )
    con.commit()
    return cur.rowcount


@_serialized
def provenance_summary(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Live and retired edge counts per producer and run."""
    rows = con.execute(
        """
        SELECT producer, run, sum(valid_to IS NULL) AS live,
               sum(valid_to IS NOT NULL) AS retired, min(ingested_at) AS first_at
        FROM edges GROUP BY producer, run ORDER BY first_at
        """
    ).fetchall()
    return [dict(r) for r in rows]


@_serialized
def backfill_provenance(con: sqlite3.Connection) -> dict[str, int]:
    """Fill ``producer`` and ``run`` on edges written before migration 0007,
    from the evidence prefix (citations, pages) and the source document's
    stamps (Zotero seeds, extraction). Idempotent: only NULL producers."""
    counts: dict[str, int] = {}

    def tag(
        where: str, producer: str, run_expr: str, args: tuple[Any, ...] = ()
    ) -> None:
        cur = con.execute(
            f"UPDATE edges SET producer = ?, run = {run_expr}"
            f" WHERE producer IS NULL AND {where}",
            (producer, *args),
        )
        counts[producer] = counts.get(producer, 0) + cur.rowcount

    tag("evidence LIKE 'crossref %'", "crossref", "'backfill'")
    tag("evidence LIKE 'openalex %'", "openalex", "'backfill'")
    tag("evidence LIKE 'page %' OR evidence LIKE 'project %'", "page", "'backfill'")
    tag(
        "evidence IS NULL AND rel IN ('authored_by', 'published_in') AND source_doc IN"
        " (SELECT id FROM documents WHERE json_extract(meta, '$.source') = 'zotero')",
        "zotero",
        "'backfill'",
    )
    # extraction: the source document's stamp names the model; the edge's
    # ontology version names the run
    cur = con.execute(
        """
        UPDATE edges SET
            producer = COALESCE(
                (SELECT json_extract(meta, '$.extraction.extractor') FROM documents d
                 WHERE d.id = edges.source_doc), 'extraction'),
            run = 'ontology-v' || COALESCE(ontology_version, '?')
        WHERE producer IS NULL AND evidence IS NOT NULL
        """
    )
    counts["extraction"] = cur.rowcount
    cur = con.execute(
        "UPDATE edges SET producer = 'manual', run = 'backfill' WHERE producer IS NULL"
    )
    counts["manual"] = cur.rowcount
    con.commit()
    return counts


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


def _review_where(
    open_only: bool, rel: str | None, unmapped: bool | None
) -> tuple[str, list[Any]]:
    """The filter shared by listing, counting and bulk resolution: open
    items only, a relation, and whether the item is an ``unmapped`` triple
    (no types, the model's own relation name) or a typed misfit."""
    clauses: list[str] = []
    args: list[Any] = []
    if open_only:
        clauses.append("resolved_at IS NULL")
    if rel:
        clauses.append("lower(rel) = ?")
        args.append(rel.lower())
    if unmapped is True:
        clauses.append("reason LIKE 'unmapped:%'")
    elif unmapped is False:
        clauses.append("reason NOT LIKE 'unmapped:%'")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


@_serialized
def list_review(
    con: sqlite3.Connection,
    *,
    open_only: bool = True,
    limit: int = 100,
    offset: int = 0,
    rel: str | None = None,
    unmapped: bool | None = None,
) -> list[dict[str, Any]]:
    where, args = _review_where(open_only, rel, unmapped)
    rows = con.execute(
        f"SELECT * FROM review_queue{where} ORDER BY id LIMIT ? OFFSET ?",
        (*args, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


@_serialized
def count_review(
    con: sqlite3.Connection,
    *,
    open_only: bool = True,
    rel: str | None = None,
    unmapped: bool | None = None,
) -> int:
    where, args = _review_where(open_only, rel, unmapped)
    row = con.execute(f"SELECT count(*) FROM review_queue{where}", args).fetchone()
    return int(row[0])


@_serialized
def resolve_review_many(
    con: sqlite3.Connection,
    resolution: str,
    *,
    rel: str | None = None,
    unmapped: bool | None = None,
) -> int:
    """Close every open item matching the filter; returns how many."""
    if resolution not in ("linked", "dropped", "ontology"):
        raise ValueError("resolution must be linked, dropped or ontology")
    where, args = _review_where(True, rel, unmapped)
    cur = con.execute(
        f"UPDATE review_queue SET resolved_at = {_NOW}, resolution = ?{where}",
        (resolution, *args),
    )
    con.commit()
    return cur.rowcount


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
    domain: str | None = None,
    onto: ontology.Ontology | None = None,
) -> list[int]:
    """Indexed documents not yet extracted under ``ontology_version``
    (``meta.extraction.ontology_version``), oldest first. ``min_chars``
    skips documents whose chunks hold less text than that (Zotero notes,
    scans without a text layer): nothing to extract, a call wasted.
    ``domain`` keeps the documents assigned to that module; with ``onto``
    a document with a domain set is compared against the version of its
    own subset of the ontology, not the whole."""
    sql = (
        "SELECT id FROM documents WHERE text_hash IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
        " AND (json_extract(meta, '$.extraction.ontology_version') IS NULL"
        "      OR json_extract(meta, '$.extraction.ontology_version') != ?)"
    )
    args: list[Any] = [ontology_version]
    if domain:
        sql += (
            " AND EXISTS (SELECT 1 FROM json_each(documents.meta, '$.domains')"
            " WHERE value = ?)"
        )
        args.append(domain)
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
    if limit is not None and onto is None:
        sql += " LIMIT ?"
        args.append(limit)
    ids = [r["id"] for r in con.execute(sql, args)]
    if onto is not None:
        # a document read under the version of its own domains is done
        kept = []
        for doc_id in ids:
            meta = get_meta(con, doc_id)
            stamp = (meta.get("extraction") or {}).get("ontology_version")
            if meta.get("domains") and stamp == expected_version(meta, onto):
                continue
            kept.append(doc_id)
        ids = kept[:limit] if limit is not None else kept
    return ids


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
               e.producer, e.run,
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
