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

import contextlib
import sqlite3
import time
from dataclasses import asdict
from typing import Any

from prax import embeddings, extraction, inbox, models, ontology, pipeline, store
from prax.parsers import figures, queue

LEASE_SECONDS = 900
# a worker's "not yet" (the server it needs is loading or down) keeps the
# item leased this long, so the next batches hold other work instead of
# the same ten items again — the follow-ups of the first papers marker
# read once starved the 228 behind them
DEFER_SECONDS = 600
STEPS = (
    "parse",
    "titles",
    "extract",
    "promote",
    "typing",
    "embed",
    "resolve",
    "adjudicate",
)


def _vision_is_free() -> bool:
    """Whether a reading by the vision model costs nothing (a local
    server): the readings the door asks for on its own are only those."""
    try:
        spec = models.resolve("vision")
    except Exception:  # noqa: BLE001 - a broken prax.yaml is not this step's problem
        return False
    return spec is not None and spec.kind == "openai"


def _formulas_are_free() -> bool:
    """Whether a formula reading costs nothing (a local server named by
    the ``formulas`` step): the door asks for those on its own."""
    try:
        spec = models.resolve("formulas")
    except Exception:  # noqa: BLE001
        return False
    return spec is not None and spec.kind == "openai"


def _follow_up(con: sqlite3.Connection, doc_id: int, extractor: str) -> bool:
    """The next reading after a finished one — the formulas of a text a
    parser just wrote with its mathematics — placed by the door in the
    finished request's stead (the outcome stays in ``parse_history``);
    never over a request still waiting."""
    reading = store.get_meta(con, doc_id).get("reading") or {}
    if reading.get("state") in ("requested", "leased"):
        return False
    store.request_reading(con, doc_id, extractor, by="door")
    return True


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


def _lease(
    step: str, items: list[int], worker: str, seconds: float = LEASE_SECONDS
) -> None:
    until = time.monotonic() + seconds
    for i in items:
        _leases[(step, i)] = (worker, until)


def _release(step: str, items: list[int]) -> None:
    for i in items:
        _leases.pop((step, i), None)


def renew(step: str, items: list[int], worker: str) -> int:
    """The worker holding those items keeps them another ``LEASE_SECONDS``:
    a book takes marker longer than one lease, and the worker beats while
    it reads. An item leased to another worker, or free, is left alone.
    Returns how many were renewed."""
    n = 0
    until = time.monotonic() + LEASE_SECONDS
    for i in items:
        held = _leases.get((step, i))
        if held is not None and held[0] == worker:
            _leases[(step, i)] = (worker, until)
            n += 1
    return n


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
    if step in ("extract", "promote"):
        onto = ontology.current()
        if step == "promote":
            # the flagged documents the promote step's model has not read
            # (whatever the scope: a flag is explicit); images included,
            # since the expensive pass reads the picture again first
            try:
                producer = extraction.current("promote").name
            except RuntimeError:  # the step is off on this host
                return {"step": step, "items": [], "lease_seconds": 0}
            due = [
                d["doc_id"]
                for d in store.promoted_documents(con, producer=producer)
                if not d["done"]
            ]
        else:
            due = store.select_for_extraction(
                con,
                ontology_version=onto.version,
                min_chars=pipeline.MIN_CHARS,
                onto=onto,
                sources=tuple(pipeline.CAPTURE_SOURCES)
                if scope == "captures"
                else None,
                skip_mime_prefix="image/",
            )
        items = []
        for doc_id in due:
            if len(items) >= limit or not _free(step, doc_id, now):
                continue
            doc = extraction.build_input(con, doc_id)
            item = {
                "doc_id": doc_id,
                "title": doc.title,
                "header": doc.header,
                "text": doc.text,
                "domains": doc.domains,
                "ontology_version": doc.ontology().version,
            }
            if step == "promote":
                row = store.get_document(con, doc_id)
                if row and (row["mime"] or "").startswith("image/"):
                    path = row.get("original_path")
                    item["image"] = {
                        "original": f"/doc/{doc_id}/original",
                        "filename": path.replace("\\", "/").rsplit("/", 1)[-1]
                        if path
                        else None,
                        "previous": row["text"],
                    }
            items.append(item)
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
    if step == "typing":
        # untyped review items to the typing model, a batch of items each;
        # nothing without a model for the step
        from prax import typing_pass

        if models.resolve("typing") is None:
            return {"step": step, "items": [], "lease_seconds": 0}
        items = [
            b
            for b in typing_pass.hand_out(con, limit=limit * 2)
            if all(_free(step, it["id"], now) for it in b["items"])
        ][:limit]
        _lease(step, [it["id"] for b in items for it in b["items"]], worker)
        return {"step": step, "items": items, "lease_seconds": LEASE_SECONDS}
    if step == "parse":
        from prax import parsers

        items = []
        # requested readings first: a person asked, whatever the scope; the
        # whole queue, oldest first — the status view's newest fifty hid
        # the 228 marker requests behind the follow-ups placed after them
        for req in store.reading_requests(con, limit=None, oldest_first=True):
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
            named = doc["meta"].get("parser")  # a document may name its parser
            exts = parsers.candidates(doc["mime"] or "", named)
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
                    **({"extractor": named} if named else {}),
                }
            )
        _lease(step, [i["doc_id"] for i in items], worker)
        return {"step": step, "items": items, "lease_seconds": LEASE_SECONDS}
    if step == "resolve":
        # the likely tier of entity resolution: one type's names to a
        # worker, which embeds them and posts the close pairs; a type is
        # due when its pairs are older than LIKELY_DAYS or were never
        # computed (the door never embeds: invariant 7)
        from prax import resolution

        emb = embeddings.current()
        if emb is None:
            return {"step": step, "type": None, "names": [], "lease_seconds": 0}
        runs = store.candidate_runs(con)
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() - resolution.LIKELY_DAYS * 86400),
        )
        for i, t in enumerate(sorted(resolution.LIKELY_TYPES)):
            if runs.get(t, "") >= cutoff or not _free(step, i, now):
                continue
            names = store.entity_names(con, t)
            if len(names) < 2:
                continue
            _lease(step, [i], worker)
            return {
                "step": step,
                "type": t,
                "names": names,
                "model": emb.name,
                "threshold": resolution.LIKELY_THRESHOLD,
                "lease_seconds": LEASE_SECONDS,
            }
        return {"step": step, "type": None, "names": [], "lease_seconds": 0}
    if step == "adjudicate":
        # the likely pairs nobody has decided, to a worker with the
        # adjudicate step's model (a paid one: the worker spends only when
        # told to); leased by the entity that would be dropped
        from prax import resolution

        if models.resolve("adjudicate") is None:
            return {"step": step, "items": [], "lease_seconds": 0}
        items = []
        for c in resolution.plan(con).likely:
            if len(items) >= limit or not _free(step, c.drop, now):
                continue
            items.append(
                {
                    "keep": c.keep,
                    "drop": c.drop,
                    "keep_name": c.keep_name,
                    "drop_name": c.drop_name,
                    "type": c.type,
                    "score": round(c.score, 4),
                }
            )
        _lease(step, [i["drop"] for i in items], worker)
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
    # "not yet": the item stays leased a while, the queue moves on
    deferred = [int(r["doc_id"]) for r in results if r.get("defer")]
    if deferred:
        _lease(step, deferred, worker, seconds=DEFER_SECONDS)
        out["deferred"] = len(deferred)
        results = [r for r in results if not r.get("defer")]
    if step in ("extract", "promote"):
        extractor = str(payload.get("extractor") or worker)
        prefix = "promote" if step == "promote" else "work"
        run = payload.get("run") or f"{prefix}-{time.strftime('%Y%m%dT%H%M%S')}"
        totals = extraction.ApplyReport()
        for r in results:
            doc_id = int(r["doc_id"])
            _release(step, [doc_id])
            if r.get("error"):
                out["errors"].append({"doc_id": doc_id, "error": r["error"]})
                with contextlib.suppress(Exception):  # the note is a nicety
                    extraction.note_failure(
                        con, doc_id, str(r["error"]), extractor=extractor
                    )
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
    if step == "resolve":
        from prax import resolution

        etype = str(payload.get("type") or "")
        if etype not in resolution.LIKELY_TYPES:
            raise ValueError(
                f"resolve takes a type among {sorted(resolution.LIKELY_TYPES)}"
            )
        emb = embeddings.current()
        model = str(payload.get("model") or "")
        if emb is None or model != emb.name:
            theirs = emb.name if emb else None
            raise ValueError(f"the door's names embed with {theirs}, not {model!r}")
        pairs = [(int(a), int(b), float(s)) for a, b, s in (payload.get("pairs") or [])]
        _release(step, [sorted(resolution.LIKELY_TYPES).index(etype)])
        out["applied"] = store.replace_entity_candidates(
            con, etype, pairs, producer=f"{model} via {worker}"
        )
        out["type"] = etype
        return out
    if step == "adjudicate":
        from prax import resolution

        model = str(payload.get("model") or worker)
        items = list(payload.get("items") or [])
        same = [bool(x) for x in (payload.get("same") or [])]
        if len(same) != len(items):
            raise ValueError("adjudicate takes one decision per item")
        _release(step, [int(it["drop"]) for it in items])
        rep = resolution.decide(con, items, resolution.DecidedAdjudicator(model, same))
        out["applied"] = rep.merged_likely
        out["declined"] = rep.declined
        return out
    if step == "typing":
        from prax import typing_pass

        model = str(payload.get("model") or worker)
        run = payload.get("run") or f"typing-model-{time.strftime('%Y%m%dT%H%M%S')}"
        for r in results:
            _release(step, [it["id"] for it in r.get("items") or []])
        rep = typing_pass.take_in(con, results, model=model, run=run)
        out["applied"] = rep.requests
        out["report"] = {
            "checked": rep.checked,
            "linked": rep.linked,
            "existing": rep.existing,
            "dropped": rep.dropped,
            "misfit": rep.misfit,
            "unanswered": rep.unanswered,
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
                    pages=int(r["pages"]) if r.get("pages") else None,
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
                # the edge of the process graph a marker read adds: a text
                # that now holds display equations gets their readings next,
                # as a fresh capture with figures gets the vision model
                if (
                    stamp.startswith("marker/")
                    and action in ("created", "upgraded")
                    and _formulas_are_free()
                    and store.has_unread_formulas(con, doc_id)
                ):
                    _follow_up(con, doc_id, "formulas")
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
