"""What is done to a document: its domains, the promote flag, the routes
from it, an extraction or a reading asked for, the readings queue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from prax import models, store, work
from prax.capture import routes
from prax.graph import ontology

from ._base import _con

router = APIRouter()


# ---------------------------------------------------------------- domains


class DomainsReq(BaseModel):
    domains: list[str] | None = None  # None: every module
    by: str = "human"  # "agent" from the MCP proxy


@router.get("/doc/{doc_id}/domains")
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


@router.put("/doc/{doc_id}/domains")
def put_domains(doc_id: int, req: DomainsReq, request: Request) -> dict[str, Any]:
    """Replace the document's domain set; null means every module.
    ``reread`` says the change left an extraction stale: the worker's
    next extract pass reads the document again against the new set."""
    try:
        con = _con(request)
        domains = store.set_domains(con, doc_id, req.domains, by=req.by)
        stale = store.get_meta(con, doc_id).get("extraction_stale") or {}
        return {"domains": domains, "reread": bool(stale.get("domains_changed"))}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/doc/{doc_id}/domains/{domain}")
def post_domain(doc_id: int, domain: str, request: Request) -> dict[str, Any]:
    try:
        return {"domains": store.add_domain(_con(request), doc_id, domain)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/doc/{doc_id}/domains/{domain}")
def delete_domain(doc_id: int, domain: str, request: Request) -> dict[str, Any]:
    try:
        return {"domains": store.remove_domain(_con(request), doc_id, domain)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


class DryRunReq(BaseModel):
    rules: list[dict[str, Any]] | None = None  # None: the rules in prax.yaml


@router.post("/domains/dry-run")
def domains_dry_run(req: DryRunReq, request: Request) -> dict[str, Any]:
    """What a set of domain rules would assign to the documents without a
    domain, written nowhere (stage Z, step 5). Without rules, the ones in
    prax.yaml (``domains:``)."""
    rules = (
        req.rules if req.rules is not None else list(models.load().get("domains") or [])
    )
    try:
        return store.domains_dry_run(_con(request), rules)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class AssignReq(BaseModel):
    ids: list[int]  # the documents, by id: never the whole library


@router.post("/domains/assign")
def domains_assign(req: AssignReq, request: Request) -> dict[str, Any]:
    """The rules in prax.yaml applied again to the named documents, over a
    set a rule gave them: what a changed rule needs for the documents it
    now places elsewhere. A set a person wrote stays. Returns the counts
    per rule, as the ``domains`` pass does."""
    rules = list(models.load().get("domains") or [])
    if not rules or not req.ids:
        return {"unmatched": 0}
    try:
        return store.assign_domains(_con(request), rules, force=True, ids=req.ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------- genres


@router.get("/genres")
def genres() -> dict[str, Any]:
    """What a document may be (``ontology/genres.yaml``): the levels and
    their genres, each with its line of description; and under
    ``subjects`` what it may be about (``ontology/subjects.yaml``), in the
    same shape."""
    return {**ontology.genres().as_dict(), "subjects": ontology.subjects().as_dict()}


@router.get("/documents/genre-sample")
def genre_sample(
    request: Request, state: str = "open", offset: int = 0, limit: int = 10
) -> dict[str, Any]:
    """The documents to label on the Review page's "genre" tab (``open``),
    a model's labels to check (``check``, and ``ruled`` for those a domain
    rule placed), or the ones a person labelled, the last first
    (``labelled``)."""
    try:
        return store.genre_sample(
            _con(request), state=state, offset=offset, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/genres/training")
def genre_training(request: Request) -> dict[str, Any]:
    """Every labelled document with the view a model reads
    (``writing.genres.view``), who labelled it and whether it is a blind
    label: what `scripts/train_labeller.py` trains and measures on."""
    from prax.writing import genres as gw

    rows = store.genre_training(_con(request))
    items = [
        {
            "id": r["id"],
            "view": gw.view(r["title"], r["meta"], r["text"], where=r["where"]),
            "by": r["by"],
            "blind": r["blind"],
            "g": r["g"],
            "s": r["s"],
        }
        for r in rows
    ]
    return {
        "genres": ontology.genres().labels(),
        "subjects": ontology.subjects().labels(),
        "versions": {
            "genres": ontology.genres().version,
            "subjects": ontology.subjects().version,
        },
        "items": items,
    }


class GenresReq(BaseModel):
    genres: list[str] | None = None  # None with skip false: take them back
    subjects: list[str] | None = None  # what it is about; may be none
    skip: bool = False  # the person could not place it
    by: str = "human"  # or the model that labelled it
    p: dict[str, float] | None = None  # a model's probability per label
    note: str | None = None  # a model's reason, in a line


@router.put("/doc/{doc_id}/genres")
def put_genres(doc_id: int, req: GenresReq, request: Request) -> dict[str, Any]:
    """A person's genres and subjects for a document: the gold sample of
    stage Z."""
    try:
        return store.set_genres(
            _con(request),
            doc_id,
            req.genres,
            subjects=req.subjects,
            skip=req.skip,
            by=req.by,
            p=req.p,
            note=req.note,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# --------------------------------------------------------------- promote


class PromoteReq(BaseModel):
    reason: str | None = None
    by: str = "human"


@router.post("/doc/{doc_id}/promote")
def promote_doc(doc_id: int, req: PromoteReq, request: Request) -> dict[str, Any]:
    """Flag a document for the expensive pass (the ``promote`` work step)."""
    try:
        return store.promote(_con(request), doc_id, by=req.by, reason=req.reason)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/doc/{doc_id}/promote")
def unpromote_doc(doc_id: int, request: Request) -> dict[str, bool]:
    return {"removed": store.unpromote(_con(request), doc_id)}


@router.get("/doc/{doc_id}/routes")
def doc_routes(doc_id: int, request: Request) -> dict[str, Any]:
    """The document's state and the routes from it: what has been done to
    its text, figures, formulas and graph, and what can be asked for,
    with the model each step resolves to on this host and whether it
    costs money (``prax.capture.routes``). The UI's "process…" dialog."""
    try:
        return routes.routes_for(_con(request), doc_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


class ExtractReq(BaseModel):
    by: str = "human"


@router.post("/doc/{doc_id}/extract")
def request_extraction(
    doc_id: int, req: ExtractReq, request: Request
) -> dict[str, Any]:
    """Ask for the document's graph to be read again by the extract
    step's model, before the backlog. The worker's next extract pass
    takes it, whatever its scope; the earlier reading's edges are
    retired, history kept."""
    con = _con(request)
    doc = store.get_document(con, doc_id, max_chars=0)
    if doc is None:
        raise HTTPException(404, "no such document")
    if doc.get("text_hash") is None:
        raise HTTPException(400, "no text to extract from yet")
    return store.request_extraction(con, doc_id, by=req.by)


@router.post("/doc/{doc_id}/rechunk")
def rechunk_doc(doc_id: int, request: Request) -> dict[str, Any]:
    """Chunk this document's text again, with the chunker as it stands.

    Chunks are disposable: the artifact and everything else stay as they
    are, and a chunk whose text did not change keeps its id and its
    vector. One document at a time is how a chunker change is tried
    before ``prax maintain --rechunk`` puts the library through it.
    """
    con = _con(request)
    doc = store.get_document(con, doc_id, max_chars=0)
    if doc is None:
        raise HTTPException(404, "no such document")
    if doc.get("text_hash") is None:
        raise HTTPException(400, "no text to chunk yet")
    return {"chunks": store.rechunk(con, doc_id)}


class ReadingReq(BaseModel):
    extractor: str  # one of store.READINGS
    mode: str | None = (
        None  # store.MODES: vision-pages scans|all, figures captioned|all, OCR a word
    )
    by: str = "human"


@router.post("/doc/{doc_id}/reading")
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
    bare_captions: bool = False  # captions with no picture: the crop pass
    read_formulas: bool = False  # the same for display equations
    unread_formulas: bool = False
    maths: float | None = None  # references to numbered equations per 10k characters
    limit: int | None = None
    dry_run: bool = False  # count, place nothing
    by: str = "human"


@router.post("/readings/bulk")
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
        or req.bare_captions
        or req.read_formulas
        or req.unread_formulas
        or req.maths is not None
    ):
        raise HTTPException(
            400,
            "a selection: ids, mime, text_source, title, unreadable, thin,"
            " doctype, unpolished, read_figures, unread_figures, bare_captions,"
            " read_formulas,"
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
            bare_captions=req.bare_captions,
            read_formulas=req.read_formulas,
            unread_formulas=req.unread_formulas,
            maths=req.maths,
            limit=req.limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        counts = store.request_readings(
            con, ids, req.extractor, mode=req.mode, by=req.by, dry_run=req.dry_run
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**counts, "dry_run": req.dry_run}


@router.delete("/doc/{doc_id}/reading")
def cancel_reading(doc_id: int, request: Request) -> dict[str, bool]:
    return {"removed": store.cancel_reading(_con(request), doc_id)}


@router.get("/readings")
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


@router.get("/promote")
def promote_view(request: Request, limit: int = 30) -> dict[str, Any]:
    """The flagged documents with their status under the promote step's
    model, and the candidates the library keeps coming back to."""
    step = models.describe("promote")
    producer = step["runtime"]
    return {
        "step": step,
        "waiting": work.who_runs(_con(request), "promote"),
        "promoted": store.promoted_documents(_con(request), producer=producer),
        "candidates": store.promotion_candidates(_con(request), limit=limit),
    }
