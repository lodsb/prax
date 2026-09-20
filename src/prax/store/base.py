"""The connection, the lock, the archive and the index files.

Nothing here knows about documents or the graph: it is what every other
module of the store stands on. One process-wide re-entrant lock serializes
writes (invariant 4); reads in the door take a connection per thread
(``thread_connection``). Originals and parsed text are content-addressed
files under ``data/archive`` (invariant 2), and the vector index files are
opened and cached here.
"""

from __future__ import annotations

import functools
import hashlib
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from prax import config, vectors

VEC_DIM = 384  # dimension of the vector index; another dimension is a new index file


# chunk kinds the search leaves out unless asked for by kind, and the
# embed step never vectorises: a reference entry matches every author,
# venue and year in the library and says nothing the cited paper does
# not say better — the references pass turns it into an edge instead
ASIDE_KINDS = ("reference",)
_ASIDE = (  # a legacy row without a kind is text
    " AND (c.kind IS NULL OR c.kind NOT IN ("
    + ", ".join(f"'{k}'" for k in ASIDE_KINDS)
    + "))"
)


_indexes: dict[tuple[str, bool], vectors.VectorIndex] = {}  # (path, writable)


_LOCK = threading.RLock()
# the process-wide cache of index views and deltas: a read searches a
# view no lock guards, so opening, closing and adding to them, and the
# search of the writable delta, go behind this one (short, milliseconds)
_INDEX_LOCK = threading.RLock()


_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"


# how long a connection waits for another process's write transaction
# before "database is locked" (three writers share the file: the door,
# the watcher, a backlog pass)
BUSY_TIMEOUT = 30.0


_TOKEN = re.compile(r"\w+", re.UNICODE)


# SQLite's page cache per connection (``door.sqlite_cache_mb``) and how
# much of the file it maps (``door.sqlite_mmap_mb``). The defaults SQLite
# ships with are a 2 MB cache and no mapping: a keyword query over a
# million chunks read its posting lists from disk every time (18 s cold,
# 1.7 s warm on the desktop; 1.7 s and 1.3 s with these). The mapping is
# the operating system's cache, file-backed and shared, so a small board
# gives it back under pressure; the cache is the connection's own.
CACHE_MB = 64
MMAP_MB = 1024


_SURROGATE = re.compile(r"[\ud800-\udfff]")


P = ParamSpec("P")


R = TypeVar("R")


_depth = threading.local()


LOCK_RETRIES = 6


class _NoLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


def _guarded(fn: Callable[P, R], *, lock: bool) -> Callable[P, R]:
    """The retry that both guards share: across processes (the door, a
    backlog pass on one database) SQLite's busy handler waits
    ``BUSY_TIMEOUT`` and then says "database is locked"; the outermost
    store call rolls back and tries again a few times, so a capture
    arriving while a batch pass holds a long transaction is not lost."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with _LOCK if lock else _NoLock():
            depth = getattr(_depth, "n", 0)
            _depth.n = depth + 1
            try:
                for attempt in range(LOCK_RETRIES):
                    try:
                        return fn(*args, **kwargs)
                    except sqlite3.OperationalError as exc:
                        if (
                            depth
                            or "locked" not in str(exc)
                            or attempt == LOCK_RETRIES - 1
                        ):
                            raise
                        con = next(
                            (a for a in args if isinstance(a, sqlite3.Connection)), None
                        )
                        if con is not None:
                            con.rollback()
                        time.sleep(0.3 * (attempt + 1))
                raise AssertionError("unreachable")
            finally:
                _depth.n = depth

    return wrapper


def _serialized(fn: Callable[P, R]) -> Callable[P, R]:
    """One writer at a time in this process: every store function that
    writes takes ``_LOCK`` for its duration."""
    return _guarded(fn, lock=True)


def _reading(fn: Callable[P, R]) -> Callable[P, R]:
    """A read: no lock, only the retry. A request thread reads on its own
    connection and WAL gives it a consistent snapshot, so a search never
    waits for a book being indexed or a heal retiring documents — it did
    until 2026-09-19, when every read was serialized too, and a
    seventeen-second scan under the lock each worker cycle was what
    "loading takes ages" was."""
    return _guarded(fn, lock=False)


_local = threading.local()


def thread_connection() -> sqlite3.Connection:
    """This thread's own connection to the store (opened on first use,
    kept for the thread's life). The door's request threads read on their
    own connections; writes are still one at a time behind ``_LOCK``, and
    the schema is applied once by the process's main connection."""
    con = getattr(_local, "con", None)
    if con is None:
        con = connect()
        _local.con = con
    return con


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False, timeout=BUSY_TIMEOUT)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    cache = config.whole("door.sqlite_cache_mb", "PRAX_SQLITE_CACHE_MB", CACHE_MB)
    mmap = config.whole("door.sqlite_mmap_mb", "PRAX_SQLITE_MMAP_MB", MMAP_MB)
    con.execute(f"PRAGMA cache_size = -{max(cache, 0) * 1024}")
    con.execute(f"PRAGMA mmap_size = {max(mmap, 0) * 1024 * 1024}")
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
    with _INDEX_LOCK:
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
    with _INDEX_LOCK:
        for path in (_index_path(model), _doc_index_path(model)):
            idx = _indexes.pop((str(path), False), None)
            if idx is not None:
                idx.close()


# The delta index: new vectors go into a small writable index of their own
# (``vectors-<model>.delta.usearch``) that the process holding it searches
# together with the memory-mapped main file. The main file is rewritten
# only by ``merge_vectors``, which folds the delta in. So the door can take
# vectors as they arrive without keeping the whole index writable in
# memory (800 MB at 876 K vectors), and no other process has to replace a
# file the door has mapped.


def _delta_path(path: Path) -> Path:
    return path.with_name(path.stem + ".delta" + path.suffix)


def _delta(path: Path) -> vectors.VectorIndex:
    """The writable delta index beside ``path`` (created empty when there
    is no file yet)."""
    return _open_index(_delta_path(path), writable=True)  # type: ignore[return-value]


def _knn(path: Path, vector: Any, k: int) -> list[tuple[int, float]]:
    """Nearest keys from the main view and the delta, merged by distance;
    a key in both takes the delta's (newer) place."""
    out: dict[int, float] = {}
    with _INDEX_LOCK:
        main = _open_index(path, writable=False)
        if main is not None:
            for key, d in main.search(vector, k):
                out[key] = d
        dpath = _delta_path(path)
        delta = _indexes.get((str(dpath), True))
        if delta is None and dpath.exists():
            delta = _delta(path)
        if delta is not None and len(delta):
            for key, d in delta.search(vector, k):
                out[key] = d
    return sorted(out.items(), key=lambda kv: kv[1])[:k]


def _get_vector(path: Path, key: int) -> Any:
    dpath = _delta_path(path)
    delta = _indexes.get((str(dpath), True))
    if delta is None and dpath.exists():
        delta = _delta(path)
    if delta is not None and key in delta:
        return delta.get(key)
    main = _open_index(path, writable=False)
    return main.get(key) if main is not None else None


def _has_vectors(path: Path) -> bool:
    if path.exists():
        return True
    dpath = _delta_path(path)
    return dpath.exists() or (str(dpath), True) in _indexes


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


def _like_prefix(prefix: str) -> str:
    """A LIKE pattern matching strings that start with ``prefix`` literally."""
    escaped = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return escaped + "%"
