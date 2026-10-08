"""Documents coming in: text, files, pages the browser rendered, URLs the
door fetches; retiring; the inbox view."""

from __future__ import annotations

import http.client
import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from prax import store
from prax.capture import inbox
from prax.graph import ontology

from ._base import _capture_out, _con, _split, max_upload, ui_url

_SHA256 = re.compile(r"[0-9a-f]{64}")

router = APIRouter()


class IngestText(BaseModel):
    text: str
    title: str | None = None
    source_url: str | None = None
    meta: dict[str, Any] | None = None
    domains: list[str] | None = None
    sensitivity: str | None = None  # "personal": behind the wall from the start
    by: str = "human"  # who says so: "agent" from the MCP proxy


def _written_personal(con: Any, doc_id: int, sensitivity: str | None, by: str) -> None:
    """A write that says its document is personal (a colleague's notes):
    behind the wall at once (stage U). Only ``personal`` is taken: a
    writer may hide what it writes, never open what is hidden."""
    if sensitivity is None:
        return
    if sensitivity != "personal":
        raise HTTPException(400, "sensitivity on a write is personal, or none")
    store.set_sensitivity(con, doc_id, "personal", by=by)


@router.post("/ingest")
def ingest(req: IngestText, request: Request) -> dict[str, Any]:
    # checked before anything is written: a refused request leaves no
    # document, least of all one it meant to keep personal open (the
    # review of 2026-10-04)
    if req.sensitivity not in (None, "personal"):
        raise HTTPException(400, "sensitivity on a write is personal, or none")
    try:
        store._check_domains(req.domains or [])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = store.ingest_text(
        _con(request),
        req.text,
        title=req.title,
        source_url=req.source_url,
        meta=req.meta,
    )
    _written_personal(_con(request), int(result["doc_id"]), req.sensitivity, req.by)
    for d in req.domains or []:
        store.add_domain(_con(request), result["doc_id"], d)
    return {**result, "url": ui_url(request, int(result["doc_id"]))}


class ProjectSync(BaseModel):
    """``prax.client.project_files``' answer and the settings beside it."""

    files: list[dict[str, Any]] = []
    remote: str | None = None
    prefix: str = ""
    root_name: str | None = None
    tracked: bool | None = None
    skipped: dict[str, Any] = {}
    name: str | None = None
    domains: list[str] | None = None
    tags: list[str] | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None
    auto_sync: bool | None = None
    sensitivity: str | None = None  # "personal": every synced note of the project
    dry_run: bool = True


@router.post("/projects/sync")
def projects_sync(req: ProjectSync, request: Request) -> dict[str, Any]:
    """A project's documents planned (add, refresh, unchanged, moved, skip
    and why, gone) and, unless ``dry_run``, applied; the manifest kept in
    prax (``prax.capture.projects``)."""
    from prax.capture import projects

    try:
        return projects.sync(_con(request), req.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except projects.NotAllowed as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc


@router.get("/projects")
def projects_list(
    request: Request, remote: str | None = None, prefix: str = ""
) -> dict[str, Any]:
    """Every project's manifest, or with ``remote`` the one of that working
    copy (``project`` null when it has none): what the session-end hook
    asks before it syncs on its own."""
    con = _con(request)
    if remote:
        return {"project": store.project_at(con, remote, prefix.strip("/"))}
    return {"projects": store.list_projects(con)}


class TitleReq(BaseModel):
    title: str
    by: str = "agent"  # who says so: "human" from the UI


@router.put("/doc/{doc_id}/title")
def set_title(doc_id: int, req: TitleReq, request: Request) -> dict[str, Any]:
    """A document's title, the old one kept in its history and its entity
    following (``store.retitle``). A document the viewer may not see is a
    404, as if absent."""
    con = _con(request)
    if store.document_hidden(con, doc_id):
        raise HTTPException(404, f"no such document: {doc_id}")
    try:
        out = store.retitle(con, doc_id, req.title, source=req.by)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'\"")) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**out, "url": ui_url(request, doc_id)}


class RetireReq(BaseModel):
    reason: str = "retired by hand"
    duplicate_of: int | None = None


@router.post("/doc/{doc_id}/retire")
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


@router.delete("/doc/{doc_id}/retire")
def unretire(doc_id: int, request: Request) -> dict[str, Any]:
    try:
        return store.unretire_document(_con(request), doc_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/cleanup")
def cleanup_runs(request: Request) -> dict[str, Any]:
    """The clean-up rules, and the clean-ups done (what a restore takes)."""
    from prax.text import clutter

    return {"rules": clutter.RULES, "runs": store.cleanup_runs(_con(request))}


@router.get("/cleanup/{rule}")
def cleanup_preview(
    rule: str, request: Request, folder: str | None = None, limit: int = 30
) -> dict[str, Any]:
    """What a clean-up rule would retire, before it does (stage X)."""
    try:
        return store.cleanup_preview(
            _con(request), rule, folder=folder, limit=max(1, min(limit, 200))
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class CleanupReq(BaseModel):
    folder: str | None = None


@router.post("/cleanup/{rule}")
def cleanup_retire(rule: str, req: CleanupReq, request: Request) -> dict[str, Any]:
    """Retire every document the rule picks, under one run name."""
    try:
        return store.retire_set(_con(request), rule, folder=req.folder)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class RestoreReq(BaseModel):
    run: str


@router.post("/cleanup-restore")
def cleanup_restore(req: RestoreReq, request: Request) -> dict[str, Any]:
    """Bring a clean-up's documents back, with the facts it ended."""
    try:
        return store.restore_set(_con(request), req.run)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/captures")
def captures(url: str, request: Request) -> list[dict[str, Any]]:
    """The live captures of one URL (canonicalised here), oldest first:
    what an importer asks before fetching a link it may already hold."""
    try:
        inbox.check_url(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return store.live_captures_of(_con(request), inbox.canonical_url(url))


@router.post("/inbox/dedupe")
def dedupe(request: Request, commit: bool = False) -> dict[str, Any]:
    """Retire the duplicate captures of each URL (dry run unless commit)."""
    return store.dedupe_captures(_con(request), commit=commit)


# what a sender may stamp a text with: a name and a version,
# "latex-source/3.9"; ``_own_stamp`` refuses a parser's name
_STAMP = re.compile(r"[a-z][a-z0-9-]{1,40}/[\w.+-]{1,40}")


def _own_stamp(stamp: str) -> bool:
    """A stamp of the sender's own: the shape, and no extractor's name,
    which the parse queue would take for its own reading."""
    from prax import parsers

    if not _STAMP.fullmatch(stamp):
        return False
    try:
        parsers.by_name(stamp.split("/", 1)[0])
    except KeyError:
        return True
    return False


@router.post("/ingest/file")
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
    origin: Annotated[str | None, Form()] = None,
    text: Annotated[str | None, Form()] = None,
    text_source: Annotated[str | None, Form()] = None,
    text_file: Annotated[UploadFile | None, File()] = None,
) -> dict[str, Any]:
    """Upload a file: archived at once, text and HTML indexed at once,
    anything else parsed by the batch host. ``domains`` and ``tags`` are
    comma-separated; ``by`` says what sent it (the extension, a script);
    ``paper`` is JSON — what the sender read off the page the file came
    from (doi, arxiv, authors, journal, date, pdf_url); ``origin`` is
    JSON too — where the file lives on the machine that sent it (host,
    path; ``clients/send/prax_send.py``). ``text`` is the file's text read
    from a better source, a paper's LaTeX (``prax import latex``), with
    ``text_source`` its stamp (``latex-source/3.9``): taken in as a parse
    is, and the parse queue then leaves the file alone. A text past the
    form's 1 MB a field (a page with its pictures inlined) comes as the
    file part ``text_file``, UTF-8."""
    if text_file is not None:
        text = text_file.file.read(max_upload() + 1).decode("utf-8", "replace")
    if (text is None) != (text_source is None):
        raise HTTPException(400, "text and text_source come together")
    if text_source is not None and not _own_stamp(text_source):
        raise HTTPException(
            400, "text_source is a stamp of the sender's own: name/version"
        )
    if text is not None and len(text.encode("utf-8")) > max_upload():
        raise HTTPException(413, "text over door.max_upload_mb")
    paper_info = origin_info = None
    try:
        paper_info = json.loads(paper) if paper else None
        origin_info = json.loads(origin) if origin else None
    except ValueError as exc:
        raise HTTPException(400, f"paper and origin must be JSON: {exc}") from exc
    data = file.file.read(max_upload() + 1)  # chunked uploads carry no length
    if len(data) > max_upload():
        raise HTTPException(
            413, f"file over {max_upload() >> 20} MB (door.max_upload_mb)"
        )
    try:
        cap = inbox.ingest_upload(
            _con(request),
            data,
            filename=file.filename,
            mime=file.content_type,
            title=title,
            source_url=source_url,
            domains=_split(domains),
            tags=_split(tags),
            session=session,
            by=by or "upload",
            paper=paper_info if isinstance(paper_info, dict) else None,
            origin=origin_info if isinstance(origin_info, dict) else None,
            text=text,
            text_source=text_source,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _capture_out(cap, request)


class KnownReq(BaseModel):
    hashes: list[str]


@router.post("/known")
def known(req: KnownReq, request: Request) -> dict[str, Any]:
    """Which of these sha256 hashes (of a file's bytes) the door already
    holds: what a sender asks before it sends (``prax_send.py``). At most
    ``store.KNOWN_BATCH`` a question."""
    if len(req.hashes) > store.KNOWN_BATCH:
        raise HTTPException(413, f"at most {store.KNOWN_BATCH} hashes a request")
    bad = [h for h in req.hashes if not _SHA256.fullmatch(h.lower())]
    if bad:
        raise HTTPException(400, f"not a sha256 hex digest: {bad[0][:80]!r}")
    return {"known": store.known_hashes(_con(request), req.hashes)}


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


@router.post("/ingest/html")
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
    return _capture_out(cap, request)


@router.post("/ingest/url", response_model=None)
def ingest_url(req: IngestUrl, request: Request) -> dict[str, Any] | JSONResponse:
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
    except (ValueError, http.client.InvalidURL, OSError) as exc:
        # not one to fetch (400); unreachable, 404, a timeout (502); refused
        # or a certificate the server cannot verify: kept for the extension
        status, body = _failed_capture(req, exc)
        if status == 202:
            return JSONResponse(status_code=202, content=body)
        raise HTTPException(status, body["error"]) from exc
    return _capture_out(cap, request)


class IngestUrls(BaseModel):
    items: list[IngestUrl]


BATCH_URLS = 20  # URLs one request may carry
BATCH_FETCHES = 4  # fetched at once


def _fetch_failure(exc: Exception) -> tuple[int, str]:
    """What ``ingest_url`` said when a fetch failed, as a status and the
    words a person reads (the single route's 400 and 502)."""
    if isinstance(exc, (ValueError, http.client.InvalidURL)):
        return 400, f"not a URL to fetch: {exc}"
    why = str(exc)
    if _browser_can(exc):
        why += (
            " (the site refused the server; it waits for the browser extension,"
            " which fetches it with your own session)"
        )
    return 502, f"fetch failed: {why}"


# what a browser gets past and the server does not: a refusal, a bot check,
# a certificate chain the server cannot verify. A 404 is not one: the
# browser cannot fetch what is not there either
_REFUSED = re.compile(r"\b(401|403|429)\b|CERTIFICATE|SSL", re.IGNORECASE)


def _browser_can(exc: Exception) -> bool:
    return isinstance(exc, inbox.BotCheck) or bool(_REFUSED.search(str(exc)))


def _failed_capture(item: IngestUrl, exc: Exception) -> tuple[int, dict[str, Any]]:
    """A fetch that failed, as a status and an answer: what the browser
    can fetch is kept for the extension (202, ``queued_for_extension``,
    the ``request`` id); anything else is the error (400, 502)."""
    status, detail = _fetch_failure(exc)
    if status == 502 and _browser_can(exc):
        req = store.ask_extension(
            store.thread_connection(),
            item.url,
            detail,
            title=item.title,
            domains=item.domains,
            tags=item.tags,
            by=item.by,
            note=item.note,
        )
        return 202, {
            "queued_for_extension": True,
            "request": req["id"],
            "error": detail,
        }
    return status, {"error": detail}


@router.post("/ingest/urls")
def ingest_urls(req: IngestUrls, request: Request) -> dict[str, Any]:
    """Several URLs fetched at once (``BATCH_FETCHES`` at a time, at most
    ``BATCH_URLS``), one result each in the order sent: the capture as
    ``POST /ingest/url`` answers it, or ``error`` with its ``status``. One
    URL that fails never fails the others."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    if len(req.items) > BATCH_URLS:
        raise HTTPException(400, f"at most {BATCH_URLS} URLs a request")

    def one(item: IngestUrl) -> dict[str, Any]:
        try:
            cap = inbox.ingest_url(
                store.thread_connection(),
                item.url,
                title=item.title,
                domains=item.domains,
                tags=item.tags,
                session=item.session,
                by=item.by,
                note=item.note,
            )
        except (ValueError, http.client.InvalidURL, OSError) as exc:
            status, body = _failed_capture(item, exc)
            return {"source": item.url, **body, "status": status}
        return {"source": item.url, **_capture_out(cap, request)}

    with ThreadPoolExecutor(max_workers=BATCH_FETCHES) as pool:
        # each thread sees the request's viewer (the wall), as ask's do
        futures = [
            pool.submit(contextvars.copy_context().run, one, item) for item in req.items
        ]
        results = [f.result() for f in futures]
    queued = sum(1 for r in results if r.get("queued_for_extension"))
    return {
        "results": results,
        "captured": sum(1 for r in results if "error" not in r),
        "queued": queued,
        "failed": sum(1 for r in results if "error" in r) - queued,
    }


class RequestDone(BaseModel):
    doc_id: int | None = None  # what the extension uploaded
    error: str | None = None  # why its try failed
    drop: bool = False  # given up


@router.get("/captures/requests")
def requests_list(
    request: Request, state: str = "waiting", limit: int = 20
) -> dict[str, Any]:
    """The captures the door could not fetch and the extension may:
    ``waiting`` (what it fetches next, oldest first), ``done``, ``failed``
    or ``dropped``."""
    if state not in ("waiting", "done", "failed", "dropped"):
        raise HTTPException(400, "state is waiting, done, failed or dropped")
    return {"requests": store.capture_requests(_con(request), state=state, limit=limit)}


@router.post("/captures/requests/{request_id}")
def requests_finish(
    request_id: int, req: RequestDone, request: Request
) -> dict[str, Any]:
    """What became of a request: the document the extension uploaded, a
    failed try (``error``; failed for good after a few), or given up."""
    con = _con(request)
    try:
        done = store.finish_request(
            con, request_id, doc_id=req.doc_id, error=req.error, drop=req.drop
        )
        if done["state"] == "done" and done["doc_id"]:
            # what the capture asked for comes with it: its title, its domains
            if done.get("title"):
                store.retitle(con, int(done["doc_id"]), done["title"], source="request")
            for d in done.get("domains") or []:
                store.add_domain(con, int(done["doc_id"]), d)
        return done
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'\"")) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/inbox")
def inbox_view(request: Request, limit: int = 50) -> dict[str, Any]:
    """The latest captures (uploads, sent pages, fetched URLs, dropped
    files) with their state, the drop folder, and the domains to choose."""
    return {
        "recent": inbox.recent(_con(request), limit=limit),
        # what waits for the browser extension to fetch it
        "requests": store.capture_requests(_con(request), state="waiting"),
        "inbox_dir": str(inbox.inbox_dir()),
        "modules": sorted(m for m in ontology.current().modules if m != ontology.CORE),
    }
