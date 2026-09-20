"""FastAPI app — the HTTP door. Thin wrappers over prax.store.

The store connection is opened in the app lifespan and shared by all
handlers; prax.store serializes access.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import socket
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import anyio
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ask as ask_mod
from . import (
    auth,
    config,
    embeddings,
    hostinfo,
    inbox,
    models,
    ontology,
    review,
    schedule,
    store,
    work,
)

UI_DIR = Path(__file__).resolve().parent / "ui"


def _con(request: Request) -> Any:
    """The connection for this request's thread (reads in parallel, writes
    one at a time behind the store's lock)."""
    return store.thread_connection()


def _scan_inbox(app: FastAPI, stop: threading.Event, every: float) -> None:
    """The door consumes its own drop folder (``data/inbox/``): what lands
    there is registered through the store without any other process.
    Parsing and the model passes are a worker's business (``prax.worker``)."""
    con = store.connect()
    try:
        while not stop.wait(every):
            try:
                rep = inbox.scan(con, inbox.inbox_dir())
                if rep.registered or rep.failed:
                    logging.getLogger("prax.inbox").info("drop folder: %s", rep)
                if store.job_reap(con):
                    logging.getLogger("prax.jobs").info(
                        "closed jobs whose process is gone"
                    )
            except Exception:
                logging.getLogger("prax.inbox").exception("drop folder scan failed")
    finally:
        con.close()


def _clock(stop: threading.Event, every: float) -> None:
    """The door's clock (``schedule:``): maintain and backup at their
    hours, as the jobs their endpoints start; the jobs table remembers
    what ran, so a restart does not run the night again."""
    con = store.connect()
    try:
        while not stop.wait(every):
            try:
                schedule.tick(
                    con,
                    {
                        "maintain": lambda o: _start_maintain(con, o.get("only")),
                        "backup": lambda o: _start_backup(
                            con, o.get("dest"), archive=o.get("archive", True)
                        ),
                    },
                )
            except Exception:
                logging.getLogger("prax.schedule").exception("the clock failed")
    finally:
        con.close()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    con = store.connect()
    store.init_db(con)
    app.state.con = con  # the main connection: migrations, the change stamp
    app.state.con_lock = threading.Lock()  # one thread on it at a time
    store.job_reap(con)  # sessions left behind by a killed door or worker
    stop = threading.Event()
    every = config.number("door.inbox_scan_seconds", "PRAX_INBOX_SCAN", 20.0)
    scanner = None
    if every > 0:
        scanner = threading.Thread(
            target=_scan_inbox, args=(app, stop, every), name="prax-inbox", daemon=True
        )
        scanner.start()
    clock = None
    if schedule.entries():
        tick = config.number("door.clock_seconds", "PRAX_CLOCK", schedule.TICK)
        clock = threading.Thread(
            target=_clock, args=(stop, tick), name="prax-clock", daemon=True
        )
        clock.start()
    try:
        yield
    finally:
        stop.set()
        for thread in (scanner, clock):
            if thread is not None:
                thread.join(timeout=5)
        con.close()


app = FastAPI(title="prax", version="0.0.1", lifespan=_lifespan)
app.middleware("http")(auth.middleware)
app.state.writes = 0


SLOW_SECONDS = 2.0  # a request slower than this is logged with what else was on
app.state.in_flight = 0


@app.middleware("http")
async def _count_writes(request: Request, call_next: Any) -> Any:
    """Every mutating request bumps a counter: with SQLite's data_version
    (other writers) it makes the change stamp the UI polls. A slow
    request is logged with how many others were in flight and which jobs
    ran, so "loading takes ages" leaves a trace of what the door was
    doing at the time (a GET of one document took 77 s once, on
    2026-09-17, with three book-sized readings being taken in)."""
    state = request.app.state
    state.in_flight += 1
    others = state.in_flight - 1
    started = time.monotonic()
    try:
        response = await call_next(request)
    finally:
        state.in_flight -= 1
    seconds = time.monotonic() - started
    if request.method not in ("GET", "HEAD", "OPTIONS") and response.status_code < 400:
        state.writes += 1
    if seconds >= SLOW_SECONDS and not request.url.path.startswith("/ask"):
        jobs = ""
        with contextlib.suppress(Exception):
            running = store.list_jobs(store.thread_connection(), limit=1)["running"]
            jobs = ", ".join(j["name"] for j in running)
        logging.getLogger("prax.door").warning(
            "slow: %s %s took %.1f s (%d other requests in flight%s)",
            request.method,
            request.url.path,
            seconds,
            others,
            f"; jobs: {jobs}" if jobs else "",
        )
    return response


# A browser extension calls the door from its own origin
# (chrome-extension://…, moz-extension://…). Every extension origin is
# answered: the token is what gates the door, not the origin, and a
# browser that withholds its host permission (Firefox until granted,
# Chrome in some contexts) then runs the request through CORS.
# door.cors_origins in prax.yaml (or PRAX_CORS_ORIGINS, comma-separated)
# adds web origins; the UI itself is same-origin.
from fastapi.middleware.cors import CORSMiddleware

EXTENSION_ORIGINS = r"^(chrome|moz|safari-web)-extension://.+$"
_cors = [o for o in config.words("door.cors_origins", "PRAX_CORS_ORIGINS")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors,
    allow_origin_regex=EXTENSION_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    # Chrome's private network access: a request from an extension (or a
    # public page) into a private address asks in its preflight whether
    # the target consents; a door on the private network does
    allow_private_network=True,
)


class SessionReq(BaseModel):
    token: str


@app.get("/health", include_in_schema=False)
def health() -> dict[str, Any]:
    return {"ok": True, "auth": "token" if auth.token() else "loopback-only"}


@app.post("/session")
def session(req: SessionReq, request: Request) -> JSONResponse:
    """Exchange the bearer token for the session cookie the UI uses."""
    if auth.token() is None:
        if not auth.is_loopback(request):
            raise HTTPException(401, "no token configured")
        return JSONResponse({"ok": True, "cookie": False})
    if not auth.valid(req.token):
        raise HTTPException(401, "invalid token")
    response = JSONResponse({"ok": True, "cookie": True})
    auth.session_cookie(response, req.token, secure=request.url.scheme == "https")
    return response


@app.delete("/session")
def end_session() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


class IngestText(BaseModel):
    text: str
    title: str | None = None
    source_url: str | None = None
    meta: dict[str, Any] | None = None
    domains: list[str] | None = None


class LinkReq(BaseModel):
    src: str
    src_type: str
    rel: str
    dst: str
    dst_type: str
    confidence: str = "EXTRACTED"
    source_doc: int | None = None
    ontology_version: str | None = None
    producer: str = "manual"  # "agent" from the MCP proxy


@app.post("/ingest")
def ingest(req: IngestText, request: Request) -> dict[str, Any]:
    result = store.ingest_text(
        _con(request),
        req.text,
        title=req.title,
        source_url=req.source_url,
        meta=req.meta,
    )
    if req.domains:
        try:
            for d in req.domains:
                store.add_domain(_con(request), result["doc_id"], d)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return result


def _split(csv: str | None) -> list[str] | None:
    items = [s.strip() for s in (csv or "").split(",") if s.strip()]
    return items or None


def _capture_out(cap: inbox.Capture) -> dict[str, Any]:
    return {
        "doc_id": cap.doc_id,
        "created": cap.created,
        "indexed": cap.indexed,
        "domains": cap.domains,
        "previous_capture": cap.previous,
        "mime": cap.mime,
        "duplicate_of": cap.duplicate_of,
        "replaced": cap.replaced,
    }


class RetireReq(BaseModel):
    reason: str = "retired by hand"
    duplicate_of: int | None = None


@app.post("/doc/{doc_id}/retire")
def retire(doc_id: int, req: RetireReq, request: Request) -> dict[str, Any]:
    """Take a document out of search and the graph; row and bytes stay."""
    try:
        return store.retire_document(
            _con(request),
            doc_id,
            reason=req.reason,
            duplicate_of=req.duplicate_of,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/doc/{doc_id}/retire")
def unretire(doc_id: int, request: Request) -> dict[str, Any]:
    try:
        return store.unretire_document(_con(request), doc_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/captures")
def captures(url: str, request: Request) -> list[dict[str, Any]]:
    """The live captures of one URL (canonicalised here), oldest first:
    what an importer asks before fetching a link it may already hold."""
    try:
        inbox.check_url(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return store.live_captures_of(_con(request), inbox.canonical_url(url))


@app.post("/inbox/dedupe")
def dedupe(request: Request, commit: bool = False) -> dict[str, Any]:
    """Retire the duplicate captures of each URL (dry run unless commit)."""
    return store.dedupe_captures(_con(request), commit=commit)


@app.post("/ingest/file")
def ingest_file(
    request: Request,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    source_url: Annotated[str | None, Form()] = None,
    domains: Annotated[str | None, Form()] = None,
    tags: Annotated[str | None, Form()] = None,
    session: Annotated[str | None, Form()] = None,
    by: Annotated[str | None, Form()] = None,
    paper: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Upload a file: archived at once, text and HTML indexed at once,
    anything else parsed by the batch host. ``domains`` and ``tags`` are
    comma-separated; ``by`` says what sent it (the extension, a script);
    ``paper`` is JSON — what the sender read off the page the file came
    from (doi, arxiv, authors, journal, date, pdf_url)."""
    paper_info = None
    if paper:
        try:
            paper_info = json.loads(paper)
        except ValueError as exc:
            raise HTTPException(400, f"paper must be JSON: {exc}") from exc
    try:
        cap = inbox.ingest_upload(
            _con(request),
            file.file.read(),
            filename=file.filename,
            mime=file.content_type,
            title=title,
            source_url=source_url,
            domains=_split(domains),
            tags=_split(tags),
            session=session,
            by=by or "upload",
            paper=paper_info if isinstance(paper_info, dict) else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _capture_out(cap)


class IngestHtml(BaseModel):
    url: str
    html: str
    title: str | None = None
    domains: list[str] | None = None
    tags: list[str] | None = None
    session: str | None = None
    mode: str | None = None  # "snapshot" (self-contained), "dom", or "video"
    note: str | None = None
    video: dict[str, Any] | None = None  # a video capture: provider, id, url, chapters…
    paper: dict[str, Any] | None = (
        None  # what the page says of a paper: doi, arxiv, authors…
    )


class IngestUrl(BaseModel):
    url: str
    title: str | None = None
    domains: list[str] | None = None
    tags: list[str] | None = None
    session: str | None = None
    by: str | None = "url"  # "agent" from the MCP proxy, "import:…" from an importer
    note: str | None = None  # what the sender said about it


@app.post("/ingest/html")
def ingest_html(req: IngestHtml, request: Request) -> dict[str, Any]:
    """A page as the browser rendered it (the extension): archived and
    indexed through trafilatura at once."""
    try:
        cap = inbox.ingest_html(
            _con(request),
            req.html,
            url=req.url,
            title=req.title,
            domains=req.domains,
            tags=req.tags,
            session=req.session,
            mode=req.mode,
            note=req.note,
            video=req.video,
            paper=req.paper,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _capture_out(cap)


@app.post("/ingest/url")
def ingest_url(req: IngestUrl, request: Request) -> dict[str, Any]:
    """Fetch a URL server-side and keep what came back."""
    try:
        cap = inbox.ingest_url(
            _con(request),
            req.url,
            title=req.title,
            domains=req.domains,
            tags=req.tags,
            session=req.session,
            by=req.by,
            note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:  # urllib errors: unreachable, 403, 404, timeout
        why = str(exc)
        if "403" in why or "401" in why:
            why += (
                " (the site refused the server; the browser extension fetches"
                " with your own session when it may read all sites)"
            )
        raise HTTPException(502, f"fetch failed: {why}") from exc
    return _capture_out(cap)


# ------------------------------------------------------------------ work
# The door hands model work out and takes results in (prax.work), so a
# worker on this machine or another does the parsing, titling, extraction
# and embedding without opening the database.


def _worker(request: Request) -> str:
    return request.headers.get("x-prax-worker") or (
        request.client.host if request.client else "worker"
    )


# the session routes come before the {step} routes: FastAPI matches in order
class WorkSessionReq(BaseModel):
    name: str = "worker"
    host: str | None = None
    pid: int | None = None
    note: str | None = None


class SessionBeat(BaseModel):
    done: int | None = None
    total: int | None = None
    note: str | None = None
    status: str | None = None  # "done" or "failed" ends the session
    renew: dict[str, Any] | None = None  # {"step", "items"}: the leases still held


@app.post("/work/session")
def work_session(req: WorkSessionReq, request: Request) -> dict[str, Any]:
    """A worker announces itself: a job row the Jobs view shows, with the
    worker's heartbeats."""
    job_id = store.job_start(
        _con(request), req.name, note=req.note, host=req.host, pid=req.pid or 0
    )
    return {"job_id": job_id, "leases": work.leases()}


@app.post("/work/session/{job_id}")
def work_beat(job_id: int, req: SessionBeat, request: Request) -> dict[str, Any]:
    con = _con(request)
    if req.status in ("done", "failed"):
        store.job_finish(con, job_id, status=req.status, note=req.note)
    else:
        store.job_update(con, job_id, done=req.done, total=req.total, note=req.note)
    renewed = 0
    if req.renew and req.renew.get("items"):
        renewed = work.renew(
            str(req.renew.get("step") or ""),
            [int(i) for i in req.renew["items"]],
            _worker(request),
        )
    return {"ok": True, "renewed": renewed}


@app.get("/work/{step}")
def work_out(
    step: str, request: Request, limit: int = 10, scope: str = "captures"
) -> dict[str, Any]:
    """A leased batch of work: parse, titles, extract, embed, or the
    names of an entity type to resolve."""
    try:
        return work.hand_out(
            _con(request),
            step,
            limit=limit,
            scope=scope,
            worker=_worker(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class WorkIn(BaseModel):
    results: list[dict[str, Any]] | None = None
    extractor: str | None = None
    run: str | None = None
    model: str | None = None
    chunks: list[list[Any]] | None = None
    fields: list[list[Any]] | None = None
    type: str | None = None  # resolve: the entity type the pairs are of
    pairs: list[list[Any]] | None = None  # resolve: [a, b, cosine]
    items: list[dict[str, Any]] | None = None  # adjudicate: the pairs handed out
    same: list[bool] | None = None  # adjudicate: one decision per item


@app.post("/work/{step}")
def work_in(step: str, req: WorkIn, request: Request) -> dict[str, Any]:
    """The results of a batch, applied by the door."""
    try:
        return work.take_in(
            _con(request),
            step,
            req.model_dump(exclude_none=True),
            worker=_worker(request),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/vectors/merge")
def vectors_merge() -> dict[str, Any]:
    """Fold the delta indexes into the main files now."""
    emb = embeddings.current()
    if emb is None or not store.vectors_available():
        raise HTTPException(400, "no embedder or no usearch")
    return store.merge_vectors(emb.name)


@app.get("/changes")
def changes(request: Request) -> dict[str, Any]:
    """A stamp that changes when the store changed (this door's writes or
    another process's commits) and how many jobs are running: the UI polls
    it and re-renders a listing when the stamp moved."""
    con = request.app.state.con  # one fixed connection: its data_version moves
    # when any other connection commits, this door's threads included; a
    # connection is one thread's at a time (reads no longer take the
    # store's lock, which used to serialize these two by the way)
    with request.app.state.con_lock:
        return {
            "stamp": f"{store.data_version(con)}-{request.app.state.writes}",
            "jobs": store.running_jobs(con),
        }


class HealReq(BaseModel):
    checks: list[str] | None = None  # None: every ailment that can be repaired


@app.get("/heal")
def heal_findings(
    request: Request, check: str | None = None, examples: int = 6
) -> dict[str, Any]:
    """What is wrong with the store: each ailment, how many rows it finds
    and a few to look at. Reads only; `POST /heal` is what repairs."""
    try:
        return store.health(_con(request), only=_split(check), examples=examples)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/heal")
def heal_apply(req: HealReq, request: Request) -> dict[str, Any]:
    """Repair what the named ailments find (every repairable one when none
    are named). Edges are invalidated, never deleted; the pass is a job."""
    try:
        return store.heal(_con(request), only=req.checks)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class MaintainReq(BaseModel):
    only: list[str] | None = None  # store.PASSES; None: every pass


@app.post("/maintain")
def maintain_start(req: MaintainReq, request: Request) -> dict[str, Any]:
    """The maintenance pass: the acronyms table, the document retrieval
    fields, the domain rules over documents without a set, the duplicate
    captures — what the store does to itself without a model or a
    decision. A job on a thread of its own; poll ``GET /jobs/{id}``."""
    chosen = [p for p in (req.only or []) if p] or list(store.PASSES)
    unknown = [p for p in chosen if p not in store.PASSES + store.ON_REQUEST]
    if unknown:
        raise HTTPException(
            400,
            f"no such pass: {unknown}; passes are {store.PASSES},"
            f" on request {store.ON_REQUEST}",
        )
    return _start_maintain(_con(request), chosen)


def _start_maintain(con: Any, only: list[str] | None) -> dict[str, Any]:
    chosen = [p for p in (only or []) if p] or list(store.PASSES)
    job = store.Job(con, "maintain", note=", ".join(chosen))

    def run() -> None:
        con = store.connect()
        try:
            with store.Job.existing(con, job.id) as mine:
                store.maintain(con, only=chosen, job=mine)
        except Exception:  # the job row carries the error
            logging.getLogger("prax.maintain").exception("maintenance failed")
        finally:
            con.close()

    threading.Thread(target=run, name="maintain", daemon=True).start()
    return {"job": job.id, "passes": chosen}


class ResolveReq(BaseModel):
    apply: bool = False  # False: the plan only
    type: str | None = None  # one entity type
    twins: bool = False  # merge a concept into the method of the same name
    likely: bool = True  # the likely tier: the pairs a worker left
    show: int = 40  # candidates per tier in the plan


@app.post("/graph/resolve")
def resolve_entities(req: ResolveReq, request: Request) -> dict[str, Any]:
    """Entity resolution (``prax.resolution``): the plan — sure candidates
    (equal after normalization, an initials form of one author name),
    concept/method twins, likely ones (close by name embedding, computed
    by a worker through the resolve step and kept until decided) — and,
    with ``apply``, a job that merges the sure ones (and the twins when
    asked); the likely ones are a person's decision, or an adjudicator's,
    and stay in the plan. Merges are pointers (``entities.canonical_id``):
    nothing is deleted."""
    from prax import resolution

    con = _con(request)
    plan = resolution.plan(con, etype=req.type, likely=req.likely)
    tiers = {
        tier: {
            "count": len(items),
            "examples": [
                {
                    "type": c.type,
                    "score": round(c.score, 2),
                    "drop": c.drop_name,
                    "keep": c.keep_name,
                }
                for c in items[: req.show]
            ],
        }
        for tier, items in (
            ("sure", plan.sure),
            ("twins", plan.twins),
            ("likely", plan.likely),
        )
    }
    tiers["likely"]["computed"] = store.candidate_runs(con)
    if not req.apply:
        return {"plan": tiers, "applied": False}
    job = store.Job(
        con,
        "resolve",
        total=len(plan.sure) + (len(plan.twins) if req.twins else 0),
        note=f"{len(plan.sure)} sure"
        + (f", {len(plan.twins)} twins" if req.twins else ""),
    )

    def run() -> None:
        own = store.connect()
        try:
            with store.Job.existing(own, job.id) as mine:
                rep = resolution.apply(own, plan, twins=req.twins)
                mine.note(
                    f"done: merged {rep.merged_sure} sure, {rep.merged_twins} twins;"
                    f" {len(plan.likely)} likely left for a person"
                )
        except Exception:
            logging.getLogger("prax.resolve").exception("resolution failed")
        finally:
            own.close()

    threading.Thread(target=run, name="resolve", daemon=True).start()
    return {"plan": tiers, "applied": True, "job": job.id}


@app.post("/import/zotero/item")
async def import_zotero_item(
    request: Request,
    item: Annotated[str, Form()],
    file: Annotated[UploadFile | None, File()] = None,
    cache_text: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """One planned Zotero document (``importers.zotero.Planned.to_wire``)
    with its attachment's bytes and Zotero's cached text for it: the
    door writes it as the importer would — created, merged into the
    document that already holds the bytes, refreshed when the record
    changed, skipped when it did not, or missing. The client plans over
    a copy of ``zotero.sqlite`` (``prax import zotero``); the door never
    sees the library."""
    from prax.importers import zotero

    try:
        planned = zotero.Planned.from_wire(json.loads(item))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, f"not a planned item: {exc}") from exc
    data = await file.read() if file is not None else None
    if planned.path is not None and data is None and not planned.missing:
        raise HTTPException(400, "an attachment needs its file")
    con = _con(request)
    report = zotero.Report()
    action = zotero.apply(
        con,
        planned,
        zotero.KnownKeys(con),
        version=ontology.current().version,
        report=report,
        data=data,
        text=cache_text,
    )
    return {"action": action, "edges": report.edges, "key": planned.key}


class CitationsReq(BaseModel):
    source: str = "openalex"  # or crossref
    ids: list[int] | None = None
    limit: int | None = None
    resolve_titles: bool = False  # documents without a DOI too, by exact title
    refresh: bool = False  # fetched ones again
    dry_run: bool = False  # the selection's size only


@app.post("/import/citations")
def import_citations(req: CitationsReq, request: Request) -> dict[str, Any]:
    """The citation network from OpenAlex or Crossref (``prax.importers
    .citations``): ``cites`` edges and citation counts for the documents
    not looked up yet, those with a DOI first. The door fetches — it has
    the DOIs and, with ``citations.mailto`` in prax.yaml, the polite pool.
    A job; ``dry_run`` only counts."""
    from prax.importers import citations

    if req.source not in ("openalex", "crossref"):
        raise HTTPException(400, "source must be openalex or crossref")
    con = _con(request)
    ids = req.ids or citations.candidates(
        con, limit=req.limit, refresh=req.refresh, doi_only=not req.resolve_titles
    )
    if req.dry_run:
        return {"selected": len(ids), "source": req.source, "dry_run": True}
    job = store.Job(con, "citations", total=len(ids), note=req.source)

    def run() -> None:
        own = store.connect()
        try:
            with store.Job.existing(own, job.id) as mine:
                fetch = citations.HttpFetcher()
                source = citations.source_named(req.source, fetch)
                rep = citations.Report()
                step = 25
                for start in range(0, len(ids), step):
                    citations.import_citations(
                        own,
                        ids[start : start + step],
                        source=source,
                        resolve_titles=req.resolve_titles,
                        report=rep,
                    )
                    mine.update(
                        done=min(start + step, len(ids)),
                        note=f"{rep.documents} resolved, {rep.linked} edges,"
                        f" {len(rep.errors)} errors, {fetch.calls} requests",
                    )
                mine.note(
                    f"done: {rep.documents} resolved, {rep.unresolved} unresolved,"
                    f" {rep.linked} cites edges, {rep.existing} existing,"
                    f" {rep.library_refs} to library documents,"
                    f" {len(rep.errors)} errors, {fetch.calls} requests"
                )
        except Exception:
            logging.getLogger("prax.citations").exception("citations failed")
        finally:
            own.close()

    threading.Thread(target=run, name="citations", daemon=True).start()
    return {"selected": len(ids), "source": req.source, "dry_run": False, "job": job.id}


class BackupReq(BaseModel):
    dest: str | None = None  # None: the paths.backup setting
    archive: bool = True  # False: the database, indexes and config only


@app.post("/backup")
def backup_start(req: BackupReq, request: Request) -> dict[str, Any]:
    """Copy the store to a directory on this host (``dest``, or the
    ``paths.backup`` setting): the database as a consistent snapshot, the
    vector indexes, the config, and the archive files the copy lacks
    (``archive: false`` leaves the originals out: what cannot be rebuilt,
    for a disk too small for them). The copy runs on a thread of its own
    and is a job; poll ``GET /jobs/{id}`` for its progress and its last
    note."""
    try:
        return _start_backup(_con(request), req.dest, archive=req.archive)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _start_backup(con: Any, dest: str | None, *, archive: bool) -> dict[str, Any]:
    dest = store.backup_target(dest)
    job = store.Job(con, "backup", note=str(dest))

    def run() -> None:
        con = store.connect()
        try:
            with store.Job.existing(con, job.id) as mine:
                store.backup(con, dest, archive=archive, job=mine)
        except Exception:  # the job row carries the error
            logging.getLogger("prax.backup").exception("backup to %s failed", dest)
        finally:
            con.close()

    threading.Thread(target=run, name="backup", daemon=True).start()
    return {"job": job.id, "dest": str(dest)}


@app.get("/stats")
def stats(request: Request) -> dict[str, Any]:
    """What the store holds: documents, chunks, vectors, the graph, the
    review queue, the ontology (`prax status`)."""
    return store.stats(_con(request))


@app.get("/jobs")
def jobs(request: Request, limit: int = 20) -> dict[str, Any]:
    """What runs and what ran lately, and what this door's host has left
    (free RAM, commit headroom) so a wall is visible before it is hit."""
    out = store.list_jobs(_con(request), limit=limit)
    out["host"] = {"name": socket.gethostname(), **hostinfo.memory()}
    return out


@app.get("/models/servers")
def model_servers() -> dict[str, Any]:
    """The model servers ``prax.yaml`` names (``openai`` models) and what
    each says about itself: reachable, model, slots, vision, and its load
    when it was started with ``--metrics`` (the Jobs page shows this)."""
    return {"servers": [models.server_status(s) for s in models.servers()]}


@app.get("/jobs/{job_id}")
def job(job_id: int, request: Request) -> dict[str, Any]:
    row = store.get_job(_con(request), job_id)
    if row is None:
        raise HTTPException(404, f"no job {job_id}")
    return row


@app.post("/vectors/release")
def vectors_release() -> dict[str, Any]:
    """Drop the door's memory-mapped index views so a batch job on this
    machine can replace the files; they reopen on the next query."""
    return {"released": store.release_vector_views()}


@app.get("/inbox")
def inbox_view(request: Request, limit: int = 50) -> dict[str, Any]:
    """The latest captures (uploads, sent pages, fetched URLs, dropped
    files) with their state, the drop folder, and the domains to choose."""
    return {
        "recent": inbox.recent(_con(request), limit=limit),
        "inbox_dir": str(inbox.inbox_dir()),
        "modules": sorted(m for m in ontology.current().modules if m != ontology.CORE),
    }


@app.get("/get/{doc_id}")
def get(
    doc_id: int, request: Request, offset: int = 0, max_chars: int | None = None
) -> dict[str, Any]:
    doc = store.get_document(_con(request), doc_id, offset=offset, max_chars=max_chars)
    if doc is None:
        raise HTTPException(404, "no such document")
    return doc


@app.get("/search")
def search(
    q: str,
    request: Request,
    limit: int = 10,
    kind: str | None = None,
    mode: str = "hybrid",
    rerank: bool | None = None,
    doctype: str | None = None,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    try:
        return store.search(
            _con(request),
            q,
            limit,
            kind=kind,
            mode=mode,
            rerank=rerank,
            doctype=doctype,
            domain=domain,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/chunk/{chunk_id}")
def chunk(chunk_id: int, request: Request) -> dict[str, Any]:
    """One chunk in full, with its kind, heading path, locator and table data."""
    c = store.get_chunk(_con(request), chunk_id)
    if c is None:
        raise HTTPException(404, "no such chunk")
    return c


@app.post("/link")
def link(req: LinkReq, request: Request) -> dict[str, int]:
    edge = store.Edge(req.src, req.src_type, req.rel, req.dst, req.dst_type)
    try:
        eid = store.link(
            _con(request),
            edge,
            confidence=req.confidence,
            source_doc=req.source_doc,
            ontology_version=req.ontology_version,
            producer=req.producer,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"edge_id": eid}


@app.get("/traverse")
def traverse(entity: str, request: Request, hops: int = 1) -> list[dict[str, Any]]:
    return store.traverse(_con(request), entity, hops)


@app.get("/ontology")
def ontology_view() -> dict[str, Any]:
    """The current ontology: what the graph and the review view may use."""
    onto = ontology.current()
    return {
        "version": onto.version,
        "modules": {
            m.name: {"version": m.version, "requires": list(m.requires)}
            for m in onto.modules.values()
        },
        "entity_types": sorted(onto.entity_types),
        "types": {
            t.name: {"module": t.module, "parent": t.parent}
            for t in onto.types.values()
        },
        "relations": {
            r.name: {
                "domain": sorted(r.domain),
                "range": sorted(r.range),
                "description": r.description,
                "module": r.module,
            }
            for r in onto.relations.values()
        },
    }


# ----------------------------------------------------------- review queue


class ReviewReq(BaseModel):
    resolution: str  # linked | dropped | ontology
    src_type: str | None = None  # overrides for "linked"
    rel: str | None = None
    dst_type: str | None = None
    confidence: str = "EXTRACTED"


class BulkReq(BaseModel):
    resolution: str  # dropped | ontology
    rel: str | None = None
    unmapped: bool | None = None


@app.get("/review")
def review_list(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    open: bool = True,
    rel: str | None = None,
    unmapped: bool | None = None,
) -> dict[str, Any]:
    """Review items (misfit triples), oldest first, with the total; filters
    by relation and by unmapped (untyped) versus typed items."""
    con = _con(request)
    kw: dict[str, Any] = {"open_only": open, "rel": rel, "unmapped": unmapped}
    return {
        "items": store.list_review(con, limit=limit, offset=offset, **kw),
        "total": store.count_review(con, **kw),
    }


@app.post("/review/bulk")
def review_bulk(req: BulkReq, request: Request) -> dict[str, int]:
    """Close every open item matching the filter (``dropped`` or
    ``ontology``); linking in bulk is what ``/review/replay`` does."""
    if req.resolution not in ("dropped", "ontology"):
        raise HTTPException(400, "bulk resolution must be dropped or ontology")
    if not req.rel and req.unmapped is None:
        raise HTTPException(400, "a filter is required")
    n = store.resolve_review_many(
        _con(request), req.resolution, rel=req.rel, unmapped=req.unmapped
    )
    return {"resolved": n}


@app.post("/review/replay")
def review_replay(request: Request) -> dict[str, Any]:
    """Link the typed open items the current ontology now accepts."""
    rep = review.replay(_con(request))
    return rep.__dict__


@app.post("/review/{review_id}")
def resolve_review(review_id: int, req: ReviewReq, request: Request) -> dict[str, Any]:
    """Close a review item; ``linked`` writes it as an edge first, with the
    item's fields unless the request overrides the types or relation."""
    con = _con(request)
    item = store.get_review(con, review_id)
    if item is None:
        raise HTTPException(404, "no such review item")
    out: dict[str, Any] = {"ok": True}
    try:
        if req.resolution == "linked":
            edge = store.Edge(
                item["src"],
                req.src_type or item["src_type"] or "",
                req.rel or item["rel"],
                item["dst"],
                req.dst_type or item["dst_type"] or "",
            )
            out["edge_id"] = store.link(
                con,
                edge,
                confidence=req.confidence,
                source_doc=item["source_doc"],
                evidence=item["evidence"],
                producer="manual",
                run=f"review-{review_id}",
            )
        store.resolve_review(con, review_id, req.resolution)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return out


# ------------------------------------------------------ browsing (the UI)


@app.get("/documents")
def documents(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    title: str | None = None,
    source: str | None = None,
    mime: str | None = None,
    retired: bool = False,
    domain: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Documents without text, newest first, filtered for browsing;
    ``domain`` keeps one ontology module's documents, ``tag`` the
    documents carrying a tag (``project:synth``)."""
    return store.list_documents(
        _con(request),
        limit=limit,
        offset=offset,
        title=title,
        source=source,
        mime_prefix=mime,
        retired=retired,
        domain=domain or None,
        tag=tag or None,
    )


@app.get("/doc/{doc_id}/original")
def original(doc_id: int, request: Request) -> FileResponse:
    """The archived original with its MIME type, shown inline (a PDF opens
    in the browser's viewer; ``#page=N`` selects a page)."""
    info = store.original_info(_con(request), doc_id)
    if info is None or not info["path"].exists():
        raise HTTPException(404, "no such document")
    name = Path(info["original_path"] or info["title"] or f"document-{doc_id}").name
    headers = {"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"}
    if (info["mime"] or "").split(";")[0] in ("text/html", "application/xhtml+xml"):
        # a captured page is somebody else's content rendered from this
        # origin: no scripts, no forms, no access to the door's cookies
        headers["Content-Security-Policy"] = (
            "sandbox; default-src data: 'unsafe-inline'"
        )
    return FileResponse(info["path"], media_type=info["mime"], headers=headers)


@app.get("/doc/{doc_id}/figure/{ref}")
def figure(doc_id: int, ref: str, request: Request) -> Response:
    """A figure's bytes out of the document's original, by the hash the
    text references (``![caption](figure:<sha256>)``); immutable, so
    cached for good."""
    from prax.parsers import figures

    con = _con(request)
    info = store.original_info(con, doc_id)
    if info is None or not info["path"].exists():
        raise HTTPException(404, "no such document")
    found = figures.find(info["path"].read_bytes(), ref)
    if found is None:
        raise HTTPException(404, "no such figure in the original")
    data, media = found
    return Response(
        data,
        media_type=media,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/doc/{doc_id}/text")
def text(doc_id: int, request: Request) -> PlainTextResponse:
    """The Markdown text artifact of a document."""
    doc = store.get_document(_con(request), doc_id)
    if doc is None:
        raise HTTPException(404, "no such document")
    return PlainTextResponse(doc["text"], media_type="text/markdown; charset=utf-8")


@app.get("/doc/{doc_id}/chunks")
def chunks(doc_id: int, request: Request) -> list[dict[str, Any]]:
    """The document as its chunks in order, with kind, heading, page, text."""
    try:
        store.get_meta(_con(request), doc_id)
    except KeyError as exc:
        raise HTTPException(404, "no such document") from exc
    return store.list_chunks(_con(request), doc_id)


@app.get("/entities")
def entities(q: str, request: Request, limit: int = 20) -> list[dict[str, Any]]:
    """Entities whose name contains ``q``, most connected first."""
    return store.find_entities(_con(request), q, limit=limit)


@app.get("/doc/{doc_id}/context")
def doc_context(
    doc_id: int, request: Request, limit: int = 8, domain: str | None = None
) -> dict[str, Any]:
    """What places the document in the library: summary and entities,
    citations in and out, nearest documents by vector (within ``domain``
    when given), documents sharing entities or authors, Zotero parent and
    siblings."""
    ctx = store.document_context(
        _con(request), doc_id, limit=limit, domain=domain or None
    )
    if ctx is None:
        raise HTTPException(404, "no such document")
    return ctx


# ------------------------------------------------------------------ pages


class PageReq(BaseModel):
    text: str
    title: str | None = None
    kind: str = "topic"  # addendum | project | topic (creation only)
    author: str = "human"
    note: str | None = None
    annotates: list[int] | None = None
    part_of: str | None = None
    force: bool = False


class AppendReq(BaseModel):
    section: str
    heading: str | None = None
    author: str = "agent"
    note: str | None = None


class MemberReq(BaseModel):
    doc_id: int


@app.get("/pages")
def pages(request: Request, kind: str | None = None) -> list[dict[str, Any]]:
    """Pages, most recently revised first."""
    return store.list_pages(_con(request), kind=kind)


@app.get("/page/{slug}")
def page(slug: str, request: Request) -> dict[str, Any]:
    p = store.get_page(_con(request), store.slugify(slug))
    if p is None:
        raise HTTPException(404, "no such page")
    return p


@app.get("/page/{slug}/revision/{revision}")
def page_revision(slug: str, revision: int, request: Request) -> dict[str, Any]:
    try:
        text = store.page_revision_text(_con(request), slug, revision)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"slug": slug, "revision": revision, "text": text}


@app.put("/page/{slug}")
def put_page(slug: str, req: PageReq, request: Request) -> dict[str, Any]:
    """Create the page or add a revision. An agent revision over a human
    one is refused with 409 unless ``force``; use append."""
    try:
        return store.write_page(
            _con(request),
            slug,
            req.text,
            title=req.title,
            kind=req.kind,
            author=req.author,
            note=req.note,
            annotates=req.annotates,
            part_of=req.part_of,
            force=req.force,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/page/{slug}/append")
def append_page(slug: str, req: AppendReq, request: Request) -> dict[str, Any]:
    try:
        return store.append_page(
            _con(request),
            slug,
            req.section,
            heading=req.heading,
            author=req.author,
            note=req.note,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/project/{slug}/members")
def add_member(slug: str, req: MemberReq, request: Request) -> dict[str, Any]:
    """``part_of`` edge from a document to a project page."""
    try:
        eid = store.add_to_project(_con(request), slug, req.doc_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"edge_id": eid, "existing": eid is None}


# ------------------------------------------------------------------- ask


class AskReq(BaseModel):
    question: str
    limit: int = ask_mod.PASSAGES
    doctype: str | None = None
    backend: str | None = None  # local | claude | none; None: the host default
    history: list[dict[str, Any]] | None = None  # earlier turns: question, answer
    steps: int | None = None  # surfing steps before the answer; 0: one shot
    tokens: int | None = None  # the reading budget of the steps
    stream: bool = False  # the trail as it happens, one JSON line per event


class SaveReq(BaseModel):
    slug: str
    result: dict[str, Any]
    heading: str | None = None
    create: str | None = None  # a page kind: create the page when the slug is new


@app.get("/ask/config")
def ask_config() -> dict[str, Any]:
    """The backend this host answers with, for the UI's choice."""
    return ask_mod.describe()


@app.post("/ask")
def ask(req: AskReq, request: Request) -> Any:
    """Passages and graph facts for a question, and an answer citing them
    when a backend is configured. With ``steps`` the model surfs first
    (``prax.surf``): the budgets default to the host's and are clamped
    to what the model holds; the result carries the trail. ``stream``
    answers with one JSON object per line as it goes — ``step`` events,
    ``answering``, then ``answer`` with the result (or ``error``) — for a
    client that shows the trail while the model works. Generation runs
    outside the store lock; a local model answers in tens of seconds, a
    surf in a minute or two."""
    try:
        answerer = (
            ask_mod.answerer_named(req.backend) if req.backend else ask_mod.current()
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    steps = req.steps
    if steps is None and answerer is not None:
        steps = ask_mod.default_steps()
    kw: dict[str, Any] = {
        "limit": max(1, min(req.limit, 20)),
        "doctype": req.doctype,
        "answerer": answerer,
        "history": req.history,
        "steps": max(0, steps or 0),
        "tokens": req.tokens,
    }
    if not req.stream:
        try:
            return ask_mod.ask(_con(request), req.question, **kw)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
    return _ask_stream(req.question, kw)


def _ask_stream(question: str, kw: dict[str, Any]) -> StreamingResponse:
    """The surf on its own thread with its own connection, each event
    handed to the response as a line. A client that goes away stops the
    surf at its next step (the response's ``finally`` sets ``stop``; the
    model is not asked again for nobody)."""
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()
    stop = threading.Event()

    def run() -> None:
        con = store.connect()
        try:
            result = ask_mod.ask(con, question, on_event=events.put, stop=stop, **kw)
            events.put({"event": "answer", "result": result})
        except Exception as exc:  # noqa: BLE001 - the client gets the reason
            events.put({"event": "error", "detail": str(exc)})
        finally:
            con.close()
            events.put(None)

    threading.Thread(target=run, name="prax-ask", daemon=True).start()

    def next_event() -> dict[str, Any] | None | bool:
        try:
            return events.get(timeout=1.0)
        except queue.Empty:
            return False  # nothing yet: look again (and notice a cancel)

    async def lines() -> AsyncIterator[str]:
        try:
            while True:
                event = await anyio.to_thread.run_sync(
                    next_event, abandon_on_cancel=True
                )
                if event is None:
                    break
                if event is False:
                    continue
                yield json.dumps(event, ensure_ascii=False) + "\n"
        finally:
            stop.set()

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.post("/ask/save")
def ask_save(req: SaveReq, request: Request) -> dict[str, Any]:
    """Append an answer (the result of ``POST /ask``) to a page as the agent."""
    try:
        return ask_mod.save(
            _con(request),
            req.result,
            req.slug,
            heading=req.heading,
            create=req.create,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------- domains


class DomainsReq(BaseModel):
    domains: list[str] | None = None  # None: every module
    by: str = "human"  # "agent" from the MCP proxy


@app.get("/doc/{doc_id}/domains")
def get_domains(doc_id: int, request: Request) -> dict[str, Any]:
    try:
        return {
            "domains": store.document_domains(_con(request), doc_id),
            "modules": sorted(
                m for m in ontology.current().modules if m != ontology.CORE
            ),
        }
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/doc/{doc_id}/domains")
def put_domains(doc_id: int, req: DomainsReq, request: Request) -> dict[str, Any]:
    """Replace the document's domain set; null means every module."""
    try:
        return {
            "domains": store.set_domains(_con(request), doc_id, req.domains, by=req.by)
        }
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/doc/{doc_id}/domains/{domain}")
def post_domain(doc_id: int, domain: str, request: Request) -> dict[str, Any]:
    try:
        return {"domains": store.add_domain(_con(request), doc_id, domain)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/doc/{doc_id}/domains/{domain}")
def delete_domain(doc_id: int, domain: str, request: Request) -> dict[str, Any]:
    try:
        return {"domains": store.remove_domain(_con(request), doc_id, domain)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


# --------------------------------------------------------------- promote


class PromoteReq(BaseModel):
    reason: str | None = None
    by: str = "human"


@app.post("/doc/{doc_id}/promote")
def promote_doc(doc_id: int, req: PromoteReq, request: Request) -> dict[str, Any]:
    """Flag a document for the expensive pass (the ``promote`` work step)."""
    try:
        return store.promote(_con(request), doc_id, by=req.by, reason=req.reason)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/doc/{doc_id}/promote")
def unpromote_doc(doc_id: int, request: Request) -> dict[str, bool]:
    return {"removed": store.unpromote(_con(request), doc_id)}


class ReadingReq(BaseModel):
    extractor: str  # one of store.READINGS
    mode: str | None = (
        None  # store.MODES: vision-pages scans|all, figures captioned|all, OCR a word
    )
    by: str = "human"


@app.post("/doc/{doc_id}/reading")
def request_reading(doc_id: int, req: ReadingReq, request: Request) -> dict[str, Any]:
    """Ask for a named extractor on this document — the vision model over
    its scanned pages, a second reading of an image, OCR, Docling. A
    worker picks it up (``GET /work/parse`` hands requests out first);
    the outcome lands in ``meta.reading``."""
    from prax import parsers

    doc = store.get_document(_con(request), doc_id, max_chars=0)
    if doc is None:
        raise HTTPException(404, "no such document")
    if req.extractor not in store.READINGS:
        raise HTTPException(400, f"extractor must be one of {store.READINGS}")
    # the type only: whether the extractor's server is up is the worker's
    # business when it runs the reading (the request waits otherwise)
    if not parsers.by_name(req.extractor).accepts(doc["mime"] or ""):
        raise HTTPException(400, f"{req.extractor} does not read {doc['mime']}")
    try:
        return store.request_reading(
            _con(request), doc_id, req.extractor, mode=req.mode, by=req.by
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class BulkReadingReq(BaseModel):
    extractor: str  # one of store.READINGS
    mode: str | None = None  # store.MODES
    ids: list[int] | None = None
    mime: str | None = None  # a type or a prefix: application/pdf, image/
    text_source: str | None = None  # a stamp prefix: what an old extractor read
    title: str | None = None  # words the title contains
    unreadable: bool = False  # the documents nothing here could read
    thin: int | None = None  # PDFs with under this many bytes of text a page
    doctype: str | None = None  # pdf, web, video, image, text, note, page
    unpolished: bool = False  # videos with an automatic transcript not yet polished
    read_figures: bool = False  # the documents whose figures a model has read
    unread_figures: bool = False  # the documents holding a figure nobody read
    read_formulas: bool = False  # the same for display equations
    unread_formulas: bool = False
    maths: float | None = None  # references to numbered equations per 10k characters
    limit: int | None = None
    dry_run: bool = False  # count, place nothing
    by: str = "human"


@app.post("/readings/bulk")
def request_readings(req: BulkReadingReq, request: Request) -> dict[str, Any]:
    """Ask for a named extractor over a selection at once — OCR over every
    scan nothing could read, the vision model over their pages, a re-read
    of what an old extractor produced (``text_source`` prefix), the
    figures a model has read before under a better prompt
    (``read_figures`` with ``mode=again``), a list of ids — one reading
    request per document (``POST /doc/{id}/reading``);
    what the extractor does not read is skipped and counted. The worker
    drains them like any request, and refuses a paid model. ``dry_run``
    only counts."""
    con = _con(request)
    if req.extractor not in store.READINGS:
        raise HTTPException(400, f"extractor must be one of {store.READINGS}")
    try:
        store.check_mode(req.extractor, req.mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not (
        req.ids
        or req.mime
        or req.text_source
        or req.title
        or req.unreadable
        or req.thin is not None
        or req.doctype
        or req.unpolished
        or req.read_figures
        or req.unread_figures
        or req.read_formulas
        or req.unread_formulas
        or req.maths is not None
    ):
        raise HTTPException(
            400,
            "a selection: ids, mime, text_source, title, unreadable, thin,"
            " doctype, unpolished, read_figures, unread_figures, read_formulas,"
            " unread_formulas or maths",
        )
    try:
        ids = store.select_for_reading(
            con,
            ids=req.ids,
            mime=req.mime,
            text_source=req.text_source,
            title=req.title,
            unreadable=req.unreadable,
            thin=req.thin,
            doctype=req.doctype,
            unpolished=req.unpolished,
            read_figures=req.read_figures,
            unread_figures=req.unread_figures,
            read_formulas=req.read_formulas,
            unread_formulas=req.unread_formulas,
            maths=req.maths,
            limit=req.limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if req.dry_run:
        return {"selected": len(ids), "requested": 0, "skipped": 0, "dry_run": True}
    try:
        counts = store.request_readings(
            con, ids, req.extractor, mode=req.mode, by=req.by
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**counts, "dry_run": False}


@app.delete("/doc/{doc_id}/reading")
def cancel_reading(doc_id: int, request: Request) -> dict[str, bool]:
    return {"removed": store.cancel_reading(_con(request), doc_id)}


@app.get("/readings")
def readings(request: Request, limit: int = 50) -> dict[str, Any]:
    """Reading requests: the waiting ones (a page of them, with how many
    wait in all: a bulk re-read places thousands) and the recently
    finished."""
    con = _con(request)
    return {
        "waiting": store.count_reading_requests(con),
        "by_extractor": store.waiting_readings(con),
        "requested": store.reading_requests(con, state="requested", limit=limit),
        "recent": store.finished_readings(con, limit=limit),
        "vision": models.describe("vision"),
    }


@app.get("/promote")
def promote_view(request: Request, limit: int = 30) -> dict[str, Any]:
    """The flagged documents with their status under the promote step's
    model, and the candidates the library keeps coming back to."""
    step = models.describe("promote")
    producer = step["runtime"]
    return {
        "step": step,
        "promoted": store.promoted_documents(_con(request), producer=producer),
        "candidates": store.promotion_candidates(_con(request), limit=limit),
    }


@app.get("/graph/overview")
def graph_overview(
    request: Request, limit: int = 30, min_shared: int = 2
) -> dict[str, Any]:
    """The most connected concepts, methods, tools and datasets, the edges
    among them, and co-occurrence links (hubs sharing at least
    ``min_shared`` source documents): what the graph view opens on."""
    return store.hub_graph(_con(request), limit=limit, min_shared=min_shared)


class UIError(BaseModel):
    kind: str = "error"
    message: str
    stack: str | None = None
    hash: str | None = None
    agent: str | None = None


@app.post("/ui/error", include_in_schema=False)
def ui_error(err: UIError, request: Request) -> dict[str, bool]:
    """A client-side error, logged by the door so it can be read later."""
    logging.getLogger("prax.ui").warning(
        "browser %s at %s: %s\n%s\n%s",
        err.kind,
        err.hash,
        err.message,
        err.stack or "",
        err.agent or "",
    )
    return {"logged": True}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


# What the UI page may load and run. The UI renders Markdown that other
# people wrote (captured pages, a model's answer) into HTML, and marked
# passes raw HTML through; this is what keeps a <script> or an onerror=
# in that text from running with the session cookie: only the UI's own
# files run as script, nothing inline, no javascript: links, no forms
# posting elsewhere, no framing, images only from the door itself.
UI_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    # a video document's player, loaded only when the reader presses play
    "frame-src https://www.youtube-nocookie.com; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


class _UIFiles(StaticFiles):
    """Static files that browsers revalidate on every load (ETag makes
    that cheap), so a redeploy never leaves a stale app.js behind, and
    that carry the UI's content security policy."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Content-Security-Policy"] = UI_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


app.mount("/ui", _UIFiles(directory=UI_DIR, html=True), name="ui")
