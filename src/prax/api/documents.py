"""Reading documents: get, search, a chunk, the listing, the original,
a figure, the text, the chunks, the context."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    PlainTextResponse,
    Response,
)
from pydantic import BaseModel

from prax import (
    store,
)

from ._base import _con, ui_url

router = APIRouter()


@router.get("/get/{doc_id}")
def get(
    doc_id: int,
    request: Request,
    offset: int = 0,
    max_chars: int | None = None,
    brief: bool = False,
) -> dict[str, Any]:
    """One document and a window of its text; ``brief`` is what an agent
    reads (``store.brief_document``: the meta without its histories)."""
    con = _con(request)
    doc = store.get_document(con, doc_id, offset=offset, max_chars=max_chars)
    if doc is None:
        raise HTTPException(404, "no such document")
    # what it is waiting to be read by: a list since migration 21, and
    # `meta.reading` is the last reading that finished
    doc["pending"] = store.pending_readings(con, doc_id)
    doc["url"] = ui_url(request, doc_id)
    return store.brief_document(doc) if brief else doc


@router.get("/search")
def search(
    q: str,
    request: Request,
    limit: int = 10,
    kind: str | None = None,
    mode: str = "hybrid",
    rerank: bool | None = None,
    doctype: str | None = None,
    domain: str | None = None,
    regions: bool = False,
    brief: bool = False,
    published_since: str | None = None,
    published_before: str | None = None,
    include_stale: bool = False,
) -> list[dict[str, Any]]:
    """The hits; with ``regions`` first, when most of the first hits'
    entities live in one region of the library, that region and its part
    as an item of their own (``kind: "region"``): where the results are.
    ``brief`` leaves out the ranks and the empty fields (``store.brief_hit``),
    which an agent does not read; without it each hit says its
    ``domains`` (None: every module). Each hit says when its document was
    published; ``published_since``/``published_before`` keep a span. A
    document no longer current carries ``stale`` and ranks lower, unless
    ``include_stale``. ``brief`` (an agent) adds each passage's ``cite``,
    a link that survives a re-chunk."""
    timing: dict[str, float] = {}
    request.state.detail = timing  # the slow-request log says which side took long
    con = _con(request)
    try:
        hits = store.search(
            con,
            q,
            limit,
            kind=kind,
            mode=mode,
            rerank=rerank,
            doctype=doctype,
            domain=domain,
            timing=timing,
            published_since=published_since,
            published_before=published_before,
            include_stale=include_stale,
            cite=brief,  # the agent's shape carries the passage's link
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if brief:
        hits = [store.brief_hit(h) for h in hits]
    else:  # the UI shows each hit's domains; an agent asks with domain=
        sets = store.domains_of(con, [int(h["doc_id"]) for h in hits])
        for h in hits:
            h["domains"] = sets.get(int(h["doc_id"]))
    if regions and hits:
        first = [int(h["doc_id"]) for h in hits[: store.REGION_HITS]]
        where = store.regions_of(con, first)
        if where is not None:
            return [where, *hits]
    return hits


@router.get("/chunk/{chunk_id}")
def chunk(chunk_id: int, request: Request) -> dict[str, Any]:
    """One chunk in full, with its kind, heading path, locator and table data."""
    c = store.get_chunk(_con(request), chunk_id)
    if c is None:
        raise HTTPException(404, "no such chunk")
    return c


# ------------------------------------------------------ browsing (the UI)


@router.get("/documents")
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
    genre: str | None = None,
    subject: str | None = None,
    since: str | None = None,
    published_since: str | None = None,
    published_before: str | None = None,
) -> dict[str, Any]:
    """Documents without text, newest first, filtered for browsing;
    ``domain`` keeps one ontology module's documents and those of the
    modules built on it (``unassigned``: the documents no module was set
    for), ``tag`` the documents carrying a tag (``project:synth``),
    ``genre`` and ``subject`` the documents labelled so (stage Z),
    ``since`` those added at that date or moment (UTC) or later."""
    try:
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
            genre=genre or None,
            subject=subject or None,
            since=since or None,
            published_since=published_since or None,
            published_before=published_before or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/doc/{doc_id}/original")
def original(doc_id: int, request: Request) -> FileResponse:
    """The archived original with its MIME type, shown inline (a PDF opens
    in the browser's viewer; ``#page=N`` selects a page)."""
    info = store.original_info(_con(request), doc_id)
    if info is None or not info["path"].exists():
        raise HTTPException(404, "no such document")
    name = Path(info["original_path"] or info["title"] or f"document-{doc_id}").name
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}",
        "X-Content-Type-Options": "nosniff",
    }
    if (info["mime"] or "").split(";")[0] not in INERT_TYPES:
        # a captured page, an SVG, an XML document: somebody else's content
        # rendered from this origin, so no scripts, no forms, no access to
        # the door's cookies, and nothing fetched from elsewhere
        headers["Content-Security-Policy"] = SANDBOX_POLICY
    return FileResponse(info["path"], media_type=info["mime"], headers=headers)


# what a browser renders without running anything: the PDF viewer and the
# raster image types. Everything else served out of the archive is sandboxed.
INERT_TYPES = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}
)
SANDBOX_POLICY = "sandbox; default-src data: 'unsafe-inline'"


@router.get("/doc/{doc_id}/figure/{ref}")
def figure(doc_id: int, ref: str, request: Request) -> Response:
    """A figure's bytes out of the document's original, by the hash the
    text references (``![caption](figure:<sha256>)``); immutable, so
    cached for good."""
    from prax.parsers import figures

    con = _con(request)
    info = store.original_info(con, doc_id)
    if info is None or not info["path"].exists():
        raise HTTPException(404, "no such document")
    if not store.document_has_figure(con, doc_id, ref):
        # the archive is addressed by hash; a document shows only the
        # figures its own text references (security audit, item 8)
        raise HTTPException(404, "no such figure in this document")
    # the archive first: a filed picture (a scanned page) is its own
    # artifact, and looking for it in the original means extracting
    # every image of a 200-page scan — the figure strip asked 500 times
    found = store.figure_blob(ref) or figures.find(info["path"].read_bytes(), ref)
    if found is None:
        raise HTTPException(404, "no such figure in the original")
    data, media = found
    return Response(
        data,
        media_type=media,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            # an <img> ignores this; a tab opened on the figure's URL does not
            "Content-Security-Policy": SANDBOX_POLICY,
        },
    )


@router.get("/doc/{doc_id}/text")
def text(doc_id: int, request: Request) -> PlainTextResponse:
    """The Markdown text artifact of a document."""
    doc = store.get_document(_con(request), doc_id)
    if doc is None:
        raise HTTPException(404, "no such document")
    return PlainTextResponse(
        doc["text"],
        media_type="text/markdown; charset=utf-8",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/doc/{doc_id}/chunks")
def chunks(doc_id: int, request: Request) -> list[dict[str, Any]]:
    """The document as its chunks in order, with kind, heading, page, text."""
    try:
        store.get_meta(_con(request), doc_id)
    except KeyError as exc:
        raise HTTPException(404, "no such document") from exc
    return store.list_chunks(_con(request), doc_id)


REFERENCES_SHOWN = 50  # entries an answer lists unless asked for more
REFERENCES_MAX = 300


@router.get("/doc/{doc_id}/references")
def doc_references(
    doc_id: int, request: Request, limit: int = REFERENCES_SHOWN, offset: int = 0
) -> dict[str, Any]:
    """A paper's reference list, an entry each: what it names, the library
    document it cites (``in_library``) or ``links`` to read it elsewhere
    (``store.references_of``). ``limit`` entries from ``offset``: a book's
    list ran to 7,249 entries and 2 MB in one answer (invariant 6); the
    counts are of the whole list, ``left_out`` what this page did not
    carry."""
    con = _con(request)
    if store.get_document(con, doc_id, max_chars=0) is None:
        raise HTTPException(404, "no such document")
    refs = store.references_of(con, doc_id)
    limit = max(1, min(int(limit), REFERENCES_MAX))
    offset = max(0, int(offset))
    shown = refs[offset : offset + limit]
    return {
        "doc_id": doc_id,
        "entries": len(refs),
        "in_library": sum(1 for r in refs if r.get("in_library")),
        "offset": offset,
        "references": shown,
        "left_out": len(refs) - len(shown),
    }


class MissingReq(BaseModel):
    doc_ids: list[int] | None = None
    tag: str | None = None
    project: str | None = None
    page: str | None = None  # the documents a page links
    limit: int = 30


@router.post("/references/missing")
def references_missing(req: MissingReq, request: Request) -> dict[str, Any]:
    """What a set of documents cites that the library lacks, ranked by how
    many of them cite it (``store.cited_but_missing``). The set is
    ``doc_ids``, a ``tag``, a ``project``'s documents or what a ``page``
    links; several are joined."""
    con = _con(request)
    ids: list[int] = list(req.doc_ids or [])
    if req.tag or req.project:
        ids += store.seed_documents(con, project=req.project, tag=req.tag)
    if req.page:
        page = store.get_page(con, store.slugify(req.page))
        if page is None:
            raise HTTPException(404, f"no page {req.page!r}")
        ids += store.linked_documents(page["text"])
    if not ids:
        raise HTTPException(400, "name the set: doc_ids, a tag, a project or a page")
    limit = max(1, min(req.limit, 200))
    return store.cited_but_missing(con, ids, limit=limit)


@router.get("/doc/{doc_id}/context")
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
    return {**ctx, "url": ui_url(request, doc_id)}
