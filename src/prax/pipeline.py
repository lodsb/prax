"""The batch passes as library functions, and the pipeline that runs them
over new captures without anyone asking.

The worker's steps (``prax.worker``, through ``prax.work``) are the
fronts for ``extract_documents``, ``retitle_documents`` and
``embed_pending``; the door's own capture handling runs
``process_captures``, which takes a
capture from registered to searchable in the graph: parse, title,
extract, embed. Every pass announces itself as a job (``store.Job``) so
the UI can show it.

What the pipeline never does on its own: spend money (a step whose model
is the Claude API is skipped with a note), touch the curated imports
(only captures, uploads and dropped files are its business), or read a
document that has no text worth reading (``MIN_CHARS``), an image (the
vision step is a paid one), or a retired document.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from prax import (
    config,
    embeddings,
    extraction,
    models,
    ontology,
    store,
    summaries,
    titles,
)
from prax.parsers import figures

log = logging.getLogger("prax.pipeline")

MIN_CHARS = 300  # less than this is a stub, a cover or an error page
CAPTURE_SOURCES = ("capture", "upload", "inbox")
FETCH = 512  # chunks per embedding batch
Log = Callable[[str], None]


def _say(logger: Log | None, text: str) -> None:
    if logger:
        logger(text)
    else:
        log.info(text)


# ------------------------------------------------------------ extraction


@dataclass
class ExtractReport:
    n: int = 0
    totals: extraction.ApplyReport = field(default_factory=extraction.ApplyReport)
    spent: float = 0.0
    seconds: float = 0.0
    errors: list[tuple[int, str]] = field(default_factory=list)
    stopped: str | None = None  # a budget reached

    def __str__(self) -> str:
        t = self.totals
        return (
            f"{self.n} documents: {t.linked} edges added, {t.existing} existing,"
            f" {t.queued} queued for review, {t.rejected} rejected;"
            f" {len(self.errors)} errors; ~{self.spent:.2f} USD in {self.seconds:.0f} s"
        )


def extract_documents(
    con: sqlite3.Connection,
    ids: list[int],
    ext: extraction.Extractor,
    *,
    workers: int = 1,
    run: str | None = None,
    budget_usd: float | None = None,
    log: Log | None = None,
    job: store.Job | None = None,
) -> ExtractReport:
    """Read ``ids`` with ``ext`` and apply the triples. ``workers`` above one
    only for a served local model (the API is one at a time)."""
    rep = ExtractReport()
    run = run or "sync-" + time.strftime("%Y%m%dT%H%M%S")
    if workers > 1 and not isinstance(ext, extraction.LocalExtractor):
        workers = 1
    t0 = time.monotonic()

    def extract_one(doc_id: int) -> tuple[int, extraction.Extraction | None, str]:
        try:
            doc = extraction.build_input(con, doc_id)
            result = ext.extract(doc)
            if not result.triples and result.usage.get("dropped_lines"):
                result = ext.extract(doc)  # a sampled model's occasional blank
            return doc_id, result, ""
        except Exception as exc:  # noqa: BLE001 - one document must not stop the run
            return doc_id, None, f"{type(exc).__name__}: {exc}"

    pool = ThreadPoolExecutor(max_workers=workers) if workers > 1 else None
    stream = pool.map(extract_one, ids) if pool else map(extract_one, ids)
    try:
        for n, (doc_id, result, error) in enumerate(stream, 1):
            if budget_usd is not None and rep.spent >= budget_usd:
                rep.stopped = f"budget reached after {n - 1} documents"
                break
            rep.n = n
            if result is None:
                rep.errors.append((doc_id, error))
                _say(log, f"[{n}/{len(ids)}] doc {doc_id}: {error}")
            else:
                try:
                    r = extraction.apply(
                        con, doc_id, result, extractor=ext.name, run=run
                    )
                except Exception as exc:  # noqa: BLE001
                    rep.errors.append((doc_id, f"{type(exc).__name__}: {exc}"))
                    _say(
                        log,
                        f"[{n}/{len(ids)}] doc {doc_id}: {type(exc).__name__}: {exc}",
                    )
                    continue
                rep.spent += extraction.cost_usd(ext.name, result.usage)
                for k in ("linked", "existing", "queued", "rejected"):
                    setattr(rep.totals, k, getattr(rep.totals, k) + getattr(r, k))
                rate = (time.monotonic() - t0) / n
                _say(
                    log,
                    f"[{n}/{len(ids)}] doc {doc_id}: +{r.linked} edges,"
                    f" {r.queued} queued, {len(result.triples)} triples;"
                    f" ~{rep.spent:.2f} USD,"
                    f" {rate:.0f} s/doc, ~{rate * (len(ids) - n) / 3600:.1f} h left",
                )
            if job:
                job.update(done=n, note=f"doc {doc_id}")
    finally:
        if pool:
            pool.shutdown(wait=False, cancel_futures=True)
    rep.seconds = time.monotonic() - t0
    return rep


# ---------------------------------------------------------------- titles


@dataclass
class TitleReport:
    done: int = 0
    skipped: int = 0
    failed: int = 0
    unconfirmed: int = 0
    entity_actions: dict[str, int] = field(default_factory=dict)
    guesses: list[dict[str, Any]] = field(default_factory=list)  # when not applied
    seconds: float = 0.0

    def __str__(self) -> str:
        return (
            f"{self.done} retitled, {self.skipped} without text, {self.failed} without"
            f" a usable guess, {self.unconfirmed} unconfirmed (file name kept),"
            f" entities {self.entity_actions or 'untouched'}; {self.seconds:.0f} s"
        )


def pdf_title(con: sqlite3.Connection, doc: dict[str, Any]) -> str | None:
    """The title in a PDF's metadata, a hint for the guess."""
    if doc["mime"] != "application/pdf":
        return None
    try:
        import pymupdf

        path = config.archive_dir() / doc["hash"][:2] / doc["hash"]
        with pymupdf.open(str(path)) as pdf:
            return titles.pdf_meta_title((pdf.metadata or {}).get("title"))
    except Exception:  # noqa: BLE001 - a hint only
        return None


def file_name(doc: dict[str, Any]) -> str | None:
    z = (doc["meta"] or {}).get("zotero") or {}
    return z.get("filename") or doc["title"]


def summaries_needed(
    con: sqlite3.Connection,
    *,
    ids: list[int] | None = None,
    untried_only: bool = False,
) -> list[tuple[int, str]]:
    """``(doc_id, lang)`` for the documents whose summary is not in the
    language the document field is written in.

    The language is read from ``meta.summary_lang``, which the extraction
    writes and the ``languages`` pass of ``prax maintain`` fills in for
    the summaries written before it existed. A summary nobody has
    detected yet is not handed out: detecting it is free and belongs to
    that pass, not to a model call.

    A translation already made but not good enough to keep is asked for
    again, from the original ``meta.summaries`` held on to. That is what
    makes the check worth tightening: a model that handed the prompt's
    labels back with the answer (2026-09-24) is corrected by improving
    ``summaries.acceptable`` and running the pass, not by repairing rows.
    It cannot loop — a second refusal is the worker's, which writes
    ``meta.summary_tried``.

    With ``untried_only`` the documents a translation already failed on
    are left out, as a guess costs a model call; a pass that wants them
    again clears ``meta.summary_tried``.
    """
    where = (
        " WHERE json_extract(meta, '$.summary') IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL"
    )
    if untried_only:
        where += " AND json_extract(meta, '$.summary_tried') IS NULL"
    args: tuple[Any, ...] = ()
    if ids is not None:
        where += f" AND id IN ({','.join('?' * len(ids))})"
        args = tuple(ids)
    # two narrow questions rather than one pass over every summary: this
    # runs on every hand-out, and a worker asks every twenty seconds
    out: list[tuple[int, str]] = [
        (r["id"], str(r["lang"]))
        for r in con.execute(
            "SELECT id, json_extract(meta, '$.summary_lang') AS lang FROM documents"
            + where
            + " AND json_extract(meta, '$.summary_lang') IS NOT NULL"
            " AND json_extract(meta, '$.summary_lang') != ?",
            (*args, summaries.CANONICAL),
        )
    ]
    # and the ones a translation has already been made for, which are only
    # the documents this pass has touched
    for r in con.execute(
        "SELECT id, json_extract(meta, '$.summary') AS summary,"
        " json_extract(meta, '$.summaries') AS held FROM documents"
        + where
        + " AND json_extract(meta, '$.summaries') IS NOT NULL"
        " AND json_extract(meta, '$.summary_lang') = ?",
        (*args, summaries.CANONICAL),
    ):
        native = summaries.native(json.loads(r["held"] or "{}"))
        if native is not None and summaries.acceptable(r["summary"], native[1]):
            out.append((r["id"], native[0]))  # translated, but not usably
    return sorted(out)


def titles_needed(
    con: sqlite3.Connection,
    *,
    reasons: tuple[str, ...] = ("empty", "filename", "zotero-auto", "caps"),
    ids: list[int] | None = None,
    untried_only: bool = False,
) -> list[tuple[int, str]]:
    """``(doc_id, why)`` for the documents whose title is not one. With
    ``untried_only`` (the pipeline) documents a guess already failed on,
    and documents without text, are left out: a guess costs a model call."""
    out = []
    sql = "SELECT id, title, text_hash, meta FROM documents"
    args: tuple[Any, ...] = ()
    if ids is not None:
        sql += f" WHERE id IN ({','.join('?' * len(ids))})"
        args = tuple(ids)
    for r in con.execute(sql + " ORDER BY id", args):
        meta = json.loads(r["meta"] or "{}")
        if meta.get("retired"):
            continue
        if untried_only and (meta.get("titles_tried") or not r["text_hash"]):
            continue  # a guess that failed before, or nothing to guess from
        why = titles.needs_title(r["title"], meta)
        if why in reasons:
            out.append((r["id"], why))
    return out


def _mark_tried(con: sqlite3.Connection, doc_id: int, run: str, why: str) -> None:
    """Remember that a guess was made and not applied, so the pipeline does
    not ask the model again every pass (a later titles pass over the document
    or ``--apply-low`` still can)."""
    meta = store.get_meta(con, doc_id)
    meta["titles_tried"] = {"run": run, "why": why}
    store.set_meta(con, doc_id, meta)


def retitle_documents(
    con: sqlite3.Connection,
    chosen: list[tuple[int, str]],
    runtime: Any | None,
    *,
    run: str | None = None,
    apply: bool = True,
    apply_low: bool = False,
    log: Log | None = None,
    job: store.Job | None = None,
) -> TitleReport:
    """Give the chosen documents a title: the recase rule for ALL CAPS,
    the model's guess (confirmed by the text) for the rest. With ``apply``
    off the guesses are returned and nothing changes."""
    rep = TitleReport()
    run = run or "titles-" + time.strftime("%Y%m%dT%H%M%S")
    t0 = time.monotonic()
    for n, (doc_id, why) in enumerate(chosen, 1):
        doc = store.get_document(con, doc_id, max_chars=60000)
        if doc is None:
            continue
        old = doc["title"] or ""
        confidence: str | None = None
        if why == "caps":
            new, source = titles.recase(old), "recase"
        else:
            if not doc["text"].strip() or runtime is None:
                rep.skipped += 1
                continue
            guess = titles.guess_title(
                runtime,
                doc["text"],
                filename=file_name(doc),
                heading=titles.first_heading(doc["text"]),
                pdf_title=pdf_title(con, doc),
            )
            if guess is None:
                rep.failed += 1
                _mark_tried(con, doc_id, run, "no usable guess")
                continue
            new, source, confidence = guess.title, runtime.name, guess.confidence
            if confidence == "low" and apply and not apply_low:
                rep.unconfirmed += 1
                _mark_tried(con, doc_id, run, f"unconfirmed: {new[:80]}")
                continue
        if not apply:
            rep.guesses.append(
                {
                    "doc_id": doc_id,
                    "why": why,
                    "old": old,
                    "new": new,
                    "confidence": confidence,
                }
            )
            continue
        r = store.retitle(
            con, doc_id, new, source=source, run=run, confidence=confidence
        )
        if r["changed"]:
            rep.done += 1
            if r["entity"]:
                rep.entity_actions[r["entity"]] = (
                    rep.entity_actions.get(r["entity"], 0) + 1
                )
        if job and (n % 10 == 0 or n == len(chosen)):
            job.update(done=n, note=f"doc {doc_id}")
        if log and (n % 50 == 0 or n == len(chosen)):
            rate = (time.monotonic() - t0) / n
            _say(log, f"  {n}/{len(chosen)} ({rate:.1f} s/doc)")
    rep.seconds = time.monotonic() - t0
    return rep


# ------------------------------------------------------------- embedding


class IndexBusy(RuntimeError):
    """The index file is mapped by another process (the door on Windows):
    a save would fail, so nothing is embedded."""


def index_writable(model: str) -> bool:
    """Whether the index files can be replaced: on Windows a file another
    process has mapped refuses to be renamed, and that is what the save
    does. A rename there and back is the probe."""
    store._drop_index_views(model)  # this process's own views are not the question
    for path in (store._index_path(model), store._doc_index_path(model)):
        if not path.exists():
            continue
        probe = path.with_suffix(path.suffix + ".probe")
        try:
            os.rename(path, probe)
            os.rename(probe, path)
        except OSError:
            if probe.exists() and not path.exists():
                os.rename(probe, path)
            return False
    return True


def release_door_views(door: str, *, token: str | None = None) -> bool:
    """Ask a running door to drop its memory-mapped index views so the
    files can be replaced; it reopens them on the next query."""
    req = urllib.request.Request(f"{door.rstrip('/')}/vectors/release", method="POST")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except OSError:
        return False


def _save_with_retry(
    save: Callable[[], dict[str, Any]], release: Callable[[], Any] | None
) -> dict[str, Any]:
    """The door may have mapped the index again between the probe and the
    save (a query came in): ask it to let go and try again, a few times."""
    last: OSError | None = None
    for attempt in range(5):
        try:
            return save()
        except OSError as exc:  # WinError 5 / 32: the file is mapped
            last = exc
            if release:
                release()
            time.sleep(0.5 * (attempt + 1))
    assert last is not None
    raise IndexBusy(f"the index could not be replaced: {last}") from last


def embed_pending(
    con: sqlite3.Connection,
    emb: embeddings.Embedder,
    *,
    limit: int | None = None,
    save_every: int = 50_000,
    log: Log | None = None,
    job: store.Job | None = None,
    release: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Embed the chunks and document fields without a vector, saving the
    index files. Raises ``IndexBusy`` before any work when a file cannot
    be replaced (``release`` asks the door to let go and is retried)."""
    # new vectors go into the delta file, which nobody maps; only a merge
    # replaces the main file, and _save_with_retry handles that one
    status = store.vec_status(con)
    index_count = status["index"]["count"] if status["index"] else 0
    booked = status["models"].get(emb.name, 0)
    out: dict[str, Any] = {"chunks": 0, "fields": 0, "reconciled": None}
    if index_count != booked:
        out["reconciled"] = store.compact_vectors(con, emb.name)
    pending = store.count_pending_embeddings(con, emb.name)
    if job:
        job.update(total=pending, done=0)
    done = since_save = 0
    t0 = time.monotonic()
    while True:
        want = FETCH if limit is None else min(FETCH, limit - done)
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
        if since_save >= save_every:
            _save_with_retry(lambda: store.save_vectors(emb.name), release)
            since_save = 0
        rate = done / max(1e-9, time.monotonic() - t0)
        if job:
            job.update(done=done, note=f"{rate:.0f} chunks/s")
        _say(log, f"[{done}/{pending}] {rate:.1f} chunks/s")
    if done or out["reconciled"]:
        # replacing the 800 MB index file is the expensive part: only when
        # something changed in it
        stats = _save_with_retry(lambda: store.save_vectors(emb.name), release)
        out["index_count"] = stats["count"]
        out["bytes"] = stats["bytes"]
    out["chunks"] = done
    fields = 0
    while True:
        rows = store.pending_document_embeddings(con, emb.name, limit=FETCH)
        if not rows:
            break
        vectors = emb.embed([r["text"] for r in rows])
        store.store_document_embeddings(
            con,
            [(r["doc_id"], v) for r, v in zip(rows, vectors, strict=True)],
            emb.name,
        )
        fields += len(rows)
    if fields:
        dstats = _save_with_retry(
            lambda: store.save_document_vectors(emb.name), release
        )
        out["doc_index_count"] = dstats["count"]
    out["fields"] = fields
    out["seconds"] = time.monotonic() - t0
    return out


# ----------------------------------------------------------- the pipeline


def _paid(spec: models.ModelSpec | None) -> bool:
    return spec is not None and spec.paid


def captures_ready(con: sqlite3.Connection, onto: ontology.Ontology) -> list[int]:
    """Captures with text that the current ontology has not read yet, worth
    reading: not an image, not retired, at least ``MIN_CHARS`` of text."""
    return store.select_for_extraction(
        con,
        ontology_version=onto.version,
        min_chars=MIN_CHARS,
        onto=onto,
        sources=tuple(CAPTURE_SOURCES),
        skip_mime_prefix="image/",
    )


def process_captures(
    con: sqlite3.Connection,
    *,
    parse: bool = True,
    retitle: bool = True,
    extract: bool = True,
    embed: bool = True,
    workers: int = 3,
    door: str | None = None,
    token: str | None = None,
    log: Log | None = None,
) -> dict[str, Any]:
    """Take new captures the rest of the way: parse what the door only
    registered, give file-name titles a real one, read them into the
    graph, embed what has no vector. Each step is a job; a step whose model
    would cost money is skipped and says so. Returns what each step did."""

    out: dict[str, Any] = {}
    release = (lambda: release_door_views(door, token=token)) if door else None
    try:
        _parse_step(con, out, log) if parse else None
    except Exception as exc:  # noqa: BLE001 - the pass goes on
        out["parse"] = f"failed: {type(exc).__name__}: {exc}"
        _say(log, out["parse"])
    try:
        _titles_step(con, out, log) if retitle else None
    except Exception as exc:  # noqa: BLE001
        out["titles"] = f"failed: {type(exc).__name__}: {exc}"
        _say(log, out["titles"])
    try:
        _extract_step(con, out, log, workers) if extract else None
    except Exception as exc:  # noqa: BLE001
        out["extract"] = f"failed: {type(exc).__name__}: {exc}"
        _say(log, out["extract"])
    try:
        _embed_step(con, out, log, release) if embed else None
    except Exception as exc:  # noqa: BLE001
        out["embed"] = f"failed: {type(exc).__name__}: {exc}"
        _say(log, out["embed"])
    return out


def _parse_step(con: sqlite3.Connection, out: dict[str, Any], log: Log | None) -> None:
    from prax import inbox, parsers
    from prax.parsers import queue

    ids = []
    for doc_id in inbox.pending_captures(con):
        doc = store.get_document(con, doc_id, max_chars=0)
        if doc is None:
            continue
        exts = parsers.candidates(doc["mime"] or "")
        if exts and not queue._seen(doc["meta"], exts[0].stamp):
            ids.append(doc_id)
    if ids:
        with store.Job(con, "parse", total=len(ids)) as job:
            rep = queue.run(
                con,
                ids,
                log=lambda n, i, a: job.update(done=n + 1, note=f"{a} doc {i}"),
            )
            job.note(str(rep))
        out["parse"] = str(rep)
        _say(log, f"parse: {rep}")


def _titles_step(con: sqlite3.Connection, out: dict[str, Any], log: Log | None) -> None:
    spec = models.resolve("titles")
    chosen = [
        (i, why)
        for i, why in titles_needed(con, untried_only=True)
        if con.execute(
            "SELECT json_extract(meta, '$.source') FROM documents WHERE id = ?",
            (i,),
        ).fetchone()[0]
        in CAPTURE_SOURCES
    ]
    if spec is None:  # no model: only the recase rule can do anything
        chosen = [c for c in chosen if c[1] == "caps"]
    if chosen and _paid(spec):
        out["titles"] = f"skipped: the titles step is {spec.name} (paid)"
    elif chosen:
        runtime = models.runtime(spec) if spec else None
        with store.Job(con, "titles", total=len(chosen)) as job:
            rep = retitle_documents(con, chosen, runtime, job=job, log=log)
            job.note(str(rep))
        out["titles"] = str(rep)
        _say(log, f"titles: {rep}")


def _extract_step(
    con: sqlite3.Connection, out: dict[str, Any], log: Log | None, workers: int
) -> None:
    spec = models.resolve("extract")
    onto = ontology.current()
    ids = captures_ready(con, onto)
    if ids and (spec is None or _paid(spec)):
        out["extract"] = (
            f"skipped: {len(ids)} documents wait; the extract step is"
            f" {spec.name if spec else 'not configured'}" + (" (paid)" if spec else "")
        )
        _say(log, out["extract"])
    elif ids:
        ext = extraction.current("extract")
        with store.Job(con, "extract", total=len(ids), note=ext.name) as job:
            rep = extract_documents(con, ids, ext, workers=workers, job=job, log=log)
            job.note(str(rep))
        out["extract"] = str(rep)
        _say(log, f"extract: {rep}")


FIELD_BATCH = 200  # document fields gathered before an embed pass
FIELD_WAIT_S = 15 * 60  # or this long, whichever comes first
_last_embed = [0.0]


def _embed_step(
    con: sqlite3.Connection,
    out: dict[str, Any],
    log: Log | None,
    release: Callable[[], Any] | None,
) -> None:
    emb = embeddings.current()
    if emb is not None and store.vectors_available():
        pending = store.count_pending_embeddings(con, emb.name)
        pending_fields = store.count_pending_document_embeddings(con, emb.name)
        # a backlog pass changes a document field every few seconds (its
        # summary): those are gathered and embedded in batches, so the
        # index files are not replaced every pass
        due = (
            pending > 0
            or pending_fields >= FIELD_BATCH
            or (pending_fields > 0 and time.monotonic() - _last_embed[0] > FIELD_WAIT_S)
        )
        if due:
            _last_embed[0] = time.monotonic()
            try:
                with store.Job(con, "embed", total=pending, note=emb.name) as job:
                    rep = embed_pending(con, emb, job=job, log=log, release=release)
                    job.note(
                        f"{rep['chunks']} chunks, {rep['fields']} fields in"
                        f" {rep.get('seconds', 0):.0f} s"
                    )
                out["embed"] = rep
                _say(log, f"embed: {rep['chunks']} chunks, {rep['fields']} fields")
            except IndexBusy as exc:
                out["embed"] = f"deferred: {exc}"
                _say(log, out["embed"])


# ------------------------------------------------- the readings the door asks for
# The edges of the process graph: after a parse lands, what the door asks
# the worker to read next, on its own — only from a model that costs
# nothing (a local server), never over a request still waiting, one at a
# time (a document has one reading slot; the next is placed when this one
# lands). Both roads into the library call this: a capture parsed inline
# at ingest, and a parse posted by a worker.


def _free(step: str) -> bool:
    """Whether a reading by that step's model costs nothing (a local
    server): the readings the door asks for on its own are only those."""
    try:
        spec = models.resolve(step)
    except Exception:  # noqa: BLE001 - a broken prax.yaml is not this step's problem
        return False
    return spec is not None and spec.kind == "openai"


def vision_is_free() -> bool:
    return _free("vision")


def formulas_are_free() -> bool:
    return _free("formulas")


def polish_is_free() -> bool:
    return _free("polish")


def _wants_crops(con: sqlite3.Connection, doc_id: int) -> bool:
    """Whether this document holds a caption with no picture behind it —
    a figure drawn with vector paths, which no extractor pulls out."""
    doc = store.get_document(con, doc_id)
    if not doc or doc.get("mime") != "application/pdf":
        return False
    lines = (doc.get("text") or "").split("\n")
    return bool(figures.bare_captions(lines))


def follow_ups(
    con: sqlite3.Connection, doc_id: int, *, stamp: str, action: str
) -> str | None:
    """The reading the door places after a text landed under ``stamp``
    with ``action`` (``created``/``upgraded``; nothing after ``same`` or an
    error), or None. A video capture with an automatic transcript gets the
    polish first and its frames read once that has landed; a marker read
    with display equations gets the formula readings; a capture with
    figures nobody has read gets the vision model."""
    if action not in ("created", "upgraded"):
        return None
    meta = store.get_meta(con, doc_id)
    # what the document is already waiting for: since migration 21 a
    # reading queues beside the others instead of replacing them, so the
    # door only declines to ask for the same one twice
    pending = {r["extractor"] for r in store.pending_readings(con, doc_id)}
    video = meta.get("video") or {}
    if (
        video.get("captions") == "asr"
        and not stamp.startswith("polish/")
        and polish_is_free()
    ):
        if "polish" not in pending:
            store.request_reading(con, doc_id, "polish", by="door")
        return "polish"
    if (
        stamp.startswith("marker/")
        and formulas_are_free()
        and store.has_unread_formulas(con, doc_id)
    ):
        if "formulas" not in pending:
            store.request_reading(con, doc_id, "formulas", by="door")
        return "formulas"
    # a PDF whose captions have no picture: the crop pass renders them off
    # the page and runs no model at all, so there is nothing to be free.
    # Never after itself, and never for a scan (its pages are the picture)
    if (
        stamp.startswith(("pymupdf4llm/", "marker/"))
        and "figure-crops" not in pending
        and _wants_crops(con, doc_id)
    ):
        store.request_reading(con, doc_id, "figure-crops", by="door")
        return "figure-crops"
    # never the edge whose reading just landed: a model that read none of
    # the figures would be asked for them without end
    if (
        meta.get("source") in CAPTURE_SOURCES
        and not stamp.startswith("figures/")
        and vision_is_free()
    ):
        doc = store.get_document(con, doc_id)
        if doc and any(not r["described_by"] for r in figures.refs(doc["text"])):
            if "figures" not in pending:
                store.request_reading(con, doc_id, "figures", by="door")
            return "figures"
    return None
