#!/usr/bin/env python
"""Stress the door's vector indexes the way a busy evening does: one
thread adding to the delta index (a worker's embed post) while others read
single vectors out of it (a document page's similar-documents column),
and now and then a merge that swaps the main file.

Stage S (docs/PLAN.md): the door exited twice with STATUS_BAD_STACK and
twice with an access violation, both native, and a reproduction comes
before a fix. The run is a child process of its own, so a native crash
ends only it; the parent reports the exit code.

    python scripts/stress_vectors.py                 # 30 s, reads through the store
    python scripts/stress_vectors.py --seconds 60 --merge 10

Writes only into a temporary directory.
"""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

NATIVE = {
    3221225477: "0xC0000005 access violation",
    3221225512: "0xC0000028 STATUS_BAD_STACK",
    3221226505: "0xC0000409 stack buffer overrun",
}


def child(seconds: float, readers: int, merge_every: float, size: int) -> None:
    import numpy as np

    from prax import store
    from prax.ml import vectors
    from prax.store import base, retrieval

    model = "stress"
    path = base._index_path(model)
    rng = np.random.default_rng(1)
    main = vectors.VectorIndex(path, base.VEC_DIM, writable=True)
    main.add(range(size), rng.standard_normal((size, base.VEC_DIM)).astype(np.float32))
    main.save()
    main.close()
    added: list[int] = []
    stop = threading.Event()
    counts = {"adds": 0, "reads": 0, "merges": 0}

    def writer() -> None:
        next_key = size
        while not stop.is_set():
            keys = list(range(next_key, next_key + 256))
            vecs = rng.standard_normal((256, base.VEC_DIM)).astype(np.float32)
            base._add_to_delta(path, keys, vecs)  # what store.add_vectors calls
            added.extend(keys)
            next_key += 256
            counts["adds"] += 1

    def reader(seed: int) -> None:
        r = random.Random(seed)
        while not stop.is_set():
            pool = added[-4096:] if added and r.random() < 0.7 else None
            key = r.choice(pool) if pool else r.randrange(size)
            store._get_vector(path, key)  # as similar_documents does
            counts["reads"] += 1

    def merger() -> None:
        while not stop.wait(merge_every):
            retrieval.merge_vectors(model)
            counts["merges"] += 1

    threads = [threading.Thread(target=writer, daemon=True)]
    threads += [
        threading.Thread(target=reader, args=(i,), daemon=True) for i in range(readers)
    ]
    if merge_every > 0:
        threads.append(threading.Thread(target=merger, daemon=True))
    for t in threads:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join(timeout=30)
    print(f"survived: {counts}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--readers", type=int, default=4)
    ap.add_argument(
        "--merge", type=float, default=0, help="seconds between merges (0: none)"
    )
    ap.add_argument("--size", type=int, default=50_000, help="vectors in the main file")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        child(a.seconds, a.readers, a.merge, a.size)
        return
    with tempfile.TemporaryDirectory(prefix="prax-stress-") as tmp:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PRAX_")}
        env["PRAX_DATA_DIR"] = tmp
        args = [
            sys.executable,
            __file__,
            "--child",
            "--seconds",
            str(a.seconds),
            "--readers",
            str(a.readers),
            "--merge",
            str(a.merge),
            "--size",
            str(a.size),
        ]
        started = time.time()
        done = subprocess.run(
            args, env=env, capture_output=True, text=True, check=False
        )
        took = time.time() - started
        code = done.returncode
        print(done.stdout.strip() or "(no output)")
        if done.stderr.strip():
            print(done.stderr.strip()[-1500:])
        what = NATIVE.get(code & 0xFFFFFFFF, "") if code else "clean"
        print(f"exit {code} ({what or 'an error'}) after {took:.0f} s")
        sys.exit(0 if code == 0 else 1)


if __name__ == "__main__":
    main()
