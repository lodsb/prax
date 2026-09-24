"""The worker: does the model work the door hands out, on this machine or
another, and never opens the database.

    prax work --door http://<board>:8000 --watch

Each pass asks the door for a batch per step (parse, titles, extract,
embed), runs it with the models of this machine's prax.yaml, and posts
the results. A step whose model would cost money is skipped with a note,
so a worker left running never spends. Files in this machine's drop
folders (``Downloads/prax-inbox``, ``--also``) are uploaded to the door
with their sidecars, then removed, the way the store host's own folder
is consumed. The worker announces itself as a job with heartbeats, so
the Jobs view shows it wherever it runs.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Self

from prax import (
    drop,
    embeddings,
    extraction,
    hostinfo,
    language,
    models,
    parsers,
    sections,
    summaries,
    titles,
    usage,
    vocabulary,
    work,
)
from prax import steps as steps_mod
from prax.client import Door
from prax.parsers import figures

log = logging.getLogger("prax.worker")
Log = Callable[[str], None]
STEPS = steps_mod.STEPS
# passes that fail the same way in a row before the worker stops and lets
# the supervisor start one with the current code (2026-09-24: 519)
GIVE_UP_AFTER = 5


# ------------------------------------------------------------------ steps


def _requested(
    it: dict[str, Any], *, spend: bool = False
) -> tuple[list[Any], str | None]:
    """The extractors to try for an item: a requested reading names one
    (and is refused when its model would cost money and ``spend`` was not
    given, so nobody's click spends unasked — the request stays on the
    document with that reason); otherwise the candidates for the type.

    What a reading costs is the step's model it runs
    (``store.READING_STEPS``), never the reading's own name: `figures`
    and `vision-pages` are the vision step as much as `vision` is, and
    keying on the name let them past this guard until 2026-09-23.
    """
    name = it.get("extractor")
    if not name:
        return parsers.candidates(it.get("mime") or ""), None
    try:
        ext = parsers.by_name(name)
    except KeyError as exc:
        return [], str(exc)
    step = steps_mod.READING_STEPS.get(name)
    if step and not spend:
        spec = models.resolve(step)
        if _paid(spec):
            return [], (
                f"the {step} step is {spec.name if spec else 'none'} (paid):"
                " run it with --spend as the promote step, or point the step"
                " at a local server"
            )
    if ext.check is None and not ext.available():
        return [], f"{name} is not installed on this worker"
    return [ext], None  # a server that is not up says so itself: NotYet


def do_parse(
    door: Door,
    items: list[dict[str, Any]],
    *,
    log_: Log | None = None,
    landed: Callable[[list[dict[str, Any]]], None] | None = None,
    spend: bool = False,
) -> list[dict[str, Any]]:
    """Every item through its extractor chain. ``landed`` is called with
    each document's results as soon as it is done — the caller posts them
    then, so a batch of books lands one book at a time and not when the
    last one is read."""
    results: list[dict[str, Any]] = []

    def done(*rs: dict[str, Any]) -> None:
        results.extend(rs)
        if landed is not None:
            landed(list(rs))

    for it in items:
        doc_id = it["doc_id"]
        usage.clear()  # what this document's readings pay for, and nothing before
        exts, refused = _requested(it, spend=spend)
        if not exts:
            done(
                {
                    "doc_id": doc_id,
                    "extractor": it.get("extractor") or "none",
                    "error": refused or "no extractor for this type",
                    "requested": it.get("extractor"),
                }
            )
            continue
        try:
            data = door.get_bytes(it["original"])
        except Exception as exc:  # noqa: BLE001
            done(
                {"doc_id": doc_id, "extractor": exts[0].stamp, "error": f"fetch: {exc}"}
            )
            continue
        pages = _page_count(data)  # a fact of the original, for the door's record
        last = None
        tried: list[
            dict[str, Any]
        ] = []  # the chain's earlier attempts, posted with the outcome
        for ext in exts:
            t0 = time.monotonic()
            try:
                with (
                    _mode(ext.name, it.get("mode")),
                    figures.fetching(
                        lambda ref, d=door, i=doc_id: _figure_from_door(d, i, ref)
                    ),
                ):
                    stamp = ext.stamp
                    text = ext(
                        data, filename=it.get("filename"), previous=it.get("previous")
                    ).strip()
            except (parsers.NotYet, models.ServerNotReady) as exc:
                # the server it reads through is loading or down: not the
                # document's fault; deferred, so the door leaves it leased a
                # while and hands out other work meanwhile
                _say(log_, f"parse doc {doc_id}: not yet — {exc}")
                done(*tried, {"doc_id": doc_id, "defer": True})
                break
            except Exception as exc:  # noqa: BLE001
                last = (ext.stamp, f"{type(exc).__name__}: {exc}")
                if ext is not exts[-1]:
                    # the chain goes on; the door records this attempt too,
                    # so it can see the chain was run and not hand the
                    # document out again (a scan refused by the first
                    # extractor, empty for the fallback, came back every
                    # cycle otherwise)
                    tried.append(
                        {
                            "doc_id": doc_id,
                            "extractor": ext.stamp,
                            "error": last[1],
                            "pages": pages,
                        }
                    )
                continue
            done(
                *tried,
                {
                    "doc_id": doc_id,
                    "extractor": stamp,
                    "text": text,
                    "seconds": round(time.monotonic() - t0, 2),
                    "force": bool(it.get("force")),
                    "requested": it.get("extractor"),
                    "keep_source": ext.annotates,
                    "pages": pages,
                    # what the reading paid for, for the door's ledger
                    "usage": usage.take(),
                },
            )
            pictures = len(figures.DATA_IMAGE.findall(text))
            words = len(figures.DATA_IMAGE.sub("", text)) if pictures else len(text)
            _say(
                log_,
                f"parse doc {doc_id}: {words} chars"
                + (f" + {pictures} pictures to file" if pictures else "")
                + f" ({stamp})",
            )
            break
        else:
            done(
                *tried,
                {
                    "doc_id": doc_id,
                    "extractor": last[0] if last else exts[0].stamp,
                    "error": last[1] if last else "failed",
                    "requested": it.get("extractor"),
                    "pages": pages,
                },
            )
    return results


BEAT_SECONDS = 300  # a third of the door's lease: the beat renews what is held


class _Beating:
    """A heartbeat beside a long batch: every ``BEAT_SECONDS`` the session
    is beaten (so the door does not reap it for silence) and the leases of
    the items still in flight are renewed (so the door does not hand them
    out again while a book is being read). ``landed(ids)`` takes items out
    of flight as their results are posted."""

    def __init__(
        self, door: Door, session: int | None, step: str, items: list[int]
    ) -> None:
        self.door = door
        self.session = session
        self.step = step
        self.in_flight = set(items)
        self.done = 0
        self.total = len(items)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def landed(self, ids: list[int]) -> None:
        with self.lock:
            self.in_flight.difference_update(ids)
            self.done = self.total - len(self.in_flight)
        self.beat()

    def beat(self) -> None:
        if self.session is None:
            return
        with self.lock:
            body = {
                "done": self.done,
                "total": self.total,
                "renew": {"step": self.step, "items": sorted(self.in_flight)},
            }
        with contextlib.suppress(Exception):  # a missed beat is not a failed batch
            self.door.post_json(f"/work/session/{self.session}", body)

    def _run(self) -> None:
        while not self.stop.wait(BEAT_SECONDS):
            self.beat()

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        self.thread.join(timeout=5)


def _figure_from_door(door: Door, doc_id: int, ref: str) -> tuple[bytes, str] | None:
    """A figure the original does not hold (a filed picture of a scanned
    page): the door serves it out of its archive."""
    try:
        data = door.get_bytes(f"/doc/{doc_id}/figure/{ref}")
    except Exception:  # noqa: BLE001 - not there: the reading skips it
        return None
    return (data, figures.media_of(data)) if data else None


def _page_count(data: bytes) -> int | None:
    """How many pages a PDF has, or None for anything else (and for a PDF
    pymupdf cannot open: the extractor will say so in its own words)."""
    if data[:5] != b"%PDF-":
        return None
    try:
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return int(doc.page_count)
    except Exception:  # noqa: BLE001
        return None


_MODE_SETTINGS = {
    "vision-pages": "PRAX_VISION_PAGES",
    "figures": "PRAX_FIGURES",
    "pymupdf4llm-ocr": "PRAX_OCR_LANGUAGE",
    "marker": "PRAX_MARKER_MODE",
    "formulas": "PRAX_FORMULA_READINGS",
}


@contextlib.contextmanager
def _mode(extractor: str, mode: str | None) -> Iterator[None]:
    """The requested mode as the extractor's setting for one call
    (``vision-pages``: every page or the scans; ``figures``: every image
    or the captioned ones; OCR: the recognizer's language)."""
    variable = _MODE_SETTINGS.get(extractor)
    if not mode or variable is None:
        yield
        return
    before = os.environ.get(variable)
    os.environ[variable] = mode
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = before


def do_titles(
    items: list[dict[str, Any]], runtime: Any | None, *, log_: Log | None = None
) -> list[dict[str, Any]]:
    results = []
    for it in items:
        doc_id, why, old = it["doc_id"], it["why"], it.get("title") or ""
        if why == "caps":
            results.append(
                {"doc_id": doc_id, "title": titles.recase(old), "source": "recase"}
            )
            continue
        if runtime is None or not (it.get("text") or "").strip():
            results.append(
                {
                    "doc_id": doc_id,
                    "tried": "no titles model" if runtime is None else "no text",
                }
            )
            continue
        try:
            guess = titles.guess_title(
                runtime,
                it["text"],
                filename=it.get("filename"),
                heading=titles.first_heading(it["text"]),
                pdf_title=None,
            )
        except models.ServerNotReady as exc:
            # the titles model's server is loading or down: deferred, like
            # a reading; the pass goes on with the rest and posts them
            _say(log_, f"title doc {doc_id}: not yet — {exc}")
            results.append({"doc_id": doc_id, "defer": True})
            continue
        if guess is None:
            results.append({"doc_id": doc_id, "tried": "no usable guess"})
        elif guess.confidence == "low":
            results.append(
                {"doc_id": doc_id, "tried": f"unconfirmed: {guess.title[:80]}"}
            )
        else:
            results.append(
                {
                    "doc_id": doc_id,
                    "title": guess.title,
                    "source": runtime.name,
                    "confidence": guess.confidence,
                }
            )
            _say(log_, f"title doc {doc_id}: {guess.title[:60]!r}")
    return results


def do_summaries(
    items: list[dict[str, Any]], runtime: Any | None, *, log_: Log | None = None
) -> list[dict[str, Any]]:
    """Each summary translated into the language the document field is
    written in. Nothing reads the document: the summary is the input."""
    results = []
    for it in items:
        doc_id, lang = it["doc_id"], it.get("lang")
        if runtime is None:
            results.append({"doc_id": doc_id, "tried": "no summaries model"})
            continue
        try:
            got = summaries.translate(
                runtime,
                str(it.get("summary") or ""),
                lang=lang,
                title=str(it.get("title") or ""),
            )
        except models.ServerNotReady as exc:
            _say(log_, f"summary doc {doc_id}: not yet — {exc}")
            results.append({"doc_id": doc_id, "defer": True})
            continue
        if got is None:
            results.append({"doc_id": doc_id, "tried": f"no usable {lang} translation"})
            continue
        results.append(
            {
                "doc_id": doc_id,
                "summary": got.text,
                "lang": language.canonical(),
                "source": runtime.name,
            }
        )
        _say(log_, f"summary doc {doc_id} ({lang}): {got.text[:60]!r}")
    return results


def do_sections(
    items: list[dict[str, Any]], runtime: Any | None, *, log_: Log | None = None
) -> list[dict[str, Any]]:
    """Each of a document's chapters read in turn.

    A document with no section long enough to be a chapter comes back
    with an empty list, which is an answer: the door records it against
    the text it was read from, and the document is not offered again
    until that text changes.
    """
    results = []
    for it in items:
        doc_id = it["doc_id"]
        done: list[dict[str, Any]] = []
        deferred = False
        for part in it.get("sections") or []:
            if runtime is None:
                break
            try:
                got = sections.summarize(
                    runtime,
                    str(part.get("heading") or ""),
                    str(part.get("text") or ""),
                    title=str(it.get("title") or ""),
                )
            except models.ServerNotReady as exc:
                _say(log_, f"sections doc {doc_id}: not yet - {exc}")
                deferred = True
                break
            if got is not None:
                done.append(got.as_meta())
        if deferred:
            results.append({"doc_id": doc_id, "defer": True})
            continue
        results.append(
            {
                "doc_id": doc_id,
                "sections": done,
                "source": runtime.name if runtime else "none",
            }
        )
        if done:
            _say(log_, f"sections doc {doc_id}: {len(done)} sections")
    return results


def do_vocabulary(
    items: list[dict[str, Any]], runtime: Any | None, *, log_: Log | None = None
) -> list[dict[str, Any]]:
    """Each name asked of the model: what does English call this thing?

    The commonest answer is the name it was given — a rare English term
    nobody else in the library wrote down looks foreign to the candidate
    net and is handed back unchanged, which costs the call and nothing
    else.
    """
    results = []
    for it in items:
        entity_id, name = it["id"], str(it.get("name") or "")
        if runtime is None or not name:
            results.append({"id": entity_id, "name": name, "changed": False})
            continue
        try:
            got = vocabulary.rename(
                runtime,
                name,
                str(it.get("type") or "concept"),
                context=str(it.get("context") or ""),
            )
        except models.ServerNotReady as exc:
            _say(log_, f"name {entity_id}: not yet — {exc}")
            results.append({"id": entity_id, "defer": True})
            continue
        if got is None:
            results.append({"id": entity_id, "name": name, "changed": False})
            continue
        results.append({"id": entity_id, "name": got.name, "changed": got.changed})
        if got.changed:
            _say(log_, f"name {entity_id}: {name!r} -> {got.name!r}")
    return results


def do_extract(
    items: list[dict[str, Any]],
    ext: extraction.Extractor,
    *,
    workers: int = 1,
    log_: Log | None = None,
) -> list[dict[str, Any]]:
    def one(it: dict[str, Any]) -> dict[str, Any]:
        doc = extraction.DocumentInput(
            it["doc_id"],
            it.get("title") or "",
            it.get("header") or "",
            it.get("text") or "",
            it.get("domains"),
        )
        try:
            result = ext.extract(doc)
            if not result.triples and result.usage.get("dropped_lines"):
                result = ext.extract(doc)
            _say(log_, f"extract doc {it['doc_id']}: {len(result.triples)} triples")
            return {
                "doc_id": it["doc_id"],
                "extraction": work.extraction_to_dict(result),
            }
        except models.ServerNotReady as exc:
            # the model server is loading or down: not the document's
            # fault, no error recorded against it; deferred, so the door
            # leaves it leased a while and hands out other work meanwhile
            _say(log_, f"extract doc {it['doc_id']}: not yet — {exc}")
            return {"doc_id": it["doc_id"], "defer": True}
        except Exception as exc:  # noqa: BLE001
            return {"doc_id": it["doc_id"], "error": f"{type(exc).__name__}: {exc}"}

    if workers > 1 and isinstance(ext, extraction.LocalExtractor):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return [r for r in pool.map(one, items) if r]
    return [r for r in (one(it) for it in items) if r]


def do_promote(
    door: Door,
    items: list[dict[str, Any]],
    ext: extraction.Extractor,
    spec: models.ModelSpec,
    *,
    log_: Log | None = None,
) -> list[dict[str, Any]]:
    """The expensive pass over flagged documents: a promoted image is
    first described again by the promote model (a Claude one; a server
    that cannot see leaves the reading as it is), the reading posted as a
    parse result so the document carries it, and the extraction reads
    that; then the extraction, as for the extract step."""
    from prax.parsers import vision

    for it in items:
        image = it.get("image")
        if not image or spec.kind != "claude":
            continue
        try:
            data = door.get_bytes(image["original"])
            text = vision.describe(
                data,
                filename=image.get("filename"),
                model=spec.model,
                previous=image.get("previous"),
            )
        except Exception as exc:  # noqa: BLE001 - the extraction still runs
            _say(log_, f"promote doc {it['doc_id']}: reading failed: {exc}")
            continue
        door.post_json(
            "/work/parse",
            {
                "results": [
                    {
                        "doc_id": it["doc_id"],
                        "extractor": f"vision/1+{spec.runtime_name}",
                        "text": text,
                        "force": True,
                    }
                ]
            },
        )
        it["text"] = text
        _say(log_, f"promote doc {it['doc_id']}: read again by {spec.runtime_name}")
    return do_extract(items, ext, workers=1, log_=log_)


def do_typing(
    items: list[dict[str, Any]], runtime: Any, *, log_: Log | None = None
) -> list[dict[str, Any]]:
    """Each handed-out batch to the typing model: the prompt rebuilt from
    the items and their documents, the answer posted as it came."""
    from prax import typing_pass

    results = []
    for it in items:
        b = typing_pass.batch_of(it)
        try:
            text, usage = runtime.chat(
                typing_pass.system_prompt(b.onto),
                typing_pass.user_prompt(b),
                max_tokens=40 * len(b.items) + 50,
            )
            results.append({**it, "text": text, "usage": usage})
            _say(log_, f"typing: {len(b.items)} items answered")
        except Exception as exc:  # noqa: BLE001
            results.append({**it, "error": f"{type(exc).__name__}: {exc}"})
    return results


def do_adjudicate(
    items: list[dict[str, Any]], spec: models.ModelSpec
) -> dict[str, Any]:
    """Ask the adjudicate step's model about the likely pairs the door
    handed out; the decisions go back with what they cost."""
    from prax import resolution

    if spec.kind == "claude":
        judge: Any = resolution.ClaudeAdjudicator(model=spec.model)
    else:
        judge = resolution.StubAdjudicator(threshold=1.0)  # a local model: later
    candidates = [
        resolution.Candidate(
            int(it["keep"]),
            int(it["drop"]),
            str(it.get("keep_name") or ""),
            str(it.get("drop_name") or ""),
            str(it.get("type") or ""),
            "likely",
            float(it.get("score") or 0.0),
        )
        for it in items
    ]
    same = judge.decide(candidates)
    out: dict[str, Any] = {"items": items, "same": same, "model": judge.name}
    usage = getattr(judge, "usage", None)
    if usage:
        out["usage"] = dict(usage)
        out["cost"] = round(judge.cost, 4)
    return out


def do_resolve(batch: dict[str, Any], emb: embeddings.Embedder) -> dict[str, Any]:
    """The likely tier of entity resolution for one type: embed the names
    the door handed out, find the close pairs, and post them."""
    from prax import resolution

    names = [(int(i), str(n)) for i, n in (batch.get("names") or [])]
    pairs = resolution.likely_pairs(
        names,
        emb,
        threshold=float(batch.get("threshold") or resolution.LIKELY_THRESHOLD),
    )
    return {
        "type": batch["type"],
        "model": emb.name,
        "pairs": [[a, b, round(s, 4)] for a, b, s in pairs],
    }


def do_embed(batch: dict[str, Any], emb: embeddings.Embedder) -> dict[str, Any]:
    chunks = batch.get("chunks") or []
    fields = batch.get("fields") or []
    out: dict[str, Any] = {"model": emb.name, "chunks": [], "fields": []}
    if chunks:
        vecs = emb.embed([c["text"] for c in chunks])
        out["chunks"] = [
            [c["chunk_id"], c["kind"], [float(x) for x in v]]
            for c, v in zip(chunks, vecs, strict=True)
        ]
    if fields:
        vecs = emb.embed([f["text"] for f in fields])
        out["fields"] = [
            [f["doc_id"], [float(x) for x in v]]
            for f, v in zip(fields, vecs, strict=True)
        ]
    return out


def _paid(spec: models.ModelSpec | None) -> bool:
    """Whether running this step costs money (``ModelSpec.paid``): a
    Claude model, or any model the file prices — an OpenAI-shaped
    endpoint at somebody else's API is paid too, and used not to be."""
    return spec is not None and spec.paid


def _say(logger: Log | None, text: str) -> None:
    if logger:
        logger(text)
    else:
        log.info(text)


# ------------------------------------------------------------------- pass


def run_once(
    door: Door,
    *,
    steps: tuple[str, ...] = STEPS,
    scope: str = "captures",
    limit: int = 10,
    workers: int = 3,
    session: int | None = None,
    spend: bool = False,
    log_: Log | None = None,
) -> dict[str, Any]:
    """One pass over the steps: fetch a batch, do it, post it. Returns what
    each step did; a step with nothing to do is absent. ``spend`` lets the
    promote step run its paid model; without it a paid step is refused
    before anything is fetched."""
    out: dict[str, Any] = {}
    for step in steps:
        if step == "embed":
            emb = embeddings.current()
            if emb is None:
                continue
            batch = door.get_json("/work/embed", {"limit": limit * 20, "scope": scope})
            if not (batch.get("chunks") or batch.get("fields")):
                continue
            if batch.get("model") != emb.name:
                out["embed"] = (
                    f"skipped: the door embeds with {batch.get('model')},"
                    f" this worker with {emb.name}"
                )
                continue
            if session:
                door.post_json(
                    f"/work/session/{session}",
                    {"note": f"embedding {len(batch['chunks'])} chunks"},
                )
            rep = door.post_json("/work/embed", do_embed(batch, emb))
            out["embed"] = f"{rep.get('applied', 0)} vectors"
            _say(log_, f"embed: {out['embed']}")
            continue
        if step == "resolve":
            emb = embeddings.current()
            if emb is None:
                continue
            batch = door.get_json("/work/resolve", {"limit": 1, "scope": scope})
            if not batch.get("type"):
                continue
            if batch.get("model") != emb.name:
                out["resolve"] = (
                    f"skipped: the door's names embed with {batch.get('model')},"
                    f" this worker with {emb.name}"
                )
                continue
            if session:
                door.post_json(
                    f"/work/session/{session}",
                    {"note": f"resolving {len(batch['names'])} {batch['type']} names"},
                )
            rep = door.post_json("/work/resolve", do_resolve(batch, emb))
            out["resolve"] = (
                f"{batch['type']}: {rep.get('applied', 0)} likely pairs"
                f" among {len(batch['names'])} names"
            )
            _say(log_, f"resolve: {out['resolve']}")
            continue
        if step == "promote":
            spec = models.resolve("promote")
            if spec is None:
                out["promote"] = "skipped: the promote step is not configured"
                continue
            if _paid(spec) and not spend:
                out["promote"] = (
                    f"skipped: the promote step is {spec.name} (paid); --spend runs it"
                )
                continue
        if step == "adjudicate":
            spec = models.resolve("adjudicate")
            if spec is None:
                continue  # the step is off on this host: nothing to say
            if _paid(spec) and not spend:
                out["adjudicate"] = (
                    f"skipped: the adjudicate step is {spec.name} (paid);"
                    " --spend runs it"
                )
                continue
            batch = door.get_json("/work/adjudicate", {"limit": limit * 20})
            items = batch.get("items") or []
            if not items:
                continue
            if session:
                door.post_json(
                    f"/work/session/{session}",
                    {"note": f"adjudicating {len(items)} likely pairs"},
                )
            rep = door.post_json("/work/adjudicate", do_adjudicate(items, spec))
            out["adjudicate"] = (
                f"{rep.get('applied', 0)} merged, {rep.get('declined', 0)} kept apart"
                f" of {len(items)} pairs"
            )
            _say(log_, f"adjudicate: {out['adjudicate']}")
            continue
        if step == "typing":
            spec = models.resolve("typing")
            if spec is None:
                continue  # the step is off on this host: nothing to say
            if _paid(spec):
                out["typing"] = f"skipped: the typing step is {spec.name} (paid)"
                continue
        batch = door.get_json(f"/work/{step}", {"limit": limit, "scope": scope})
        items = batch.get("items") or []
        if not items:
            continue
        if session:
            door.post_json(
                f"/work/session/{session}",
                {"note": f"{step}: {len(items)} items", "total": len(items), "done": 0},
            )
        if step == "parse":
            # each document's results are posted as it is done, under a
            # heartbeat: a batch of books lands one book at a time, and the
            # door neither reaps the session nor re-leases the book being read
            tally: dict[str, Any] = {"applied": 0, "actions": {}}
            ids = [int(it["doc_id"]) for it in items]
            with _Beating(door, session, "parse", ids) as beating:

                def landed(
                    rs: list[dict[str, Any]],
                    *,
                    tally: dict[str, Any] = tally,
                    beating: _Beating = beating,
                ) -> None:
                    rep = door.post_json("/work/parse", {"results": rs})
                    tally["applied"] += int(rep.get("applied", 0))
                    for k, v in (rep.get("actions") or {}).items():
                        tally["actions"][k] = tally["actions"].get(k, 0) + int(v)
                    beating.landed([int(r["doc_id"]) for r in rs])

                do_parse(door, items, log_=log_, landed=landed, spend=spend)
            out["parse"] = f"{tally['applied']} parsed {tally['actions'] or ''}"
        elif step == "titles":
            spec = models.resolve("titles")
            if _paid(spec):
                out["titles"] = f"skipped: the titles step is {spec.name} (paid)"
                continue
            runtime = models.runtime(spec) if spec else None
            results = do_titles(items, runtime, log_=log_)
            rep = door.post_json(
                "/work/titles",
                {"results": results, "run": f"titles-{time.strftime('%Y%m%dT%H%M%S')}"},
            )
            out["titles"] = (
                f"{rep.get('applied', 0)} retitled, {rep.get('skipped', 0)} left"
            )
        elif step == "summaries":
            spec = models.resolve("summaries")
            if _paid(spec):
                out["summaries"] = f"skipped: the summaries step is {spec.name} (paid)"
                continue
            runtime = models.runtime(spec) if spec else None
            results = do_summaries(items, runtime, log_=log_)
            rep = door.post_json(
                "/work/summaries",
                {
                    "results": results,
                    "run": f"summaries-{time.strftime('%Y%m%dT%H%M%S')}",
                },
            )
            out["summaries"] = (
                f"{rep.get('applied', 0)} translated, {rep.get('skipped', 0)} left"
            )
        elif step == "sections":
            spec = models.resolve("sections")
            if _paid(spec):
                out["sections"] = f"skipped: the sections step is {spec.name} (paid)"
                continue
            runtime = models.runtime(spec) if spec else None
            results = do_sections(items, runtime, log_=log_)
            rep = door.post_json(
                "/work/sections",
                {
                    "results": results,
                    "run": f"sections-{time.strftime('%Y%m%dT%H%M%S')}",
                },
            )
            wrote = sum(len(r.get("sections") or []) for r in results)
            out["sections"] = f"{rep.get('applied', 0)} documents, {wrote} sections"
        elif step == "vocabulary":
            spec = models.resolve("vocabulary")
            if _paid(spec):
                out["vocabulary"] = (
                    f"skipped: the vocabulary step is {spec.name} (paid)"
                )
                continue
            runtime = models.runtime(spec) if spec else None
            results = do_vocabulary(items, runtime, log_=log_)
            rep = door.post_json(
                "/work/vocabulary",
                {
                    "results": results,
                    "run": f"vocabulary-{time.strftime('%Y%m%dT%H%M%S')}",
                },
            )
            errors = rep.get("errors") or []
            out["vocabulary"] = (
                f"{rep.get('applied', 0)} named {rep.get('actions') or ''}"
                + (
                    f" · {len(errors)} refused: {errors[0]['error'][:60]}"
                    if errors
                    else ""
                )
            )
        elif step == "extract":
            spec = models.resolve("extract")
            if spec is None or _paid(spec):
                out["extract"] = (
                    "skipped: the extract step is "
                    f"{spec.name if spec else 'not configured'}"
                    + (" (paid)" if spec else "")
                )
                continue
            ext = extraction.current("extract")
            results = do_extract(items, ext, workers=workers, log_=log_)
            rep = door.post_json(
                "/work/extract",
                {
                    "results": results,
                    "extractor": ext.name,
                    "run": f"work-{time.strftime('%Y%m%dT%H%M%S')}",
                },
            )
            out["extract"] = f"{rep.get('applied', 0)} documents: {rep.get('report')}"
            for e in rep.get("errors") or []:
                out["extract"] += f"; doc {e['doc_id']} failed: {str(e['error'])[:160]}"
        elif step == "typing":
            spec = models.resolve("typing")
            assert spec is not None
            results = do_typing(items, models.runtime(spec), log_=log_)
            rep = door.post_json(
                "/work/typing",
                {
                    "results": results,
                    "model": spec.runtime_name,
                    "run": f"typing-model-{time.strftime('%Y%m%dT%H%M%S')}",
                },
            )
            r = rep.get("report") or {}
            out["typing"] = (
                f"{rep.get('applied', 0)} requests: linked {r.get('linked', 0)},"
                f" dropped {r.get('dropped', 0)}, misfit {r.get('misfit', 0)},"
                f" unanswered {r.get('unanswered', 0)}"
            )
        elif step == "promote":
            spec = models.resolve("promote")
            assert spec is not None
            ext = extraction.current("promote")
            results = do_promote(door, items, ext, spec, log_=log_)
            rep = door.post_json(
                "/work/promote",
                {
                    "results": results,
                    "extractor": ext.name,
                    "run": f"promote-{time.strftime('%Y%m%dT%H%M%S')}",
                },
            )
            out["promote"] = f"{rep.get('applied', 0)} documents: {rep.get('report')}"
            for e in rep.get("errors") or []:
                out["promote"] += f"; doc {e['doc_id']} failed: {str(e['error'])[:160]}"
        _say(log_, f"{step}: {out.get(step)}")
    return out


# ------------------------------------------------------------- drop folders


def push_folder(
    door: Door,
    folder: Path,
    *,
    domains: list[str] | None = None,
    log_: Log | None = None,
) -> dict[str, int]:
    """Upload the files of a local drop folder to the door with their
    sidecars, removing what was taken; what the door refused goes to
    ``failed/`` beside them."""
    counts = {"sent": 0, "failed": 0, "waiting": 0}
    if not folder.is_dir():
        return counts
    for item in drop.walk(folder):
        if item is None:
            counts["waiting"] += 1
            continue
        path, extra = item.path, item.extra
        rel = path.relative_to(folder).parts
        doms = (
            list(extra.get("domains") or [])
            or ([rel[0]] if len(rel) > 1 else [])
            or list(domains or [])
        )
        fields = {
            k: v
            for k, v in {
                "title": extra.get("title") or path.stem,
                "source_url": extra.get("source_url"),
                "domains": ",".join(doms) if doms else None,
                "tags": ",".join(extra.get("tags") or []) or None,
                "session": extra.get("session"),
                "by": extra.get("by") or "worker",
            }.items()
            if v
        }
        try:
            door.upload(path, fields)
        except Exception as exc:  # noqa: BLE001
            _say(log_, f"{path.name}: {exc}")
            counts["failed"] += 1
            drop.fail(folder, item)
            continue
        counts["sent"] += 1
        drop.taken(item)
    return counts


def watch(
    door: Door,
    *,
    interval: float = 20.0,
    steps: tuple[str, ...] = STEPS,
    scope: str = "captures",
    limit: int = 10,
    workers: int = 3,
    folders: list[Path] | None = None,
    domains: list[str] | None = None,
    spend: bool = False,
    log_: Log | None = None,
    once: bool = False,
    nightly: str | None = None,
    nightly_limit: int = 100,
) -> None:
    """Keep passing; announce a session job so the door's Jobs view shows
    this worker. With ``nightly`` (``"03:00"``) one bounded pass over the
    whole library follows the regular one once that hour has passed each
    day: the backlog and the stale texts, ``nightly_limit`` documents a
    step, the same steps."""
    from prax import schedule

    night = schedule.parse_hour(nightly) if nightly else None
    # the nightly pass is at its hour: a worker (re)started after it — prax
    # up brings one back after a crash, a person after a change — waits
    # for tomorrow's rather than running a pass over everything at noon
    last_night: datetime | None = datetime.now().astimezone() if night else None
    same, last_trouble = 0, ""  # a failure that repeats is not a bad pass
    session = None
    try:
        session = door.post_json(
            "/work/session",
            {
                "name": "worker",
                "host": door.name,
                "pid": os.getpid(),
                "note": ", ".join(steps),
            },
        )["job_id"]
    except Exception as exc:  # noqa: BLE001
        _say(log_, f"no session job: {exc}")
    try:
        while True:
            for folder in folders or []:
                c = push_folder(door, folder, domains=domains, log_=log_)
                if c["sent"] or c["failed"]:
                    _say(log_, f"{folder}: {c}")
            try:
                done = run_once(
                    door,
                    steps=steps,
                    scope=scope,
                    limit=limit,
                    workers=workers,
                    session=session,
                    spend=spend,
                    log_=log_,
                )
                now = datetime.now().astimezone()
                if night and schedule.due(night, now, last_night):
                    last_night = now
                    _say(
                        log_,
                        f"the nightly pass: {nightly_limit} a step over everything",
                    )
                    done = run_once(
                        door,
                        steps=steps,
                        scope="all",
                        limit=nightly_limit,
                        workers=workers,
                        session=session,
                        spend=spend,
                        log_=log_,
                    )
                if session:
                    door.post_json(
                        f"/work/session/{session}",
                        {
                            "note": f"last pass {time.strftime('%H:%M:%S')}:"
                            f" {json.dumps(done)[:120] if done else 'nothing waiting'}"
                            + (f" · {mb} MB" if (mb := hostinfo.process_mb()) else "")
                        },
                    )
            except Exception as exc:  # a bad pass is outlived; a repeated one is not
                trouble = f"{type(exc).__name__}: {exc}"
                _say(log_, f"pass failed: {trouble}")
                # a bad pass is worth outliving; the same bad pass over and
                # over is not. A worker started before a change to
                # prax.yaml or to the code fails identically for ever —
                # 519 passes in three hours on 2026-09-24, doing no work
                # and growing by 350 MB an hour. Exiting is the repair:
                # `prax up` restarts the role, and the new process reads
                # the new configuration with the new code.
                same = same + 1 if trouble == last_trouble else 1
                last_trouble = trouble
                if not once and same >= GIVE_UP_AFTER:
                    _say(
                        log_,
                        f"the same failure {same} times: stopping so the"
                        " supervisor starts a worker that has read the"
                        " current configuration and code",
                    )
                    raise SystemExit(1) from exc
            else:
                same, last_trouble = 0, ""
            if once:
                return
            time.sleep(interval)
    finally:
        if session:
            try:
                door.post_json(
                    f"/work/session/{session}", {"status": "done", "note": "stopped"}
                )
            except Exception as exc:  # noqa: BLE001
                _say(log_, f"could not close the session job: {exc}")
