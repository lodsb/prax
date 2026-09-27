"""Every use of a vector index holds ``_INDEX_LOCK`` (stage S): the delta is
written in place by adds, and a merge closes the indexes when it swaps
the file. Two paths did not: the single-vector read of the similar-
documents column, and the delta an add looked up before taking the lock
(``scripts/stress_vectors.py`` reproduced the second twice in two
minutes)."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Self

import numpy as np
import pytest

from prax import config, vectors
from prax.store import base, retrieval

pytestmark = pytest.mark.skipif(not vectors.available(), reason="usearch not installed")


class CountingLock:
    """An RLock that counts how often it was taken."""

    def __init__(self) -> None:
        self.inner = threading.RLock()
        self.taken = 0

    def __enter__(self) -> Self:
        self.inner.acquire()
        self.taken += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self.inner.release()


def _main(path: Path, n: int = 50) -> None:
    rng = np.random.default_rng(0)
    idx = vectors.VectorIndex(path, base.VEC_DIM, writable=True)
    idx.add(range(n), rng.standard_normal((n, base.VEC_DIM)).astype(np.float32))
    idx.save()
    idx.close()


def test_a_single_vector_is_read_under_the_lock(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = base._index_path("locks")
    _main(path)
    lock = CountingLock()
    monkeypatch.setattr(base, "_INDEX_LOCK", lock)
    assert base._get_vector(path, 3) is not None
    assert lock.taken >= 1


def test_an_add_after_a_merge_goes_into_the_new_delta(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = base._index_path("locks")
    _main(path)
    rng = np.random.default_rng(1)
    stale = base._delta(path)  # what an add looked up before the lock
    base._add_to_delta(path, [100], rng.standard_normal((1, base.VEC_DIM)))
    retrieval.merge_vectors("locks")  # closes that delta and makes a new one
    assert stale._index is None
    base._add_to_delta(path, [101], rng.standard_normal((1, base.VEC_DIM)))
    assert base._get_vector(path, 100) is not None  # merged into the main file
    assert base._get_vector(path, 101) is not None  # in the new delta


def test_a_short_stress_run_survives() -> None:
    """Adds, reads and a merge a second, for a few seconds, in a child
    process so a native crash would show as its exit code."""
    script = config.REPO_ROOT / "scripts" / "stress_vectors.py"
    done = subprocess.run(
        [
            sys.executable,
            str(script),
            "--seconds",
            "4",
            "--merge",
            "1",
            "--size",
            "2000",
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Error" not in done.stdout + done.stderr
    assert "survived" in done.stdout
