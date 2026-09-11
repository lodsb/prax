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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax import config, embeddings, pipeline, store

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
        print(
            f"pending: {store.count_pending_embeddings(con, emb.name)} chunks,"
            f" {store.count_pending_document_embeddings(con, emb.name)} document fields"
        )
        return 0
    if a.compact:
        rec = store.compact_vectors(con, emb.name)
        print(f"reconciled index and bookkeeping: {rec}", file=sys.stderr)
        return 0
    say = lambda t: print(t, file=sys.stderr, flush=True)
    try:
        with store.Job(con, "embed", note=emb.name) as job:
            rep = pipeline.embed_pending(
                con, emb, limit=a.limit, save_every=a.save_every, log=say, job=job
            )
            job.note(f"{rep['chunks']} chunks, {rep['fields']} fields")
    except pipeline.IndexBusy as exc:
        print(
            f"{exc}: stop the door, or let it release its views"
            " (POST /vectors/release; the inbox watcher does this itself)",
            file=sys.stderr,
        )
        return 1
    if rep["reconciled"]:
        print(f"reconciled index and bookkeeping: {rep['reconciled']}", file=sys.stderr)
    variant = getattr(emb, "variant", None)
    providers = getattr(emb, "providers", None)
    print(
        f"embedded {rep['chunks']} chunks with {emb.name} ({variant}, {providers})"
        f" in {rep['seconds']:.0f} s; index {rep.get('index_count')} vectors,"
        f" {rep.get('bytes', 0) / 1e6:.0f} MB; {rep['fields']} document fields"
    )
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
