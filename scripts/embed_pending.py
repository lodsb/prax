#!/usr/bin/env python3
"""Embed chunks that have no vector yet (or one from another model).

    python scripts/embed_pending.py                 # everything pending, default model
    python scripts/embed_pending.py --limit 5000    # a trial slice
    python scripts/embed_pending.py --dry-run       # counts only

Model, variant and onnxruntime providers come from the PRAX_EMBED* settings
(see prax.embeddings). Runs on the batch host; on this Windows desktop the
DirectML provider uses the GPU. Idempotent: rerun to continue.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, embeddings, store

FETCH = 2048  # chunks pulled from the store per round


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int, help="stop after this many chunks")
    ap.add_argument("--batch", type=int, default=64, help="onnx batch size")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    emb = embeddings.current()
    if emb is None:
        ap.error("embeddings are disabled (PRAX_EMBED=0)")
    con = store.connect()
    store.init_db(con)
    if not store.has_vec(con):
        ap.error("sqlite-vec is not available on this Python")
    if emb.dim != store.VEC_DIM:
        ap.error(f"model dimension {emb.dim} != chunks_vec dimension {store.VEC_DIM}")
    if hasattr(emb, "batch_size"):
        emb.batch_size = a.batch
    status = store.vec_status(con)
    pending = store.count_pending_embeddings(con, emb.name)
    print(
        f"store: {config.db_path()}; model {emb.name}; vectors {status['rows']};"
        f" pending {pending}",
        file=sys.stderr,
    )
    if a.dry_run:
        return 0

    done = 0
    t0 = time.monotonic()
    while True:
        want = FETCH if a.limit is None else min(FETCH, a.limit - done)
        if want <= 0:
            break
        rows = store.pending_embeddings(con, emb.name, limit=want)
        if not rows:
            break
        vectors = emb.embed([r["text"] for r in rows])
        store.store_embeddings(
            con,
            [(r["chunk_id"], r["kind"], v) for r, v in zip(rows, vectors, strict=True)],
            emb.name,
        )
        done += len(rows)
        rate = done / (time.monotonic() - t0)
        left = pending - done
        eta = left / rate / 60
        print(
            f"[{done:7}/{pending}] {rate:5.1f} chunks/s, ~{eta:.0f} min left",
            file=sys.stderr,
            flush=True,
        )
    variant = getattr(emb, "variant", None)
    providers = getattr(emb, "providers", None)
    print(
        f"embedded {done} chunks with {emb.name} ({variant}, {providers})"
        f" in {time.monotonic() - t0:.0f} s"
    )
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
