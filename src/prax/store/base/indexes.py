"""The vector index files (invariant 1): one main file and a writable
delta per model, for chunks, documents and faces; opened, cached and
searched behind ``_INDEX_LOCK``."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prax import config
from prax.ml import faces as faces_ml
from prax.ml import vectors

from .common import LIVE, VEC_DIM

_indexes: dict[tuple[str, bool], vectors.VectorIndex] = {}  # (path, writable)


# the process-wide cache of index views and deltas: a read searches a
# view no lock guards, so opening, closing and adding to them, and the
# search of the writable delta, go behind this one (short, milliseconds)
_INDEX_LOCK = threading.RLock()


def vectors_available() -> bool:
    """True when the usearch index library is installed. Without it the
    store works FTS-only."""
    return vectors.available()


def _index_path(model: str) -> Path:
    return config.data_dir() / f"vectors-{model}.usearch"


def _doc_index_path(model: str) -> Path:
    return config.data_dir() / f"vectors-doc-{model}.usearch"


FACE_INDEX = "vectors-face-"  # and the recogniser's name: its file's name


def _index_dim(path: Path) -> int:
    """The dimension of the index at ``path``, its delta or a merge's or a
    compaction's file beside it: a face index's is its recogniser's
    (``prax.ml.faces``), named in the file's name, every other
    ``VEC_DIM``. Read from the name, so it holds before anything else
    has asked for the face index (it was a registry the face part filled,
    and a merge in a process that had not called it read 128 numbers as
    384; structure review finding 9)."""
    name = path.name.removesuffix(".merging").removesuffix(".compacting")
    name = name.replace(".delta.usearch", ".usearch").removesuffix(".usearch")
    if name.startswith(FACE_INDEX):
        return faces_ml.recognizer(name.removeprefix(FACE_INDEX)).dim
    return VEC_DIM


@dataclass(frozen=True)
class IndexFile:
    """One kind of vector file and the table that records what it holds:
    the chunks' and the documents' of an embedder, the faces' of a
    recogniser, of one ``model``. ``merge_vectors``, ``reconcile_unsaved`` and
    ``compact_vectors`` do the same over each.

    ``table`` holds a row per key with a vector: ``key`` its column,
    ``model_col`` and ``at_col`` the model and when. ``live`` selects the
    keys that may keep a vector in the file (a chunk that exists, a
    document not retired, a face not gone); ``forget`` is the statement
    that clears the rows of keys (``{marks}``) whose vector was lost, so
    they are read again. ``lost_without_file``: when neither the file nor
    its delta exists, every recent row's vector is lost (the faces'); the
    text indexes then ask nothing, as they always did."""

    kind: str
    model: str
    path: Path
    dim: int
    table: str
    key: str
    model_col: str
    at_col: str
    live: str
    forget: str
    lost_without_file: bool = False


def chunk_index(model: str) -> IndexFile:
    return IndexFile(
        "chunks",
        model,
        _index_path(model),
        VEC_DIM,
        "chunk_embeddings",
        "chunk_id",
        "model",
        "embedded_at",
        "SELECT id FROM chunks",
        "DELETE FROM chunk_embeddings WHERE chunk_id IN ({marks})",
    )


def document_index(model: str) -> IndexFile:
    return IndexFile(
        "documents",
        model,
        _doc_index_path(model),
        VEC_DIM,
        "document_embeddings",
        "doc_id",
        "model",
        "embedded_at",
        f"SELECT id FROM documents WHERE {LIVE}",
        "DELETE FROM document_embeddings WHERE doc_id IN ({marks})",
    )


def face_index(model: str | None = None) -> IndexFile:
    """The face index of ``model``, the host's recogniser by default
    (``faces.model``)."""
    spec = faces_ml.recognizer(model)
    return IndexFile(
        "faces",
        spec.name,
        config.data_dir() / f"{FACE_INDEX}{spec.name}.usearch",
        spec.dim,
        "faces",
        "id",
        "vector_model",
        "vector_at",
        "SELECT id FROM faces WHERE gone_at IS NULL",
        "UPDATE faces SET vector_model = NULL, vector_at = NULL WHERE id IN ({marks})",
        lost_without_file=True,
    )


def _open_index(path: Path, *, writable: bool) -> vectors.VectorIndex | None:
    key = (str(path), writable)
    with _INDEX_LOCK:
        idx = _indexes.get(key)
        if idx is None:
            if not writable and not path.exists():
                return None
            idx = vectors.VectorIndex(path, _index_dim(path), writable=writable)
            _indexes[key] = idx
        return idx


def _index(model: str, *, writable: bool) -> vectors.VectorIndex | None:
    """The chunk index for ``model``: a memory-mapped view for reads (None
    when no file exists yet), or the in-memory writable copy for batch jobs."""
    return _open_index(_index_path(model), writable=writable)


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


def _add_to_delta(path: Path, keys: list[int], vectors_: Any) -> None:
    """Add vectors to the delta beside ``path``: the delta is looked up and
    written under one hold of ``_INDEX_LOCK``. Looked up before the lock, it
    could be the one a merge had just closed and replaced, and the add
    failed on it (``TypeError`` on a closed index, reproduced twice in two
    minutes by ``scripts/stress_vectors.py`` with a merge a second)."""
    with _INDEX_LOCK:
        _delta(path).add(keys, vectors_)


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
    """One vector, the delta's when it has it. Under ``_INDEX_LOCK`` like
    every other use of an index: the delta is written in place by adds,
    and a merge closes both indexes when it swaps the file. Unlocked, this
    read raced both (the similar-documents column of a document page,
    while a worker's embed posts arrived)."""
    with _INDEX_LOCK:
        dpath = _delta_path(path)
        delta = _indexes.get((str(dpath), True))
        if delta is None and dpath.exists():
            delta = _delta(path)
        if delta is not None and key in delta:
            return delta.get(key)
        main = _open_index(path, writable=False)
        return main.get(key) if main is not None else None


def _get_vectors(path: Path, keys: list[int]) -> dict[int, Any]:
    """Many vectors at once, each the delta's when it has it: one taking
    of ``_INDEX_LOCK`` for all of them. A key in neither is left out."""
    out: dict[int, Any] = {}
    with _INDEX_LOCK:
        dpath = _delta_path(path)
        delta = _indexes.get((str(dpath), True))
        if delta is None and dpath.exists():
            delta = _delta(path)
        main = _open_index(path, writable=False)
        for key in keys:
            vec = None
            if delta is not None and key in delta:
                vec = delta.get(key)
            elif main is not None and key in main:
                vec = main.get(key)
            if vec is not None:
                out[key] = vec
    return out


def _has_vectors(path: Path) -> bool:
    if path.exists():
        return True
    dpath = _delta_path(path)
    return dpath.exists() or (str(dpath), True) in _indexes
