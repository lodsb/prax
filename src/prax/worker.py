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
import shutil
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from prax import embeddings, extraction, hostinfo, inbox, models, parsers, titles, work
from prax.client import Door

log = logging.getLogger("prax.worker")
Log = Callable[[str], None]
STEPS = ("parse", "titles", "extract", "embed")


# ------------------------------------------------------------------ steps


def _requested(it: dict[str, Any]) -> tuple[list[Any], str | None]:
    """The extractors to try for an item: a requested reading names one
    (and is refused when its model would cost money, so nobody's click
    spends unasked — the request stays on the document with that
    reason); otherwise the candidates for the type."""
    name = it.get("extractor")
    if not name:
        return parsers.candidates(it.get("mime") or ""), None
    try:
        ext = parsers.by_name(name)
    except KeyError as exc:
        return [], str(exc)
    if name.startswith("vision") and _paid(models.resolve("vision")):
        spec = models.resolve("vision")
        return [], (
            f"the vision step is {spec.name if spec else 'none'} (paid): run"
            " parse_pending.py yourself, or point the step at a local server"
        )
    if not ext.available():
        return [], f"{name} is not installed on this worker"
    return [ext], None


def do_parse(
    door: Door, items: list[dict[str, Any]], *, log_: Log | None = None
) -> list[dict[str, Any]]:
    results = []
    for it in items:
        doc_id = it["doc_id"]
        exts, refused = _requested(it)
        if not exts:
            results.append(
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
            results.append(
                {"doc_id": doc_id, "extractor": exts[0].stamp, "error": f"fetch: {exc}"}
            )
            continue
        last = None
        for ext in exts:
            t0 = time.monotonic()
            try:
                with _mode(ext.name, it.get("mode")):
                    stamp = ext.stamp
                    text = ext(
                        data, filename=it.get("filename"), previous=it.get("previous")
                    ).strip()
            except Exception as exc:  # noqa: BLE001
                last = (ext.stamp, f"{type(exc).__name__}: {exc}")
                if ext is not exts[-1]:
                    # the chain goes on; the door records this attempt too,
                    # so it can see the chain was run and not hand the
                    # document out again (a scan refused by the first
                    # extractor, empty for the fallback, came back every
                    # cycle otherwise)
                    results.append(
                        {"doc_id": doc_id, "extractor": ext.stamp, "error": last[1]}
                    )
                continue
            results.append(
                {
                    "doc_id": doc_id,
                    "extractor": stamp,
                    "text": text,
                    "seconds": round(time.monotonic() - t0, 2),
                    "force": bool(it.get("force")),
                    "requested": it.get("extractor"),
                    "keep_source": ext.annotates,
                }
            )
            _say(log_, f"parse doc {doc_id}: {len(text)} chars ({stamp})")
            break
        else:
            results.append(
                {
                    "doc_id": doc_id,
                    "extractor": last[0] if last else exts[0].stamp,
                    "error": last[1] if last else "failed",
                    "requested": it.get("extractor"),
                }
            )
    return results


_MODE_SETTINGS = {"vision-pages": "PRAX_VISION_PAGES", "figures": "PRAX_FIGURES"}


@contextlib.contextmanager
def _mode(extractor: str, mode: str | None) -> Iterator[None]:
    """The requested mode as the extractor's setting for one call
    (``vision-pages``: every page or the scans; ``figures``: every image
    or the captioned ones)."""
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
        guess = titles.guess_title(
            runtime,
            it["text"],
            filename=it.get("filename"),
            heading=titles.first_heading(it["text"]),
            pdf_title=None,
        )
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
        except Exception as exc:  # noqa: BLE001
            return {"doc_id": it["doc_id"], "error": f"{type(exc).__name__}: {exc}"}

    if workers > 1 and isinstance(ext, extraction.LocalExtractor):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, items))
    return [one(it) for it in items]


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
    return spec is not None and spec.kind == "claude"


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
    log_: Log | None = None,
) -> dict[str, Any]:
    """One pass over the steps: fetch a batch, do it, post it. Returns what
    each step did; a step with nothing to do is absent."""
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
            results = do_parse(door, items, log_=log_)
            rep = door.post_json("/work/parse", {"results": results})
            out["parse"] = f"{rep.get('applied', 0)} parsed {rep.get('actions') or ''}"
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
    failed_dir = folder / inbox.FAILED_DIR
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        if failed_dir in path.parents or not path.exists():
            continue
        if path.name.startswith(".") or path.suffix.lower() in inbox.SKIP_SUFFIXES:
            continue
        if path.suffix == ".json" and inbox._is_sidecar_name(path):
            if not path.with_name(path.name[:-5]).exists():
                counts["waiting"] += 1
            continue
        if not inbox._settled(path):
            counts["waiting"] += 1
            continue
        side, extra = inbox._sidecar(path)
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
            failed_dir.mkdir(exist_ok=True)
            shutil.move(str(path), failed_dir / path.name)
            if side:
                shutil.move(str(side), failed_dir / side.name)
            continue
        counts["sent"] += 1
        path.unlink()
        if side:
            side.unlink()
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
    log_: Log | None = None,
    once: bool = False,
) -> None:
    """Keep passing; announce a session job so the door's Jobs view shows
    this worker."""
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
            except Exception as exc:  # noqa: BLE001 - the worker outlives a bad pass
                _say(log_, f"pass failed: {type(exc).__name__}: {exc}")
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
