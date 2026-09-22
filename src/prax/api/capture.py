"""Documents coming in: text, files, pages the browser rendered, URLs the
door fetches; retiring; the inbox view."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from prax import (
    inbox,
    ontology,
    store,
)

from ._base import _capture_out, _con, _split, max_upload

router = APIRouter()


class IngestText(BaseModel):
    text: str
    title: str | None = None
    source_url: str | None = None
    meta: dict[str, Any] | None = None
    domains: list[str] | None = None


@router.post("/ingest")
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
    return _capture_out(cap)


@router.post("/ingest/url")
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


@router.get("/inbox")
def inbox_view(request: Request, limit: int = 50) -> dict[str, Any]:
    """The latest captures (uploads, sent pages, fetched URLs, dropped
    files) with their state, the drop folder, and the domains to choose."""
    return {
        "recent": inbox.recent(_con(request), limit=limit),
        "inbox_dir": str(inbox.inbox_dir()),
        "modules": sorted(m for m in ontology.current().modules if m != ontology.CORE),
    }
