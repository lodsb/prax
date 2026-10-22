"""Where files lie (plan item AQ, stages 2 and 3;
``docs/media-by-reference.md``): a crawl's half (``prax_send.py
--places``: start, the batches of what it saw, the end), the files it
references rather than sends (``--reference``), and the reads of the
``places`` table. The administrator's alone, as a path can be as personal
as the file: none of these routes is in ``auth.RESTRICTED_ROUTES``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from prax import store
from prax.capture import inbox

from ._base import _con

router = APIRouter()


class CrawlReq(BaseModel):
    root: str
    prefix: str = ""


class PlacesReq(BaseModel):
    root: str
    entries: list[dict[str, Any]]


class CrawlEnd(BaseModel):
    root: str
    since: str
    prefix: str = ""


class ReferenceReq(BaseModel):
    root: str
    entries: list[dict[str, Any]]
    domains: list[str] | None = None
    tags: list[str] | None = None
    session: str | None = None
    by: str = "send"


@router.post("/places/start")
def start_crawl(req: CrawlReq) -> dict[str, Any]:
    """A crawl of ``root`` (or of its folder ``prefix``) begins: the door's
    own time, which the crawl hands back when it ends, so a NAS whose clock
    is off cannot mark the wrong files gone."""
    if not store.ROOT_NAME.fullmatch(req.root):
        raise HTTPException(400, f"root: a lower-case name, not {req.root!r}")
    return {"since": store.now(), "root": req.root, "prefix": req.prefix}


@router.post("/places")
def record_places(req: PlacesReq, request: Request) -> dict[str, Any]:
    """What a crawl saw at ``root``: each entry's ``path`` (relative, with
    ``/``), ``size``, ``mtime`` (seconds) and ``sha256``, at most
    ``store.KNOWN_BATCH`` a request."""
    if len(req.entries) > store.KNOWN_BATCH:
        raise HTTPException(413, f"at most {store.KNOWN_BATCH} entries a request")
    try:
        return store.record_places(_con(request), req.root, req.entries)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/ingest/reference")
def ingest_reference(req: ReferenceReq, request: Request) -> dict[str, Any]:
    """Files a crawl saw at ``root`` (``prax_send.py --places NAME
    --reference``), each as ``POST /places`` takes it with an optional
    ``mime``: every one a place, and each recording or picture whose hash
    is new a document without its bytes (``store.register_references``).
    At most ``store.KNOWN_BATCH`` a request."""
    if len(req.entries) > store.KNOWN_BATCH:
        raise HTTPException(413, f"at most {store.KNOWN_BATCH} entries a request")
    try:
        store._check_domains(req.domains or [])
        return inbox.ingest_references(
            _con(request),
            req.root,
            req.entries,
            domains=req.domains,
            tags=req.tags,
            session=req.session,
            by=req.by,
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/places/done")
def end_crawl(req: CrawlEnd, request: Request) -> dict[str, Any]:
    """A crawl that began at ``since`` (``/places/start``) is over: what it
    did not see under its root and prefix is gone."""
    try:
        return store.end_crawl(_con(request), req.root, req.since, req.prefix)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/places")
def places(
    request: Request,
    doc: int | None = None,
    hash: str | None = None,
    root: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    """Where a document's file, a hash's, or the file at a place lies, and
    its other copies: ``doc``, ``hash``, or ``root`` and ``path``."""
    if doc is None and not hash and not (root and path):
        raise HTTPException(400, "which file? doc, hash, or root and path")
    found = store.places_of(
        _con(request), digest=hash, doc_id=doc, root=root, path=path
    )
    if found is None:
        raise HTTPException(404, "no such file in any place")
    return found


@router.get("/places/single")
def single(
    request: Request, root: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """The files that lie at one place only and are not in the archive,
    largest first: what one disk's failure would lose."""
    return store.single_copies(_con(request), root=root, limit=min(limit, 500))


@router.get("/places/duplicates")
def duplicates(
    request: Request, root: str | None = None, min_size: int = 0, limit: int = 50
) -> dict[str, Any]:
    """The files that lie at several places, those whose extra copies take
    the most space first."""
    return store.duplicate_places(
        _con(request), root=root, min_size=min_size, limit=min(limit, 500)
    )
