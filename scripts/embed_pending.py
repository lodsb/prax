#!/usr/bin/env python3
"""Embed chunks that have no vector yet and maintain the vector index.

    python scripts/embed_pending.py                 # everything pending, default model
    python scripts/embed_pending.py --limit 5000    # a trial slice
    python scripts/embed_pending.py --dry-run       # counts only
    python scripts/embed_pending.py --compact       # reconcile index and bookkeeping

Model, variant and onnxruntime providers come from the PRAX_EMBED* settings
(see prax.embeddings); the index file is ``<data dir>/vectors-<model>.usearch``
(prax.vectors). Runs on the batch host; on this Windows desktop the DirectML
provider uses the GPU. The index is saved every ``--save-every`` chunks and
at the end; the bookkeeping is reconciled with the file on every start, so
an interrupted run can simply be started again.
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
    ap.add_argument(
        "--save-every", type=int, default=50_000, help="chunks between saves"
    )
    ap.add_argument(
        "--compact", action="store_true", help="reconcile and save, no embedding"
    )
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    emb = embeddings.current()
    if emb is None:
        ap.error("embeddings are disabled (PRAX_EMBED=0)")
    if not store.vectors_available():
        ap.error("usearch is not installed (pip install -e '.[embed]')")
    if emb.dim != store.VEC_DIM:
        ap.error(f"model dimension {emb.dim} != index dimension {store.VEC_DIM}")
    con = store.connect()
    store.init_db(con)
    if hasattr(emb, "batch_size"):
        emb.batch_size = a.batch

    status = store.vec_status(con)
    index_count = status["index"]["count"] if status["index"] else 0
    booked = status["models"].get(emb.name, 0)
    print(
        f"store: {config.db_path()}; model {emb.name}; index {index_count} vectors,"
        f" bookkeeping {booked}",
        file=sys.stderr,
    )
    if a.dry_run:
        print(f"pending: {store.count_pending_embeddings(con, emb.name)}")
        return 0
    if index_count != booked or a.compact:
        rec = store.compact_vectors(con, emb.name)
        print(f"reconciled index and bookkeeping: {rec}", file=sys.stderr)
        if a.compact:
            return 0

    pending = store.count_pending_embeddings(con, emb.name)
    print(f"pending: {pending}", file=sys.stderr)
    done = 0
    since_save = 0
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
        since_save += len(rows)
        if since_save >= a.save_every:
            store.save_vectors(emb.name)
            since_save = 0
        rate = done / (time.monotonic() - t0)
        eta = (pending - done) / rate / 60 if rate else 0
        print(
            f"[{done:7}/{pending}] {rate:5.1f} chunks/s, ~{eta:.0f} min left",
            file=sys.stderr,
            flush=True,
        )
    stats = store.save_vectors(emb.name)
    variant = getattr(emb, "variant", None)
    providers = getattr(emb, "providers", None)
    print(
        f"embedded {done} chunks with {emb.name} ({variant}, {providers})"
        f" in {time.monotonic() - t0:.0f} s; index {stats['count']} vectors,"
        f" {stats['bytes'] / 1e6:.0f} MB"
    )
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
