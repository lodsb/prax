"""The graph: link, traverse, the ontology, the review queue, entities,
the overview."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from prax import (
    ontology,
    review,
    store,
)

from ._base import _con

router = APIRouter()


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


@router.post("/link")
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


@router.get("/traverse")
def traverse(entity: str, request: Request, hops: int = 1) -> list[dict[str, Any]]:
    return store.traverse(_con(request), entity, hops)


@router.get("/ontology")
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


@router.get("/review")
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


@router.post("/review/bulk")
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


@router.post("/review/replay")
def review_replay(request: Request) -> dict[str, Any]:
    """Link the typed open items the current ontology now accepts."""
    rep = review.replay(_con(request))
    return rep.__dict__


@router.post("/review/{review_id}")
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


@router.get("/entities")
def entities(q: str, request: Request, limit: int = 20) -> list[dict[str, Any]]:
    """Entities whose name contains ``q``, most connected first."""
    return store.find_entities(_con(request), q, limit=limit)


@router.get("/graph/overview")
def graph_overview(
    request: Request, limit: int = 30, min_shared: int = 2
) -> dict[str, Any]:
    """The most connected concepts, methods, tools and datasets, the edges
    among them, and co-occurrence links (hubs sharing at least
    ``min_shared`` source documents): what the graph view opens on."""
    return store.hub_graph(_con(request), limit=limit, min_shared=min_shared)
