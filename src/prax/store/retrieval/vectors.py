"""The vector files: which chunk and which document has a vector from
which model, the delta indexes and their merge into the main files,
adoption, compaction, and the index's status."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from prax.ml import embeddings
from prax.ml import vectors as vectors_mod

from ..base import (
    _ASIDE,
    _INDEX_LOCK,
    _NOW,
    VEC_DIM,
    _add_to_delta,
    _delta,
    _delta_path,
    _doc_index_path,
    _index,
    _index_path,
    _indexes,
    _open_index,
    _reading,
    _serialized,
    vectors_available,
)
from .knobs import knobs


@_reading
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
        try:
            status["delta"] = delta_counts(emb.name)
        except Exception:  # noqa: BLE001 - a status must not fail on a stale file
            status["delta"] = None
        if status["index"] is not None and status["delta"]:
            # what a query can find: the main file plus the delta
            status["index"]["count"] += status["delta"]["chunks"]
    return status


def _vec_count(con: sqlite3.Connection) -> int:
    got: int = con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]
    return got


# (database file, model) -> (highest chunk id, rows of the model) when
# every chunk was found embedded. Chunk ids are reused, so the id alone
# would not do; a chunk added, a vector lost or a model written over
# moves one of the two
_EMBEDDED: dict[tuple[str, str], tuple[int, int]] = {}


def _all_chunks_embedded(con: sqlite3.Connection, model: str) -> bool:
    """Two counts before the scan: when every chunk has its vector from
    ``model`` there is nothing to look for. The scan below walks a
    million chunks probing the embeddings table for each — seventeen
    seconds with nothing pending, under the store's lock, every worker
    cycle: the door's searches stood in that queue (2026-09-18).

    The count of chunks is the dear one (190 ms of every hand-out,
    2026-09-30), and it is taken again only when the mark in
    ``_EMBEDDED`` no longer matches."""
    top = int(con.execute("SELECT coalesce(max(id), 0) FROM chunks").fetchone()[0])
    done = int(
        con.execute(
            "SELECT count(*) FROM chunk_embeddings WHERE model = ?", (model,)
        ).fetchone()[0]
    )
    key = (str(con.execute("PRAGMA database_list").fetchone()[2]), model)
    if _EMBEDDED.get(key) == (top, done):
        return True
    chunks = con.execute(f"SELECT count(*) FROM chunks c WHERE 1=1{_ASIDE}").fetchone()[
        0
    ]
    if done < chunks:
        return False
    _EMBEDDED[key] = (top, done)
    return True


@_reading
def pending_embeddings(
    con: sqlite3.Connection,
    model: str,
    *,
    limit: int | None = None,
    below: int | None = None,
    above: int | None = None,
    check: bool = True,
) -> list[dict[str, Any]]:
    """Chunks without a vector from ``model``: ``{chunk_id, kind, text}``,
    the newest first — new chunks are where the work is, and the scan
    stops at ``limit`` as soon as it has them.

    ``below`` and ``above`` bound the ids, which is what lets a caller
    that remembers where it was skip what it has already been through.
    From the top every time, the scan walked every finished chunk above
    the ones still waiting: 1.2 M rows and 759 ms a hand-out halfway
    through a re-embed, and worse the further it got (2026-09-25).

    ``check=False`` skips the two counts that answer "nothing pending"
    before the scan: a caller in the middle of a walk knows there is work,
    and the counts were 300 ms of every hand-out once the walk was fast."""
    if check and _all_chunks_embedded(con, model):
        return []
    bounds = ""
    args: list[Any] = [model]
    if below is not None:
        bounds += " AND c.id < ?"
        args.append(below)
    if above is not None:
        bounds += " AND c.id > ?"
        args.append(above)
    sql = (
        "SELECT c.id AS chunk_id, c.kind, c.text FROM chunks c"
        " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id"
        f" WHERE (e.chunk_id IS NULL OR e.model != ?){bounds}{_ASIDE}"
        " ORDER BY c.id DESC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return [dict(r) for r in con.execute(sql, args)]


@_reading
def newest_chunk(con: sqlite3.Connection) -> int:
    """The highest chunk id: where "new since" starts."""
    return int(con.execute("SELECT coalesce(max(id), 0) FROM chunks").fetchone()[0])


@_reading
def count_pending_embeddings(con: sqlite3.Connection, model: str) -> int:
    """How many chunks ``pending_embeddings`` would return, without the text."""
    if _all_chunks_embedded(con, model):
        return 0
    got: int = con.execute(
        "SELECT count(*) FROM chunks c"
        " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id"
        " WHERE e.chunk_id IS NULL OR e.model != ?",
        (model,),
    ).fetchone()[0]
    return got


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
    ids = [cid for cid, _, _ in items]
    import numpy as np

    _add_to_delta(
        _index_path(model),
        ids,
        np.vstack([np.asarray(v, dtype=np.float32) for _, _, v in items]),
    )
    con.executemany(
        "INSERT INTO chunk_embeddings (chunk_id, model) VALUES (?, ?)"
        " ON CONFLICT(chunk_id) DO UPDATE SET model = excluded.model,"
        f" embedded_at = {_NOW}",
        [(cid, model) for cid in ids],
    )
    con.commit()
    return len(items)


@_reading
def pending_document_embeddings(
    con: sqlite3.Connection, model: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Document fields without a vector from ``model``: ``{doc_id, text}``,
    the newest first."""
    sql = (
        "SELECT f.rowid AS doc_id, f.field AS text FROM documents_fts f"
        " LEFT JOIN document_embeddings e ON e.doc_id = f.rowid"
        " WHERE e.doc_id IS NULL OR e.model != ? ORDER BY f.rowid DESC"
    )
    args: tuple[Any, ...] = (model,)
    if limit is not None:
        sql += " LIMIT ?"
        args = (model, limit)
    return [dict(r) for r in con.execute(sql, args)]


@_reading
def count_pending_document_embeddings(con: sqlite3.Connection, model: str) -> int:
    got: int = con.execute(
        "SELECT count(*) FROM documents_fts f"
        " LEFT JOIN document_embeddings e ON e.doc_id = f.rowid"
        " WHERE e.doc_id IS NULL OR e.model != ?",
        (model,),
    ).fetchone()[0]
    return got


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
    import numpy as np

    ids = [d for d, _ in items]
    _add_to_delta(
        _doc_index_path(model),
        ids,
        np.vstack([np.asarray(v, dtype=np.float32) for _, v in items]),
    )
    con.executemany(
        "INSERT INTO document_embeddings (doc_id, model) VALUES (?, ?)"
        " ON CONFLICT(doc_id) DO UPDATE SET model = excluded.model,"
        f" embedded_at = {_NOW}",
        [(d, model) for d in ids],
    )
    con.commit()
    return len(items)


def _save_delta(path: Path) -> dict[str, Any]:
    """Write the delta beside ``path`` (small, nobody maps it) and merge it
    into the main file when it has grown past ``knobs.DELTA_MERGE_AT``, or past
    the size of the main file itself (``knobs.DELTA_MERGE_MIN`` at least)."""
    with _INDEX_LOCK:
        delta = _delta(path)
        delta.save()
        main = _open_index(path, writable=False) if path.exists() else None
        held = len(main) if main is not None else 0
        due = main is None or len(delta) >= min(
            knobs.DELTA_MERGE_AT, max(held, knobs.DELTA_MERGE_MIN)
        )
        if not due:
            return {
                "count": held + len(delta),
                "delta": len(delta),
                "bytes": path.stat().st_size,
            }
    return _merge(path)  # a large delta, or no main file yet: outside the lock


def _present(vector: Any) -> Any:
    """A vector the index holds: a key it listed is there under the lock."""
    if vector is None:
        raise KeyError("a listed key without a vector")
    return vector


# one build of a main file at a time: a merge that ran while a compaction
# built from the file before it would be undone by the compaction's swap.
# Not the index lock, which a search takes: a build takes minutes
_BUILD_LOCK = threading.RLock()


def _merge(path: Path) -> dict[str, Any]:
    with _BUILD_LOCK:
        return _merge_now(path)


def _merge_now(path: Path) -> dict[str, Any]:
    """Fold the delta into the main file. The building — the main index
    loaded into memory, the delta's vectors added, the result written to a
    file beside it — happens outside the index lock: a 1.2 GB file with
    fifty thousand new vectors takes half a minute, and every search of
    the evening waited on it. The lock is held twice, briefly: to take the
    delta's vectors, and to swap the new file in — this process's views
    dropped first (Windows will not replace a mapped file) — with what
    arrived during the build kept in a fresh delta."""
    import numpy as np

    dpath = _delta_path(path)
    with _INDEX_LOCK:
        delta = _delta(path)
        keys = [int(k) for k in delta.all_keys()]
        vecs = np.vstack([_present(delta.get(k)) for k in keys]) if keys else None
    taken = set(keys)
    # the build: a private in-memory copy of the main file, nobody's view
    main = vectors_mod.VectorIndex(path, VEC_DIM, writable=True)
    if keys and vecs is not None:
        main.add(keys, vecs)
    tmp = path.with_suffix(path.suffix + ".merging")
    main.save_to(tmp)
    main.close()
    # a door that serves the index from memory loads the new file now,
    # outside the lock (seconds for a gigabyte), and installs the loaded
    # copy at the swap; a mapping door reopens at the swap (milliseconds)
    preloaded = (
        vectors_mod.VectorIndex(tmp, VEC_DIM, writable=False)
        if vectors_mod.serve_in_memory()
        else None
    )
    with _INDEX_LOCK:
        delta = _delta(path)
        later = [int(k) for k in delta.all_keys() if int(k) not in taken]
        later_vecs = (
            np.vstack([_present(delta.get(k)) for k in later]) if later else None
        )
        for key in [(str(path), False), (str(path), True), (str(dpath), True)]:
            idx = _indexes.pop(key, None)
            if idx is not None:
                idx.close()
        os.replace(tmp, path)
        if dpath.exists():
            dpath.unlink()
        if later and later_vecs is not None:
            fresh = _delta(path)  # a new, empty one
            fresh.add(later, later_vecs)
            fresh.save()
        if preloaded is not None:
            preloaded.path = path
            _indexes[(str(path), False)] = preloaded
        reopened = _open_index(path, writable=False)
        return {
            "count": (len(reopened) if reopened is not None else 0) + len(later),
            "merged": len(keys),
            "delta": len(later),
            "bytes": path.stat().st_size,
        }


@_reading
def warm_fts(con: sqlite3.Connection) -> dict[str, int]:
    """Read the keyword index through once (``chunks_fts_data``, 0.7 GB
    at 1.4 M chunks, a second from the disk), so the first searches after
    a start do not pay for it a posting list at a time: a query of common
    words took 25 s cold on the desktop, where llama-server's model file
    holds the operating system's cache, and 0.1 s warm."""
    rows, size = con.execute(
        "SELECT count(*), coalesce(sum(length(block)), 0) FROM chunks_fts_data"
    ).fetchone()
    con.execute("SELECT coalesce(sum(length(field)), 0) FROM documents_fts").fetchone()
    return {"blocks": int(rows), "bytes": int(size)}


def fts_merge(con: sqlite3.Connection, *, seconds: float = 60.0) -> dict[str, int]:
    """Merge the keyword index's segments a little at a time (FTS5's
    ``merge``, 500 pages a step) for up to ``seconds``: every batch of
    chunks leaves a segment behind, and a term spread over two dozen of
    them is read from two dozen places. Returns the steps taken and the
    segments before and after."""
    before = con.execute("SELECT count(DISTINCT segid) FROM chunks_fts_idx").fetchone()[
        0
    ]
    steps = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        changes = con.total_changes
        con.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('merge', 500)")
        con.commit()
        steps += 1
        if con.total_changes - changes <= 1:  # nothing left to merge
            break
    after = con.execute("SELECT count(DISTINCT segid) FROM chunks_fts_idx").fetchone()[
        0
    ]
    return {
        "steps": steps,
        "segments_before": int(before),
        "segments_after": int(after),
    }


def warm_indexes(model: str) -> dict[str, int]:
    """Open the read views of ``model``'s two index files now, so no
    request pays for it: with ``vectors.serve: memory`` the load takes
    seconds (a hundred while llama-server reads its model from the same
    disk), and it happened under the index lock, on the first search."""
    out: dict[str, int] = {}
    for name, path in (
        ("chunks", _index_path(model)),
        ("documents", _doc_index_path(model)),
    ):
        idx = _open_index(path, writable=False) if path.exists() else None
        out[name] = len(idx) if idx is not None else 0
    return out


def save_document_vectors(model: str) -> dict[str, Any]:
    return _save_delta(_doc_index_path(model))  # index files only, as save_vectors


@_serialized
def reconcile_unsaved(con: sqlite3.Connection, model: str) -> dict[str, int]:
    """Forget the bookkeeping rows written after the index files were last
    saved whose vector is not in them, so those chunks are embedded again.

    The door saves the delta on a timer rather than after every batch, and
    ``prax up`` ends it by terminating its job object: no shutdown hook
    runs, so the vectors of the last seconds can be lost while their rows
    say they are there. Only the rows since the last save are asked
    about, and the index answers with ``contains``, so this costs a
    fraction of a second at start where ``compact_vectors`` costs minutes.
    """
    out = {"checked": 0, "forgotten": 0}
    for path, table, key in (
        (_index_path(model), "chunk_embeddings", "chunk_id"),
        (_doc_index_path(model), "document_embeddings", "doc_id"),
    ):
        files = [f for f in (path, _delta_path(path)) if f.exists()]
        if not files:
            continue
        saved = max(f.stat().st_mtime for f in files) - RECONCILE_SLACK
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(saved))
        ids = [
            int(r[0])
            for r in con.execute(
                f"SELECT {key} FROM {table} WHERE model = ? AND embedded_at >= ?",
                (model, since),
            )
        ]
        if not ids:
            continue
        held = set(_index_has(path, ids))
        lost = [i for i in ids if i not in held]
        for i in range(0, len(lost), 500):
            part = lost[i : i + 500]
            con.execute(
                f"DELETE FROM {table} WHERE {key} IN ({','.join('?' * len(part))})",
                part,
            )
        con.commit()
        out["checked"] += len(ids)
        out["forgotten"] += len(lost)
    return out


RECONCILE_SLACK = 120.0  # seconds: a row stamped just before a save is asked about


def save_vectors(model: str) -> dict[str, Any]:
    """Write ``model``'s new vectors (the delta) to disk; the main file is
    rewritten only when the delta is large (``merge_vectors``). The
    bookkeeping rows are committed as they are written, so a crash between
    two saves leaves rows that the next ``pending_embeddings`` run will not
    repeat; ``compact_vectors`` reconciles the two. Not behind the store's
    write lock: it touches the index files only, which have a lock of
    their own — a merge inside it once held every write for half a minute."""
    return _save_delta(_index_path(model))


@_serialized
def merge_vectors(model: str) -> dict[str, Any]:
    """Fold both deltas of ``model`` into their main files now."""
    return {
        "chunks": _merge(_index_path(model)),
        "documents": _merge(_doc_index_path(model)),
    }


@_reading
def delta_counts(model: str) -> dict[str, int]:
    out = {}
    paths = (("chunks", _index_path(model)), ("documents", _doc_index_path(model)))
    with _INDEX_LOCK:
        for name, path in paths:
            dpath = _delta_path(path)
            idx = _indexes.get((str(dpath), True))
            if idx is None and dpath.exists():
                idx = _delta(path)
            out[name] = len(idx) if idx is not None else 0
    return out


def adopt_vectors(
    con: sqlite3.Connection, model: str, *, job: Any | None = None
) -> dict[str, int]:
    """Record that this model's index already holds vectors for these
    chunks — the way back from a model switch, without recomputing.

    ``chunk_embeddings.chunk_id`` is the primary key and the embed step
    writes ``ON CONFLICT DO UPDATE SET model``, so the table remembers
    **one model per chunk**: re-embedding into a new model overwrites the
    record that the old one's vectors exist. The vectors themselves are
    untouched — they are in ``vectors-<old>.usearch``, which nothing
    deletes — so going back is bookkeeping rather than compute, and this
    is that bookkeeping.

    It is the mirror of ``compact_vectors``, which forgets rows whose
    vector is missing. This claims vectors whose row is missing, for the
    chunks and documents that still exist; a key whose chunk is gone is
    left alone, because the row would not survive the foreign key.

    Used after ``embeddings.model`` in prax.yaml is put back, and then
    the door restarted so it opens that model's index.

    **A job, not a request.** The first version of this was `@_serialized`
    and held the store's write lock *and* the index lock for the whole
    run, which wedged the door: `/health` answered in 0.6 s while
    `/search` timed out (2026-09-26). The keys are read once, outside
    both; the rows go in batches, each its own short write; and the
    caller is a thread with a job row, so progress is visible and nothing
    else waits on it.
    """
    out = {"chunks": 0, "documents": 0, "missing": 0}
    for what, path, table, column in (
        ("chunks", _index_path(model), "chunk_embeddings", "chunk_id"),
        ("documents", _doc_index_path(model), "document_embeddings", "doc_id"),
    ):
        table_of = "chunks" if what == "chunks" else "documents"
        live = [r[0] for r in con.execute(f"SELECT id FROM {table_of}")]
        take = _index_has(path, live)
        if not take:
            continue
        for i in range(0, len(take), 20_000):
            _adopt_batch(con, table, column, model, take[i : i + 20_000])
            if job is not None:
                job.note(f"{what}: {min(i + 20_000, len(take)):,} of {len(take):,}")
        out[what] = len(take)
    return out


@_serialized
def _adopt_batch(
    con: sqlite3.Connection, table: str, column: str, model: str, keys: list[int]
) -> int:
    """One batch of bookkeeping, behind the write lock for its own length
    and no longer."""
    con.executemany(
        f"INSERT INTO {table} ({column}, model) VALUES (?, ?)"
        f" ON CONFLICT({column}) DO UPDATE SET model = excluded.model,"
        f" embedded_at = {_NOW}",
        [(k, model) for k in keys],
    )
    con.commit()
    return len(keys)


def _index_has(path: Path, ids: list[int]) -> list[int]:
    """Which of ``ids`` this index and its delta hold.

    Asked of the index rather than read out of it. Reading every key and
    intersecting in Python took over twenty-five minutes of a core on a
    1.58 M-vector file and wedged the door doing it (2026-09-26);
    ``contains`` over the ids actually wanted is vectorised and answers
    in 0.13 s for 1.12 M. The question was always the intersection, so
    asking for the whole key set was work nobody needed.
    """
    if not ids:
        return []
    import numpy as np

    want = np.asarray(ids, dtype=np.uint64)
    found = np.zeros(len(want), dtype=bool)
    with _INDEX_LOCK:
        for p_ in (path, _delta_path(path)):
            if not p_.exists():
                continue
            with contextlib.suppress(Exception):
                # an empty or half-written file says nothing, which is an
                # answer: it holds none of them
                from usearch.index import Index

                idx = Index.restore(str(p_), view=True)
                if idx is not None and len(idx):
                    found |= np.asarray(idx.contains(want))
    return [int(x) for x in want[found]]


COMPACT_REBUILD = 0.1  # the share of removed slots past which the graph is built anew


@_serialized
def _forget_embeddings(con: sqlite3.Connection, chunk_ids: list[int]) -> None:
    for i in range(0, len(chunk_ids), 500):
        part = chunk_ids[i : i + 500]
        marks = ",".join("?" * len(part))
        con.execute(f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({marks})", part)
    con.commit()


def compact_vectors(
    con: sqlite3.Connection, model: str, *, rebuild: bool | None = None
) -> dict[str, Any]:
    """Reconcile the index with the bookkeeping: drop keys whose chunk is
    gone, and forget bookkeeping rows whose vector is missing from the
    index (so they get embedded again). Saves the index.

    Built like a merge: a private copy loaded, changed and written beside
    the main file outside ``_INDEX_LOCK``, and swapped in under it. It used
    to drop the read views and then save over the file unlocked, so a
    search in between could map the file again before it was replaced; and
    its writable copy sat in the shared table where any thread found it.

    A removed key keeps its slot, so when the removed ones are more than
    ``COMPACT_REBUILD`` of the file the graph is built anew
    (``VectorIndex.rebuild``; ``rebuild`` says so either way). Minutes, and
    outside the store's lock: only forgetting the missing rows writes.
    A vector stored during the build is in the delta, and is not missing."""
    path = _index_path(model)
    with _BUILD_LOCK:
        _merge(path)
        booked = {
            r[0]
            for r in con.execute(
                "SELECT chunk_id FROM chunk_embeddings WHERE model = ?", (model,)
            )
        }
        with _INDEX_LOCK:
            arrived = {int(k) for k in _delta(path).all_keys()}
        idx = vectors_mod.VectorIndex(path, VEC_DIM, writable=True)
        live = {r[0] for r in con.execute("SELECT id FROM chunks")}
        keys = {int(k) for k in idx.all_keys()}
        removed = idx.remove(keys - live)
        again = (
            rebuild
            if rebuild is not None
            else bool(keys) and removed / len(keys) >= COMPACT_REBUILD
        )
        if again:
            idx.rebuild()
        missing = sorted(booked - keys - arrived)
        if missing:
            _forget_embeddings(con, missing)
        tmp = path.with_suffix(path.suffix + ".compacting")
        idx.save_to(tmp)
        count = len(idx)
        idx.close()  # the private copy goes: the delta takes new vectors
        with _INDEX_LOCK:
            for key in [(str(path), False), (str(path), True)]:
                view = _indexes.pop(key, None)
                if view is not None:
                    view.close()
            os.replace(tmp, path)
    return {
        "removed_stale": removed,
        "forgot_missing": len(missing),
        "count": count,
        "rebuilt": bool(again),
        "bytes": path.stat().st_size,
    }
