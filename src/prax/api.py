"""FastAPI app — the HTTP door. Thin wrappers over prax.store.

The store connection is opened in the app lifespan and shared by all
handlers; prax.store serializes access.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ask as ask_mod
from . import auth, embeddings, hostinfo, inbox, models, ontology, review, store, work

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


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    con = store.connect()
    store.init_db(con)
    app.state.con = con  # the main connection: migrations, the change stamp
    store.job_reap(con)  # sessions left behind by a killed door or worker
    stop = threading.Event()
    every = float(os.environ.get("PRAX_INBOX_SCAN", "20") or 0)
    scanner = None
    if every > 0:
        scanner = threading.Thread(
            target=_scan_inbox, args=(app, stop, every), name="prax-inbox", daemon=True
        )
        scanner.start()
    try:
        yield
    finally:
        stop.set()
        if scanner is not None:
            scanner.join(timeout=5)
        con.close()


app = FastAPI(title="prax", version="0.0.1", lifespan=_lifespan)
app.middleware("http")(auth.middleware)
app.state.writes = 0


@app.middleware("http")
async def _count_writes(request: Request, call_next: Any) -> Any:
    """Every mutating request bumps a counter: with SQLite's data_version
    (other writers) it makes the change stamp the UI polls."""
    response = await call_next(request)
    if request.method not in ("GET", "HEAD", "OPTIONS") and response.status_code < 400:
        request.app.state.writes += 1
    return response


# A browser extension calls the door from its own origin
# (chrome-extension://…, moz-extension://…): PRAX_CORS_ORIGINS lists the
# origins allowed, comma-separated. Unset, no cross-origin request is
# answered (the UI is same-origin).
_cors = [
    o.strip() for o in os.environ.get("PRAX_CORS_ORIGINS", "").split(",") if o.strip()
]
if _cors:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
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
) -> dict[str, Any]:
    """Upload a file: archived at once, text and HTML indexed at once,
    anything else parsed by the batch host. ``domains`` and ``tags`` are
    comma-separated; ``by`` says what sent it (the extension, a script)."""
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
    mode: str | None = None  # "snapshot" (self-contained) or "dom"
    note: str | None = None


class IngestUrl(BaseModel):
    url: str
    title: str | None = None
    domains: list[str] | None = None
    tags: list[str] | None = None
    session: str | None = None
    by: str | None = "url"  # "agent" from the MCP proxy


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
    return {"ok": True}


@app.get("/work/{step}")
def work_out(
    step: str, request: Request, limit: int = 10, scope: str = "captures"
) -> dict[str, Any]:
    """A leased batch of work: parse, titles, extract or embed."""
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
    return {  # when any other connection commits, this door's threads included
        "stamp": f"{store.data_version(con)}-{request.app.state.writes}",
        "jobs": store.running_jobs(con),
    }


@app.get("/jobs")
def jobs(request: Request, limit: int = 20) -> dict[str, Any]:
    """What runs and what ran lately, and what this door's host has left
    (free RAM, commit headroom) so a wall is visible before it is hit."""
    out = store.list_jobs(_con(request), limit=limit)
    out["host"] = {"name": socket.gethostname(), **hostinfo.memory()}
    return out


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
) -> dict[str, Any]:
    """Documents without text, newest first, filtered for browsing."""
    return store.list_documents(
        _con(request),
        limit=limit,
        offset=offset,
        title=title,
        source=source,
        mime_prefix=mime,
        retired=retired,
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
def doc_context(doc_id: int, request: Request, limit: int = 8) -> dict[str, Any]:
    """What places the document in the library: summary and entities,
    citations in and out, nearest documents by vector, documents sharing
    entities or authors, Zotero parent and siblings."""
    ctx = store.document_context(_con(request), doc_id, limit=limit)
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
    p = store.get_page(_con(request), slug)
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
def ask(req: AskReq, request: Request) -> dict[str, Any]:
    """Passages and graph facts for a question, and an answer citing them
    when a backend is configured. Generation runs outside the store lock;
    a local model answers in tens of seconds."""
    try:
        answerer = (
            ask_mod.answerer_named(req.backend) if req.backend else ask_mod.current()
        )
        return ask_mod.ask(
            _con(request),
            req.question,
            limit=max(1, min(req.limit, 20)),
            doctype=req.doctype,
            answerer=answerer,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


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
    """Flag a document for the expensive pass (``extract_graph.py --promoted``)."""
    try:
        return store.promote(_con(request), doc_id, by=req.by, reason=req.reason)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/doc/{doc_id}/promote")
def unpromote_doc(doc_id: int, request: Request) -> dict[str, bool]:
    return {"removed": store.unpromote(_con(request), doc_id)}


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


class _UIFiles(StaticFiles):
    """Static files that browsers revalidate on every load (ETag makes
    that cheap), so a redeploy never leaves a stale app.js behind."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/ui", _UIFiles(directory=UI_DIR, html=True), name="ui")
