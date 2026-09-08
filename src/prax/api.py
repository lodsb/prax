"""FastAPI app — the HTTP door. Thin wrappers over prax.store.

The store connection is opened in the app lifespan and shared by all
handlers; prax.store serializes access.
"""

from __future__ import annotations

import mimetypes
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

from . import auth, store

UI_DIR = Path(__file__).resolve().parent / "ui"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    con = store.connect()
    store.init_db(con)
    app.state.con = con
    try:
        yield
    finally:
        con.close()


app = FastAPI(title="prax", version="0.0.1", lifespan=_lifespan)
app.middleware("http")(auth.middleware)


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


class LinkReq(BaseModel):
    src: str
    src_type: str
    rel: str
    dst: str
    dst_type: str
    confidence: str = "EXTRACTED"
    source_doc: int | None = None
    ontology_version: str | None = None


@app.post("/ingest")
def ingest(req: IngestText, request: Request) -> dict[str, Any]:
    return store.ingest_text(
        request.app.state.con,
        req.text,
        title=req.title,
        source_url=req.source_url,
        meta=req.meta,
    )


@app.post("/ingest/file")
def ingest_file(
    request: Request,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    source_url: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    name = file.filename or ""
    mime = (
        file.content_type
        if file.content_type and file.content_type != "application/octet-stream"
        else mimetypes.guess_type(name)[0] or "application/octet-stream"
    )
    return store.ingest_file(
        request.app.state.con,
        file.file.read(),
        mime=mime,
        title=title or name or None,
        source_url=source_url,
        original_path=name or None,
    )


@app.get("/get/{doc_id}")
def get(
    doc_id: int, request: Request, offset: int = 0, max_chars: int | None = None
) -> dict[str, Any]:
    doc = store.get_document(
        request.app.state.con, doc_id, offset=offset, max_chars=max_chars
    )
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
) -> list[dict[str, Any]]:
    try:
        return store.search(
            request.app.state.con, q, limit, kind=kind, mode=mode, rerank=rerank
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/chunk/{chunk_id}")
def chunk(chunk_id: int, request: Request) -> dict[str, Any]:
    """One chunk in full, with its kind, heading path, locator and table data."""
    c = store.get_chunk(request.app.state.con, chunk_id)
    if c is None:
        raise HTTPException(404, "no such chunk")
    return c


@app.post("/link")
def link(req: LinkReq, request: Request) -> dict[str, int]:
    edge = store.Edge(req.src, req.src_type, req.rel, req.dst, req.dst_type)
    try:
        eid = store.link(
            request.app.state.con,
            edge,
            confidence=req.confidence,
            source_doc=req.source_doc,
            ontology_version=req.ontology_version,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"edge_id": eid}


@app.get("/traverse")
def traverse(entity: str, request: Request, hops: int = 1) -> list[dict[str, Any]]:
    return store.traverse(request.app.state.con, entity, hops)


# ------------------------------------------------------ browsing (the UI)


@app.get("/documents")
def documents(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    title: str | None = None,
    source: str | None = None,
    mime: str | None = None,
) -> dict[str, Any]:
    """Documents without text, newest first, filtered for browsing."""
    return store.list_documents(
        request.app.state.con,
        limit=limit,
        offset=offset,
        title=title,
        source=source,
        mime_prefix=mime,
    )


@app.get("/doc/{doc_id}/original")
def original(doc_id: int, request: Request) -> FileResponse:
    """The archived original with its MIME type, shown inline (a PDF opens
    in the browser's viewer; ``#page=N`` selects a page)."""
    info = store.original_info(request.app.state.con, doc_id)
    if info is None or not info["path"].exists():
        raise HTTPException(404, "no such document")
    name = Path(info["original_path"] or info["title"] or f"document-{doc_id}").name
    return FileResponse(
        info["path"],
        media_type=info["mime"],
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"},
    )


@app.get("/doc/{doc_id}/text")
def text(doc_id: int, request: Request) -> PlainTextResponse:
    """The Markdown text artifact of a document."""
    doc = store.get_document(request.app.state.con, doc_id)
    if doc is None:
        raise HTTPException(404, "no such document")
    return PlainTextResponse(doc["text"], media_type="text/markdown; charset=utf-8")


@app.get("/doc/{doc_id}/chunks")
def chunks(doc_id: int, request: Request) -> list[dict[str, Any]]:
    """The document as its chunks in order, with kind, heading, page, text."""
    try:
        store.get_meta(request.app.state.con, doc_id)
    except KeyError as exc:
        raise HTTPException(404, "no such document") from exc
    return store.list_chunks(request.app.state.con, doc_id)


@app.get("/entities")
def entities(q: str, request: Request, limit: int = 20) -> list[dict[str, Any]]:
    """Entities whose name contains ``q``, most connected first."""
    return store.find_entities(request.app.state.con, q, limit=limit)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")
