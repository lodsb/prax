"""The graph: link, traverse, the ontology, the review queue, entities,
the overview."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from prax import store
from prax.graph import ontology, review

from ._base import _con

router = APIRouter()


class LinkReq(BaseModel):
    src: str  # a name, or doc:N for a library document
    src_type: str | None = None  # needed for a name; doc:N brings its own
    rel: str
    dst: str
    dst_type: str | None = None
    confidence: str = "EXTRACTED"
    source_doc: int | None = None
    evidence: str | None = None  # a quote from source_doc, never a chunk id
    ontology_version: str | None = None
    producer: str = "manual"  # "agent" from the MCP proxy
    world_from: str | None = None  # when the fact holds in the world
    world_to: str | None = None  # a date, or "unknown"


_DOC_REF = re.compile(r"doc:(\d+)")
# what a chunk id looks like: evidence is the words, so a re-chunk that
# gives the id to another passage cannot move it (AL step 2)
_CHUNK_ID = re.compile(r"\s*(#?doc/\d+/\d+|chunk[\s:#/]*\d+|\d+)\s*", re.IGNORECASE)


def _end(con: Any, name: str, etype: str | None, side: str) -> tuple[str, str]:
    """One end of an edge: ``doc:N`` is the document's node (its title and
    type, ``store.document_node``); a name needs its type."""
    m = _DOC_REF.fullmatch(name.strip())
    if m:
        try:
            return store.document_node(con, int(m.group(1)))
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from exc
    if not etype:
        raise HTTPException(400, f"{side}_type is needed for a name ({name!r})")
    return name, etype


@router.post("/link")
def link(req: LinkReq, request: Request) -> dict[str, int]:
    """One edge. Either end may be ``doc:N``, a library document, which
    brings its own name and type; ``evidence`` is a quote, and a chunk id
    in its place is refused."""
    con = _con(request)
    if req.evidence is not None and _CHUNK_ID.fullmatch(req.evidence):
        raise HTTPException(400, "evidence is a quote from the source, not a chunk id")
    src, src_type = _end(con, req.src, req.src_type, "src")
    dst, dst_type = _end(con, req.dst, req.dst_type, "dst")
    edge = store.Edge(src, src_type, req.rel, dst, dst_type)
    try:
        eid = store.link(
            con,
            edge,
            confidence=req.confidence,
            source_doc=req.source_doc,
            evidence=req.evidence,
            ontology_version=req.ontology_version,
            producer=req.producer,
            world_from=req.world_from,
            world_to=req.world_to,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"edge_id": eid}


@router.get("/traverse")
def traverse(
    entity: str,
    request: Request,
    hops: int = 1,
    limit: int | None = None,
    type: str | None = None,
    domain: str | None = None,
    as_of: str | None = None,
    world_at: str | None = None,
    page: int | None = None,
    personal: str | None = None,
) -> dict[str, Any]:
    """The neighbourhood of an entity: the edges, and what was left out.

    The second hop is a map rather than every edge in it, so the answer
    says how many neighbours it did not carry (`store._second_hop`). A
    name that reaches several things walks one, the one of ``type`` or
    else the most connected, and ``senses`` names them all. ``domain``
    keeps what that module's documents say. ``as_of`` (a date or a UTC
    moment) walks the edges prax held then; ``world_at`` (a year, month
    or day) the facts that hold in the world then, and the undated ones.
    ``page`` (a page's document id) and ``personal`` (hide, only) narrow
    the walk to the documents ``store.scope_documents`` names.
    """
    try:
        con = _con(request)
        within = store.scope_documents(con, page=page, personal=personal or None)
        return store.traverse_map(
            _con(request),
            entity,
            hops,
            limit,
            type=type,
            domain=domain or None,
            as_of=as_of or None,
            world_at=world_at or None,
            within=within,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/graph/changes")
def changes(
    since: str,
    request: Request,
    until: str | None = None,
    world: bool = False,
    entity: str | None = None,
    rel: str | None = None,
    domain: str | None = None,
    derived: bool = False,
    rereadings: bool = False,
    corrections: bool = False,
    limit: int = store.CHANGES_SHOWN,
) -> dict[str, Any]:
    """What changed in a period (``store.changes``): on record time the
    facts prax wrote and ended in it, on world time (``world``) the facts
    that began and ended in it as their sources state; counts by relation
    and the newest facts of each side. Re-readings are left out unless
    ``rereadings``, a repair's corrections unless ``corrections``."""
    try:
        return store.changes(
            _con(request),
            since,
            until or None,
            world=world,
            entity=entity or None,
            rel=rel or None,
            domain=domain or None,
            derived=derived,
            rereadings=rereadings,
            corrections=corrections,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/graph/connect")
def connect(
    a: str,
    b: str,
    request: Request,
    type_a: str | None = None,
    type_b: str | None = None,
    max_hops: int = 4,
    relations: str | None = None,
    as_of: str | None = None,
    world_at: str | None = None,
    weak: bool = False,
) -> dict[str, Any]:
    """How ``a`` is connected to ``b`` (``store.connect_entities``): the
    best sound paths, each hop with its relation, documents and evidence;
    ``relations`` a comma-separated set a path may use; ``weak`` shows the
    paths past the line too; ``world_at`` walks the facts that hold in the
    world at that date, and the undated ones."""
    try:
        return store.connect_entities(
            _con(request),
            a,
            b,
            type_a=type_a or None,
            type_b=type_b or None,
            max_hops=max_hops,
            relations=[r.strip() for r in relations.split(",") if r.strip()]
            if relations
            else None,
            as_of=as_of or None,
            world_at=world_at or None,
            weak=weak,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/edge/{edge_id}/why")
def edge_why(edge_id: int, request: Request) -> dict[str, Any]:
    """What a derived edge follows from: its premises, in the order of the
    chain (``store.edge_premises``); empty for an asserted edge. And the
    facts it disagrees with (``conflicts``, ``store.edge_conflicts``), and
    a repair's correction: the edge that corrects it (``corrected_by``)
    or those it corrects (``corrects``). And where its quote stood in the
    text it was read from, and whether it stands there still (``place``,
    ``store.evidence_place``)."""
    con = _con(request)
    if store.edge_hidden(con, edge_id):
        # as for an edge that is not there (the wall: existence too)
        return {"edge_id": edge_id, "premises": []}
    out: dict[str, Any] = {
        "edge_id": edge_id,
        "premises": store.edge_premises(con, edge_id),
    }
    conflicts = store.edge_conflicts(con, edge_id)
    if conflicts:
        out["conflicts"] = conflicts
    # a repair's correction, either way round (migration 46)
    out.update(store.corrections_of(con, edge_id))
    place = store.evidence_place(con, edge_id)
    if place is not None:
        out["place"] = place
    return out


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


@router.get("/review/drops")
def review_drops(request: Request) -> dict[str, Any]:
    """What the extractions left out without a review item, by reason,
    over the library (``store.extraction_drops``)."""
    return store.extraction_drops(_con(request))


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


class ReopenReq(BaseModel):
    ids: list[int]
    resolution: str = "dropped"


@router.post("/review/reopen")
def review_reopen(req: ReopenReq, request: Request) -> dict[str, int]:
    """Open again review items closed as ``resolution`` (``dropped`` by
    default): a drop by mistake undone. The administrator's only."""
    return {
        "reopened": store.reopen_reviews(
            _con(request), req.ids, resolution=req.resolution
        )
    }


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
    request: Request,
    limit: int = 30,
    min_shared: int = 2,
    domain: str | None = None,
    page: int | None = None,
    personal: str | None = None,
    world_at: str | None = None,
) -> dict[str, Any]:
    """The most connected concepts, methods, tools and datasets, the edges
    among them, and co-occurrence links (hubs sharing at least
    ``min_shared`` source documents): what the graph view opens on.
    ``domain`` draws one module's graph, from its documents alone; ``page``
    (a page's tree and what it links) and ``personal`` (hide, only) narrow
    it to the documents ``store.scope_documents`` names; ``world_at`` keeps
    the facts that hold in the world at that date and the undated ones."""
    con = _con(request)
    try:
        within = store.scope_documents(con, page=page, personal=personal or None)
        return store.hub_graph(
            con,
            limit=limit,
            min_shared=min_shared,
            domain=domain or None,
            within=within,
            world_at=world_at or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class MarksReq(BaseModel):
    things: list[tuple[str, str]]
    page: int | None = None
    personal: str | None = None


@router.post("/graph/marks")
def graph_marks(req: MarksReq, request: Request) -> dict[str, Any]:
    """For things the graph view has drawn (``[name, type]`` pairs), how
    many documents say something of each and its region
    (``store.entity_marks``), so an expansion is sized and coloured as the
    overview's hubs are."""
    con = _con(request)
    try:
        within = store.scope_documents(
            con, page=req.page, personal=req.personal or None
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"marks": store.entity_marks(con, req.things, within=within)}


@router.get("/graph/documents")
def graph_documents(ids: str, request: Request) -> dict[str, Any]:
    """The titles of documents the graph view hangs off a node
    (comma-separated ``ids``, at most 50; hidden ones left out)."""
    try:
        wanted = [int(i) for i in ids.split(",") if i.strip()][:50]
    except ValueError as exc:
        raise HTTPException(400, "ids: document ids separated by commas") from exc
    return {"titles": store.document_titles(_con(request), wanted)}


@router.get("/communities/map")
def communities_map(request: Request, limit: int = 40) -> dict[str, Any]:
    """The regions as one picture: the largest as nodes and how many facts
    reach between each two (``store.region_map``)."""
    return store.region_map(_con(request), limit=max(1, min(limit, 120)))


@router.get("/communities/{cid}/graph")
def community_graph(cid: int, request: Request, members: int = 40) -> dict[str, Any]:
    """A region drawn: its members that weigh most, the facts among them,
    and which of them share documents (``store.hub_graph``)."""
    con = _con(request)
    got = store.community(con, cid, members=max(1, min(members, 120)), documents=1)
    if got is None:
        raise HTTPException(404, "no such region")
    ids = [int(m["id"]) for m in got["members"]]
    return store.hub_graph(con, entities=ids, min_shared=2)


@router.get("/communities")
def communities(
    request: Request, level: int = 0, parent: int | None = None, limit: int = 100
) -> dict[str, Any]:
    """The regions of the library (level 0) or their parts (level 1, a
    region's with ``parent``), largest first: label, size, the summary's
    first sentence, the members that weigh most."""
    return {
        "communities": store.list_communities(
            _con(request), level=level, parent=parent, limit=limit
        )
    }


@router.get("/communities/{cid}")
def community(
    cid: int, request: Request, members: int = 30, documents: int = 10
) -> dict[str, Any]:
    """One region or part: its summary, members, parts and the documents
    that name most of its members."""
    got = store.community(_con(request), cid, members=members, documents=documents)
    if got is None:
        raise HTTPException(404, "no such community")
    return got


@router.get("/graph/document/{doc_id}")
def graph_document(doc_id: int, request: Request) -> dict[str, Any]:
    """What one document says, as edges the graph view draws."""
    return {"doc_id": doc_id, "edges": store.document_edges(_con(request), doc_id)}


# ------------------------------------------------ the lists a person decides


@router.get("/graph/candidates")
def graph_candidates(
    request: Request, type: str | None = None, offset: int = 0, limit: int = 30
) -> dict[str, Any]:
    """The likely pairs nobody has decided, closest names first, each side
    with its edges and a document naming it (the review page's "same
    thing?" list)."""
    return store.candidates_page(
        _con(request), etype=type or None, offset=offset, limit=limit
    )


@router.get("/graph/sameness")
def graph_sameness() -> dict[str, Any]:
    """What "the same thing" means (``ontology/sameness.yaml``): the rule
    the models are asked with, for the person deciding the same pairs."""
    from prax.graph import ontology

    return ontology.sameness().as_dict()


class DecideReq(BaseModel):
    keep: int  # the one that stays, when they are the same
    other: int
    same: bool
    across_types: bool = False  # a split name: a tool and a method one thing
    # who decided, when it is not the person at the page: a model that
    # judged the pair signs with its name, so its answers never join the
    # person's, which are the gold sample the judge is calibrated on
    by: str | None = None


@router.post("/graph/decide")
def graph_decide(req: DecideReq, request: Request) -> dict[str, Any]:
    """A person's answer to "are these one thing?" (``store.decide_pair``):
    same merges ``other`` into ``keep`` under a run of its own; either way
    the pair is recorded, signed, and not asked again. ``by`` signs a
    model's answer instead (never "human")."""
    by = (req.by or "").strip()
    if req.by is not None and (not by or by == store.PERSON):
        raise HTTPException(400, f"by: a model's name, not {req.by!r}")
    try:
        return store.decide_pair(
            _con(request),
            req.keep,
            req.other,
            same=req.same,
            across_types=req.across_types,
            **({"by": by} if by else {}),
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/graph/split-names")
def graph_split_names(
    request: Request, offset: int = 0, limit: int = 30
) -> dict[str, Any]:
    """Names held by things of unrelated types that nobody has settled,
    each part with its edges and a document naming it."""
    return store.split_names_page(_con(request), offset=offset, limit=limit)


@router.get("/graph/merges")
def graph_merges(request: Request, offset: int = 0, limit: int = 30) -> dict[str, Any]:
    """Merges worth a second look: one word apart, or a name folded into a
    narrower one (``store.merges_page``)."""
    return store.merges_page(_con(request), offset=offset, limit=limit)


class UndecideReq(BaseModel):
    keep: int
    other: int
    run: str | None = None  # what the decision merged, taken back first


@router.post("/graph/undecide")
def graph_undecide(req: UndecideReq, request: Request) -> dict[str, Any]:
    """Take a decision back (a click undone): its merge, when it made one,
    and its record, so a mistaken click does not stay a label."""
    con = _con(request)
    back = store.unmerge_run(con, req.run) if req.run else 0
    return {"undone": store.undecide_pair(con, req.keep, req.other), "entities": back}


@router.get("/graph/export")
def graph_export(
    request: Request,
    project: str | None = None,
    domain: str | None = None,
    tag: str | None = None,
    entity: str | None = None,
    type: str | None = None,
    hops: int = 1,
    history: bool = False,
) -> Response:
    """A piece of the graph as a file (``prax.graph.graphio``): what a project,
    a domain, a tag or an entity reaches, as JSON lines. Built whole here:
    the request's connection belongs to its thread, and a streamed body is
    read on another."""
    from prax.graph import graphio

    seed = graphio.Seed(
        project=project,
        domain=domain,
        tag=tag,
        entity=entity,
        type=type,
        hops=hops,
        history=history,
    )
    try:
        body = "".join(graphio.export(_con(request), seed))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    name = project or domain or tag or entity or "graph"
    return Response(
        body,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{name}.graph.jsonl"'},
    )


@router.post("/graph/import")
async def graph_import(
    request: Request, source: str, dry_run: bool = False
) -> dict[str, Any]:
    """An export read into this library as ``import:<source>`` (the body is
    the file). A dry run says what would happen and writes nothing."""
    from dataclasses import asdict

    from prax.graph import graphio

    from ._base import max_upload

    body = await request.body()
    if len(body) > max_upload():
        raise HTTPException(413, "file over door.max_upload_mb")
    lines = body.decode("utf-8").splitlines()

    def run() -> dict[str, Any]:
        rep = graphio.import_lines(_con(request), lines, source=source, dry_run=dry_run)
        return asdict(rep)

    import anyio

    try:
        return await anyio.to_thread.run_sync(run)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class UnmergeEntityReq(BaseModel):
    entity: int  # the merged entity that is a thing of its own


@router.post("/graph/unmerge-entity")
def graph_unmerge_entity(req: UnmergeEntityReq, request: Request) -> dict[str, Any]:
    """Take one merge back (``store.unmerge_entity``) and record the pair as
    different; for a merge no run can undo alone."""
    try:
        return store.unmerge_entity(_con(request), req.entity)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/graph/events")
def graph_events(
    request: Request, state: str = "open", offset: int = 0, limit: int = 20
) -> dict[str, Any]:
    """Groups of photos taken close together, for a person to name as an
    event or set aside (``store.event_candidates_page``)."""
    try:
        return store.event_candidates_page(
            _con(request),
            state=state,
            offset=max(0, offset),
            limit=max(1, min(limit, 100)),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/graph/events/find")
def graph_events_find(request: Request) -> dict[str, Any]:
    """The groups found again now, as the nightly's ``events`` pass does."""
    return store.find_event_candidates(_con(request))


class EventReq(BaseModel):
    event: bool
    name: str | None = None


@router.post("/graph/events/{candidate_id}")
def graph_event_decide(
    candidate_id: int, req: EventReq, request: Request
) -> dict[str, Any]:
    """A person's answer: an event called ``name``, or not one
    (``store.decide_event``)."""
    try:
        return store.decide_event(
            _con(request), candidate_id, event=req.event, name=req.name
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/graph/events/{candidate_id}/undo")
def graph_event_undo(candidate_id: int, request: Request) -> dict[str, Any]:
    """A decision taken back: its facts ended, the group open again."""
    try:
        return store.undo_event(_con(request), candidate_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/graph/versions")
def graph_versions(
    request: Request, verdict: str | None = None, offset: int = 0, limit: int = 20
) -> dict[str, Any]:
    """Pairs of documents that are probably versions of one work or one
    text twice, the most alike first, for a person to answer
    (``store.version_candidates``); answered ones with ``verdict``."""
    try:
        return store.version_candidates(
            _con(request),
            verdict=verdict or None,
            offset=max(0, offset),
            limit=max(1, min(limit, store.VERSIONS_PAGE)),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/graph/versions/find")
def graph_versions_find(request: Request) -> dict[str, Any]:
    """The pairs found again now, as the nightly's ``versions`` pass does."""
    return store.find_versions(_con(request))


class VersionReq(BaseModel):
    verdict: str


@router.post("/graph/versions/{candidate_id}")
def graph_version_decide(
    candidate_id: int, req: VersionReq, request: Request
) -> dict[str, Any]:
    """A person's answer: ``version``, ``copy`` or ``different``
    (``store.decide_version``)."""
    try:
        return store.decide_version(_con(request), candidate_id, req.verdict)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/graph/versions/{candidate_id}/undo")
def graph_version_undo(candidate_id: int, request: Request) -> dict[str, Any]:
    """An answer taken back: its facts ended, a copy back, the pair open."""
    try:
        return store.undo_version(_con(request), candidate_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/graph/events/{candidate_id}/timeline")
def graph_event_timeline(candidate_id: int, request: Request) -> dict[str, Any]:
    """A group's photos as its bursts, where a cut may go
    (``store.event_timeline``)."""
    try:
        return store.event_timeline(_con(request), candidate_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


class EventCutReq(BaseModel):
    at: str


@router.post("/graph/events/{candidate_id}/split")
def graph_event_split(
    candidate_id: int, req: EventCutReq, request: Request
) -> dict[str, Any]:
    """A group split before the photos taken at ``at``, kept for every
    pass after (``store.split_event``)."""
    try:
        return store.split_event(_con(request), candidate_id, at=req.at)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/graph/events/{candidate_id}/join")
def graph_event_join(candidate_id: int, request: Request) -> dict[str, Any]:
    """A cut taken back: the group joins the one before it
    (``store.join_event``)."""
    try:
        return store.join_event(_con(request), candidate_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/people")
def people(request: Request, q: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Persons, the most photographed and connected first; ``q`` narrows
    them by name (``store.people``)."""
    return store.people(_con(request), q=q, limit=limit)


@router.get("/people/{entity_id}")
def person(entity_id: int, request: Request) -> dict[str, Any]:
    """A person's page: facts, documents, and what the pictures that depict
    them say (``store.person_page``)."""
    try:
        return store.person_page(_con(request), entity_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class PersonRenameReq(BaseModel):
    name: str


class PersonSameReq(BaseModel):
    other: int  # the entity that is this person too; it folds into this one


@router.post("/people/{entity_id}/rename")
def person_rename(
    entity_id: int, req: PersonRenameReq, request: Request
) -> dict[str, Any]:
    """A person called something else, the old name kept as what they are
    also known by (``store.rename_person``); ``POST /graph/unmerge`` with
    the run takes it back. Not a named token's route."""
    try:
        return store.rename_person(_con(request), entity_id, req.name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/people/{entity_id}/same")
def person_same(entity_id: int, req: PersonSameReq, request: Request) -> dict[str, Any]:
    """Another entity is this person too, and folds into them
    (``store.merge_people``); ``POST /graph/unmerge`` with the run takes it
    back. Not a named token's route."""
    try:
        return store.merge_people(_con(request), req.other, entity_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
