"""The importers' door: one Zotero item at a time (``prax import zotero``,
a client), and the citations pass as a job."""

from __future__ import annotations

import json
import logging
import threading
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from prax import store
from prax.graph import ontology

from ._base import _con

router = APIRouter()


@router.post("/import/zotero/item")
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


@router.post("/import/citations")
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
