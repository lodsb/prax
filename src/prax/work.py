"""The work protocol: the door hands model work out and takes the results
in, so the batch passes can run on another machine, or on this one,
without opening the database. The door stays the only writer.

Four steps, each a selection the door already knows how to make:

    parse    a capture the door only registered; the worker fetches the
             original (``GET /doc/{id}/original``), runs the extractors,
             posts the text
    titles   a document whose title is a file name; the worker guesses
             with its titles model, posts the title and its confidence
    extract  a document the current ontology has not read; the door
             sends the prepared prompt input, the worker posts the triples
    embed    chunks and document fields without a vector; the worker
             posts the vectors, the door puts them in its delta index

A handed-out item carries a lease (``LEASE_SECONDS``): until it expires
no other worker gets it. Leases live in this process; a door restart
forgets them, which costs nothing because every result is idempotent
(a stamp, a bookkeeping row, a replaced vector). ``scope`` is
``captures`` (uploads, dropped files, pages sent from the browser: what
arrives on its own) or ``all`` (the whole library, a backlog pass).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict
from typing import Any

from prax import embeddings, extraction, inbox, ontology, pipeline, store
from prax.parsers import queue

LEASE_SECONDS = 900
STEPS = ("parse", "titles", "extract", "embed")
SCOPES = ("captures", "all")
MAX_LIMIT = 200

_leases: dict[tuple[str, int], tuple[str, float]] = {}


def _free(step: str, item: int, now: float) -> bool:
    held = _leases.get((step, item))
    return held is None or held[1] < now


def _lease(step: str, items: list[int], worker: str) -> None:
    until = time.monotonic() + LEASE_SECONDS
    for i in items:
        _leases[(step, i)] = (worker, until)


def _release(step: str, items: list[int]) -> None:
    for i in items:
        _leases.pop((step, i), None)


def leases() -> dict[str, int]:
    """How many items are out per step (for the status view)."""
    now = time.monotonic()
    out: dict[str, int] = {}
    for (step, _), (_, until) in _leases.items():
        if until >= now:
            out[step] = out.get(step, 0) + 1
    return out


def _in_scope(con: sqlite3.Connection, doc_id: int, scope: str) -> bool:
    if scope == "all":
        return True
    row = con.execute(
        "SELECT json_extract(meta, '$.source') FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    return bool(row) and row[0] in pipeline.CAPTURE_SOURCES


def _check(step: str, scope: str, limit: int) -> int:
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}; steps are {STEPS}")
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}; scopes are {SCOPES}")
    return max(1, min(int(limit), MAX_LIMIT))


# ---------------------------------------------------------------- hand out


def hand_out(
    con: sqlite3.Connection,
    step: str,
    *,
    limit: int = 10,
    scope: str = "captures",
    worker: str = "worker",
) -> dict[str, Any]:
    """A batch of work for ``step``, leased to ``worker``."""
    limit = _check(step, scope, limit)
    now = time.monotonic()
    if step == "extract":
        onto = ontology.current()
        due = store.select_for_extraction(
            con, ontology_version=onto.version, min_chars=pipeline.MIN_CHARS, onto=onto
        )
        items = []
        for doc_id in due:
            if len(items) >= limit or not _free(step, doc_id, now):
                continue
            if not _in_scope(con, doc_id, scope):
                continue
            mime = con.execute(
                "SELECT mime FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()[0]
            if (mime or "").startswith("image/"):
                continue
            doc = extraction.build_input(con, doc_id)
            items.append(
                {
                    "doc_id": doc_id,
                    "title": doc.title,
                    "header": doc.header,
                    "text": doc.text,
                    "domains": doc.domains,
                    "ontology_version": doc.ontology().version,
                }
            )
        _lease(step, [i["doc_id"] for i in items], worker)
        return {"step": step, "items": items, "lease_seconds": LEASE_SECONDS}
    if step == "titles":
        items = []
        for doc_id, why in pipeline.titles_needed(con, untried_only=True):
            if len(items) >= limit or not _free(step, doc_id, now):
                continue
            if not _in_scope(con, doc_id, scope):
                continue
            doc = store.get_document(con, doc_id, max_chars=60000)
            if doc is None:
                continue
            items.append(
                {
                    "doc_id": doc_id,
                    "why": why,
                    "title": doc["title"] or "",
                    "text": doc["text"],
                    "filename": pipeline.file_name(doc),
                    "mime": doc["mime"],
                }
            )
        _lease(step, [i["doc_id"] for i in items], worker)
        return {"step": step, "items": items, "lease_seconds": LEASE_SECONDS}
    if step == "parse":
        from prax import parsers

        items = []
        for doc_id in inbox.pending_captures(con):
            if len(items) >= limit or not _free(step, doc_id, now):
                continue
            if not _in_scope(con, doc_id, scope):
                continue
            doc = store.get_document(con, doc_id, max_chars=0)
            if doc is None:
                continue
            exts = parsers.candidates(doc["mime"] or "")
            if not exts or queue._seen(doc["meta"], exts[0].stamp):
                continue
            path = doc.get("original_path")
            items.append(
                {
                    "doc_id": doc_id,
                    "mime": doc["mime"],
                    "filename": path.replace("\\", "/").rsplit("/", 1)[-1]
                    if path
                    else None,
                    "original": f"/doc/{doc_id}/original",
                    "old_len": doc["text_len"],
                }
            )
        _lease(step, [i["doc_id"] for i in items], worker)
        return {"step": step, "items": items, "lease_seconds": LEASE_SECONDS}
    # embed
    emb = embeddings.current()
    if emb is None or not store.vectors_available():
        return {
            "step": step,
            "model": None,
            "chunks": [],
            "fields": [],
            "lease_seconds": 0,
        }
    chunks = [
        r
        for r in store.pending_embeddings(con, emb.name, limit=limit * 4)
        if _free("embed", r["chunk_id"], now)
    ][:limit]
    fields = [
        r
        for r in store.pending_document_embeddings(con, emb.name, limit=limit * 4)
        if _free("embed-doc", r["doc_id"], now)
    ][:limit]
    _lease("embed", [r["chunk_id"] for r in chunks], worker)
    _lease("embed-doc", [r["doc_id"] for r in fields], worker)
    return {
        "step": step,
        "model": emb.name,
        "dim": emb.dim,
        "chunks": [
            {"chunk_id": r["chunk_id"], "kind": r["kind"], "text": r["text"]}
            for r in chunks
        ],
        "fields": [{"doc_id": r["doc_id"], "text": r["text"]} for r in fields],
        "lease_seconds": LEASE_SECONDS,
    }


# ----------------------------------------------------------------- take in


def _extraction_from(data: dict[str, Any]) -> extraction.Extraction:
    triples = [extraction.Triple(**t) for t in data.get("triples") or []]
    return extraction.Extraction(
        triples=triples,
        unmapped=list(data.get("unmapped") or []),
        summary=str(data.get("summary") or ""),
        usage=dict(data.get("usage") or {}),
    )


def extraction_to_dict(ex: extraction.Extraction) -> dict[str, Any]:
    return {
        "summary": ex.summary,
        "triples": [asdict(t) for t in ex.triples],
        "unmapped": ex.unmapped,
        "usage": ex.usage,
    }


def take_in(
    con: sqlite3.Connection,
    step: str,
    payload: dict[str, Any],
    *,
    worker: str = "worker",
) -> dict[str, Any]:
    """Apply a worker's results for ``step``; the leases go either way."""
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}; steps are {STEPS}")
    results = payload.get("results") or []
    out: dict[str, Any] = {"applied": 0, "errors": [], "skipped": 0}
    if step == "extract":
        extractor = str(payload.get("extractor") or worker)
        run = payload.get("run") or f"work-{time.strftime('%Y%m%dT%H%M%S')}"
        totals = extraction.ApplyReport()
        for r in results:
            doc_id = int(r["doc_id"])
            _release(step, [doc_id])
            if r.get("error"):
                out["errors"].append({"doc_id": doc_id, "error": r["error"]})
                continue
            try:
                rep = extraction.apply(
                    con,
                    doc_id,
                    _extraction_from(r["extraction"]),
                    extractor=extractor,
                    run=run,
                )
            except Exception as exc:  # noqa: BLE001 - one result must not stop the rest
                out["errors"].append(
                    {"doc_id": doc_id, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            for k in ("linked", "existing", "queued", "rejected", "retired"):
                setattr(totals, k, getattr(totals, k) + getattr(rep, k))
            out["applied"] += 1
        out["report"] = {
            k: getattr(totals, k)
            for k in ("linked", "existing", "queued", "rejected", "retired")
        }
        return out
    if step == "titles":
        run = payload.get("run") or f"titles-{time.strftime('%Y%m%dT%H%M%S')}"
        for r in results:
            doc_id = int(r["doc_id"])
            _release(step, [doc_id])
            if r.get("error"):
                out["errors"].append({"doc_id": doc_id, "error": r["error"]})
                continue
            if r.get("tried"):
                pipeline._mark_tried(con, doc_id, run, str(r["tried"]))
                out["skipped"] += 1
                continue
            try:
                store.retitle(
                    con,
                    doc_id,
                    str(r["title"]),
                    source=str(r.get("source") or worker),
                    run=run,
                    confidence=r.get("confidence"),
                )
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(
                    {"doc_id": doc_id, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            out["applied"] += 1
        return out
    if step == "parse":
        actions: dict[str, int] = {}
        for r in results:
            doc_id = int(r["doc_id"])
            _release(step, [doc_id])
            try:
                action = queue.apply_parse(
                    con,
                    doc_id,
                    stamp=str(r.get("extractor") or worker),
                    text=r.get("text"),
                    error=r.get("error"),
                    seconds=float(r.get("seconds") or 0.0),
                )
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(
                    {"doc_id": doc_id, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            actions[action] = actions.get(action, 0) + 1
            out["applied"] += 1
        out["actions"] = actions
        return out
    # embed
    model = str(payload.get("model") or "")
    emb = embeddings.current()
    if emb is None or model != emb.name:
        raise ValueError(
            f"the door embeds with {emb.name if emb else None}, not {model!r}"
        )
    chunks = [(int(c), k, v) for c, k, v in (payload.get("chunks") or [])]
    fields = [(int(d), v) for d, v in (payload.get("fields") or [])]
    _release("embed", [c for c, _, _ in chunks])
    _release("embed-doc", [d for d, _ in fields])
    if chunks:
        out["applied"] += store.store_embeddings(con, chunks, model)
        store.save_vectors(model)
    if fields:
        out["applied"] += store.store_document_embeddings(con, fields, model)
        store.save_document_vectors(model)
    return out
