"""The vector index: one usearch HNSW file per embedding model.

Why a file next to the database and not a SQLite table: sqlite-vec scans
every vector, which at 855 K vectors took 4 s per query; the same vectors in
a memory-mapped usearch index answer in under 50 ms at recall 0.98
(rationale R6, ``docs/eval/retrieval-library-2026-09-08.md``).

Only ``prax.store`` uses this module (invariant 3). Keys are chunk ids. The
file is opened in one of two modes:

* **view** (serving): memory-mapped, read-only; the process holds only the
  pages it touches;
* **writable** (batch jobs): the whole index in memory; ``save`` writes it
  back atomically through a temporary file.

Deleted chunks leave stale keys behind; the store filters them at query
time and ``compact`` rebuilds a clean file from the live keys.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from prax import config

DEFAULT_DTYPE = "f16"  # f16: recall 0.98, 784 MB; i8: 0.93, 456 MB (855 K x 384)
DEFAULT_CONNECTIVITY = 16
DEFAULT_EXPANSION_ADD = 128
DEFAULT_EXPANSION_SEARCH = 64


def available() -> bool:
    try:
        importlib.import_module("usearch.index")
    except ImportError:
        return False
    return True


class VectorIndex:
    """A thin wrapper over ``usearch.index.Index`` bound to one file."""

    def __init__(self, path: Path, dim: int, *, writable: bool) -> None:
        idx_mod = importlib.import_module("usearch.index")
        self.path = path
        self.dim = dim
        self.writable = writable
        dtype = str(config.setting("vectors.dtype", "PRAX_VEC_DTYPE", DEFAULT_DTYPE))
        ef = config.whole("vectors.ef", "PRAX_VEC_EF", DEFAULT_EXPANSION_SEARCH)
        if path.exists():
            if writable:
                self._index = idx_mod.Index.restore(path, view=False)
            else:
                self._index = idx_mod.Index.restore(path, view=True)
            if self._index.ndim != dim:
                raise ValueError(
                    f"{path.name} has dimension {self._index.ndim}, expected {dim}"
                )
            self._index.expansion_search = ef
        else:
            if not writable:
                raise FileNotFoundError(path)
            self._index = idx_mod.Index(
                ndim=dim,
                metric="cos",
                dtype=dtype,
                connectivity=DEFAULT_CONNECTIVITY,
                expansion_add=DEFAULT_EXPANSION_ADD,
                expansion_search=ef,
            )

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, key: int) -> bool:
        return key in self._index

    def add(self, keys: Iterable[int], vectors: np.ndarray) -> None:
        """Insert or replace vectors for ``keys`` (writable mode only)."""
        if not self.writable:
            raise RuntimeError("index opened read-only")
        keys_arr = np.asarray(list(keys), dtype=np.uint64)
        if len(keys_arr) == 0:
            return
        present = [int(k) for k in keys_arr if int(k) in self._index]
        if present:
            self._index.remove(np.asarray(present, dtype=np.uint64))
        self._index.add(keys_arr, np.ascontiguousarray(vectors, dtype=np.float32))

    def remove(self, keys: Iterable[int]) -> int:
        if not self.writable:
            raise RuntimeError("index opened read-only")
        present = np.asarray(
            [int(k) for k in keys if int(k) in self._index], dtype=np.uint64
        )
        if len(present):
            self._index.remove(present)
        return len(present)

    def search(self, vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        """``(key, cosine distance)`` pairs, nearest first."""
        if len(self._index) == 0:
            return []
        m = self._index.search(np.asarray(vector, dtype=np.float32), k)
        return [
            (int(key), float(d)) for key, d in zip(m.keys, m.distances, strict=True)
        ]

    def get(self, key: int) -> np.ndarray | None:
        v = self._index.get(int(key))
        return None if v is None else np.asarray(v, dtype=np.float32)

    def all_keys(self) -> np.ndarray:
        return np.asarray(self._index.keys, dtype=np.uint64)

    def save(self) -> None:
        """Write the index atomically (``.tmp`` then replace)."""
        if not self.writable:
            raise RuntimeError("index opened read-only")
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._index.save(str(tmp))
        os.replace(tmp, self.path)

    def save_to(self, path: Path) -> None:
        """Write the index to another file (a merge builds the new main file
        beside the old one and swaps them later, under the lock)."""
        if not self.writable:
            raise RuntimeError("index opened read-only")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._index.save(str(path))

    def close(self) -> None:
        idx: Any = self._index
        self._index = None
        del idx

    def stats(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "count": len(self._index),
            "dim": self.dim,
            "dtype": str(self._index.dtype),
            "bytes": self.path.stat().st_size if self.path.exists() else 0,
        }
