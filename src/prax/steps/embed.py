"""The embed step: the chunks and the document fields without a vector
from the embedder, to a worker that has it; the vectors back into the
door's delta indexes. The door never embeds (invariant 7)."""

from __future__ import annotations

import time
from typing import Any

from prax import config, embeddings, store
from prax.work import LEASE_SECONDS

from .base import HandOut, Pass, Step, TakeIn

# where the hand-out is in its walk down the chunks, per model: the lowest
# id it handed out (the next scan starts below it) and the highest id there
# was when the walk began (anything above is new since, and is looked for
# first, by a range the index answers at once)
walks: dict[str, tuple[int | None, int]] = {}
# vectors taken in since the delta was last written, and when that was
unsaved: dict[str, int] = {}
saved_at: dict[str, float] = {}

SAVE_SECONDS = 30.0  # the delta is written at most this often while vectors arrive
SAVE_VECTORS = 20_000  # or when this many have arrived since it was


def batch(con: Any, model: str, limit: int, h: HandOut) -> list[dict[str, Any]]:
    """The next chunks to embed, without walking past the finished ones.

    Two places to look. Above ``top``, the highest id when the walk began,
    is what arrived since: a range the index answers at once, looked at
    first. At or below it is the walk, which goes on below the lowest id
    it handed out last time. When the walk reaches the bottom it looks
    once more between there and ``top`` (what a lease let go, what a
    crash forgot) and then begins again. A hand-out used to scan from the
    top every time: 759 ms at the halfway mark of a re-embed, 1.2 M
    finished rows walked each time (2026-09-25).
    """
    want = limit * 4

    def free(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if h.free(r["chunk_id"])]

    below, top = walks.get(model, (None, store.newest_chunk(con)))
    # the range above the walk is a lookup; the counts that say "nothing
    # pending" are asked only where a walk would begin, since a walk under
    # way knows there is work
    fresh = free(
        store.pending_embeddings(con, model, limit=want, above=top, check=False)
    )
    walk: list[dict[str, Any]] = []
    if len(fresh) < want:
        start = below if below is not None else top + 1
        walked = store.pending_embeddings(
            con, model, limit=want, below=start, check=below is None
        )
        walk = free(walked)
        if len(walked) < want and below is not None:
            # the bottom: the stretch walked already, once, then a new walk
            again = store.pending_embeddings(
                con, model, limit=want, below=top + 1, above=below - 1, check=False
            )
            walk += free(again)
            below = None
    chosen = (fresh + walk)[:limit]
    if not chosen:
        # nothing to hand out: the walk is over, and what was taken in is
        # written now rather than when the timer next looks
        walks.pop(model, None)
        save_if_due(model, force=True)
        return []
    walked_ids = [r["chunk_id"] for r in chosen if r["chunk_id"] <= top]
    if walked_ids:
        below = min(walked_ids)
    walks[model] = (below, top)
    return chosen


def save_if_due(model: str, *, force: bool = False) -> bool:
    """Write the vector deltas when enough time or enough vectors have
    passed since the last write, or when ``force`` and anything waits.

    After every batch it rewrote the whole delta, which grows with the run:
    31 MB at the halfway mark of a re-embed, the 10-17 s the door logged
    as a slow POST (2026-09-25). What a door that is killed between two
    writes loses, ``store.reconcile_unsaved`` gives back at start."""
    waiting = unsaved.get(model, 0)
    if not waiting:
        return False
    every = config.number("door.vector_save_seconds", "PRAX_VECTOR_SAVE", SAVE_SECONDS)
    last = saved_at.setdefault(model, time.monotonic())
    due = force or waiting >= SAVE_VECTORS or time.monotonic() - last >= every
    if not due:
        return False
    store.save_vectors(model)
    unsaved[model] = 0
    saved_at[model] = time.monotonic()
    return True


def forget() -> None:
    """The walk and the unsaved count, as a fresh door has them."""
    walks.clear()
    unsaved.clear()
    saved_at.clear()


class Embed(Step):
    name = "embed"

    def hand_out(self, h: HandOut) -> dict[str, Any]:
        emb = embeddings.current()
        if emb is None or not store.vectors_available():
            return {
                "step": self.name,
                "model": None,
                "chunks": [],
                "fields": [],
                "lease_seconds": 0,
            }
        chunks = batch(h.con, emb.name, h.limit, h)
        fields = [
            r
            for r in store.pending_document_embeddings(
                h.con, emb.name, limit=h.limit * 4
            )
            if h.free(r["doc_id"], "embed-doc")
        ][: h.limit]
        h.lease([r["chunk_id"] for r in chunks])
        h.lease([r["doc_id"] for r in fields], "embed-doc")
        return {
            "step": self.name,
            "model": emb.name,
            "dim": emb.dim,
            "chunks": [
                {"chunk_id": r["chunk_id"], "kind": r["kind"], "text": r["text"]}
                for r in chunks
            ],
            "fields": [{"doc_id": r["doc_id"], "text": r["text"]} for r in fields],
            "lease_seconds": LEASE_SECONDS,
        }

    def take_in(self, t: TakeIn) -> dict[str, Any]:
        model = str(t.payload.get("model") or "")
        emb = embeddings.current()
        if emb is None or model != emb.name:
            raise ValueError(
                f"the door embeds with {emb.name if emb else None}, not {model!r}"
            )
        chunks = [(int(c), k, v) for c, k, v in (t.payload.get("chunks") or [])]
        fields = [(int(d), v) for d, v in (t.payload.get("fields") or [])]
        t.release([c for c, _, _ in chunks])
        t.release([d for d, _ in fields], "embed-doc")
        if chunks:
            t.out["applied"] += store.store_embeddings(t.con, chunks, model)
            unsaved[model] = unsaved.get(model, 0) + len(chunks)
            save_if_due(model)
        if fields:
            t.out["applied"] += store.store_document_embeddings(t.con, fields, model)
            store.save_document_vectors(model)
        return t.out

    def run(self, p: Pass) -> str | None:
        from prax import worker

        emb = embeddings.current()
        if emb is None:
            return None
        got = p.fetch(self.name, limit=p.limit * 20)
        if not (got.get("chunks") or got.get("fields")):
            return None
        if got.get("model") != emb.name:
            return (
                f"skipped: the door embeds with {got.get('model')},"
                f" this worker with {emb.name}"
            )
        p.note(f"embedding {len(got['chunks'])} chunks")
        rep = p.post(self.name, worker.do_embed(got, emb))
        return f"{rep.get('applied', 0)} vectors"


REGISTERED = {"embed": Embed()}
