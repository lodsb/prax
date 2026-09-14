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

In scope ``all`` the parse step also hands out the documents whose text
an extractor prax has since revised would read differently
(``parsers.behind``), so a backlog pass, run nightly, brings the library
up to date with the code a few documents at a time; a re-read that comes
out the same costs the parse and nothing else.

The readings a person asks for on a document (``meta.reading``) go out
with the parse step, first. The door asks for two kinds itself, for
captures, when the vision model is a local server (nothing spent): an
image to describe, and the figures of a freshly parsed document to read.

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

from prax import embeddings, extraction, inbox, models, ontology, pipeline, store
from prax.parsers import figures, queue

LEASE_SECONDS = 900
STEPS = ("parse", "titles", "extract", "embed")


def _vision_is_free() -> bool:
    """Whether a reading by the vision model costs nothing (a local
    server): the readings the door asks for on its own are only those."""
    try:
        spec = models.resolve("vision")
    except Exception:  # noqa: BLE001 - a broken prax.yaml is not this step's problem
        return False
    return spec is not None and spec.kind == "openai"


def _ask_reading(con: sqlite3.Connection, doc_id: int, extractor: str) -> bool:
    """A reading request the door places for a capture — an image to
    describe, figures to read — when the model is free and nobody asked
    for one yet."""
    meta = store.get_meta(con, doc_id)
    if meta.get("reading"):
        return False
    store.request_reading(con, doc_id, extractor, by="door")
    return True


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


def _takes_previous(extractor: str) -> bool:
    from prax import parsers

    try:
        return parsers.by_name(extractor).previous
    except KeyError:
        return False


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
            con,
            ontology_version=onto.version,
            min_chars=pipeline.MIN_CHARS,
            onto=onto,
            sources=tuple(pipeline.CAPTURE_SOURCES) if scope == "captures" else None,
            skip_mime_prefix="image/",
        )
        items = []
        for doc_id in due:
            if len(items) >= limit or not _free(step, doc_id, now):
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
        # requested readings first: a person asked, whatever the scope
        for req in store.reading_requests(con):
            doc_id = req["doc_id"]
            if len(items) >= limit or not _free(step, doc_id, now):
                continue
            doc = store.get_document(con, doc_id, max_chars=0)
            if doc is None:
                continue
            path = doc.get("original_path")
            item = {
                "doc_id": doc_id,
                "mime": doc["mime"],
                "filename": path.replace("\\", "/").rsplit("/", 1)[-1]
                if path
                else None,
                "original": f"/doc/{doc_id}/original",
                "old_len": doc["text_len"],
                "extractor": req["extractor"],
                "mode": req.get("mode"),
                "force": True,
            }
            if doc["text_len"] and _takes_previous(req["extractor"]):
                # the extractor works on the current text: a second reading
                # of an image joins the first (vision.merge_readings), the
                # figures' readings and references go into the parsed text
                item["previous"] = store.get_document(con, doc_id)["text"]
            items.append(item)
        waiting = list(inbox.pending_captures(con))
        if scope == "all":
            # the backlog pass also brings texts up to date: documents read
            # by an extractor prax has revised since, a few per pass
            waiting += [i for i in queue.stale(con, limit=limit) if i not in waiting]
        for doc_id in waiting:
            if len(items) >= limit or not _free(step, doc_id, now):
                continue
            if not _in_scope(con, doc_id, scope):
                continue
            doc = store.get_document(con, doc_id, max_chars=0)
            if doc is None:
                continue
            exts = parsers.candidates(doc["mime"] or "")
            if not exts:
                # an image has no parser of its own: the vision model reads
                # it, as a reading the door asks for when that is free
                if (doc["mime"] or "").startswith("image/") and _vision_is_free():
                    _ask_reading(con, doc_id, "vision")
                continue
            if any(queue._seen(doc["meta"], e.stamp) for e in exts):
                # the chain was run and found nothing (a scan without a
                # text layer): a reading asked for on its page — OCR, the
                # vision model — is the way on, not another round
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
            stamp = str(r.get("extractor") or worker)
            try:
                action = queue.apply_parse(
                    con,
                    doc_id,
                    stamp=stamp,
                    text=r.get("text"),
                    error=r.get("error"),
                    seconds=float(r.get("seconds") or 0.0),
                    force=bool(r.get("force")),
                    keep_source=bool(r.get("keep_source")),
                )
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(
                    {"doc_id": doc_id, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            if r.get("requested"):
                store.finish_reading(
                    con, doc_id, outcome=action, stamp=stamp, error=r.get("error")
                )
            elif action in ("created", "upgraded") and _vision_is_free():
                # a freshly parsed capture with figures: the vision model reads
                # them next, as a reading the door asks for
                doc = store.get_document(con, doc_id)
                source = (doc or {}).get("meta", {}).get("source")
                if source in pipeline.CAPTURE_SOURCES and figures.refs(doc["text"]):
                    _ask_reading(con, doc_id, "figures")
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
