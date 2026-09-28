"""The work protocol (hand out, take in), the door's jobs (heal, maintain,
backup, resolve, the importers), and what the door says about itself."""

from __future__ import annotations

import json
import logging
import socket
import threading
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from prax import models, store, work
from prax.graph import ontology
from prax.host import hostinfo, schedule
from prax.ml import embeddings

from ._base import _con, _split

router = APIRouter()


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
    renew: dict[str, Any] | None = None  # {"step", "items"}: the leases still held


@router.post("/work/session")
def work_session(req: WorkSessionReq, request: Request) -> dict[str, Any]:
    """A worker announces itself: a job row the Jobs view shows, with the
    worker's heartbeats."""
    job_id = store.job_start(
        _con(request), req.name, note=req.note, host=req.host, pid=req.pid or 0
    )
    return {"job_id": job_id, "leases": work.leases()}


@router.post("/work/session/{job_id}")
def work_beat(job_id: int, req: SessionBeat, request: Request) -> dict[str, Any]:
    con = _con(request)
    if req.status in ("done", "failed"):
        store.job_finish(con, job_id, status=req.status, note=req.note)
    else:
        store.job_update(con, job_id, done=req.done, total=req.total, note=req.note)
    renewed = 0
    if req.renew and req.renew.get("items"):
        renewed = work.renew(
            str(req.renew.get("step") or ""),
            [int(i) for i in req.renew["items"]],
            _worker(request),
        )
    return {"ok": True, "renewed": renewed}


@router.get("/work/demand")
def work_demand(request: Request) -> dict[str, Any]:
    """What waits for a role that has to be running to do it (``prax.work``
    ``ROLE_WORK``): the reading requests per extractor and per role. The
    supervisor asks this to know when a borrowed card can go back, and
    the Jobs view shows it beside the roles."""
    return work.demand(_con(request))


@router.get("/work/{step}")
def work_out(
    step: str, request: Request, limit: int = 10, scope: str = "captures"
) -> dict[str, Any]:
    """A leased batch of work: parse, titles, extract, embed, or the
    names of an entity type to resolve."""
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
    type: str | None = None  # resolve: the entity type the pairs are of
    pairs: list[list[Any]] | None = None  # resolve: [a, b, cosine]
    items: list[dict[str, Any]] | None = None  # adjudicate: the pairs handed out
    # adjudicate: one decision per item, None for a pair left to a person,
    # and a local model's calibrated probability per item
    same: list[bool | None] | None = None
    p: list[float | None] | None = None


@router.post("/work/{step}")
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


@router.post("/vectors/merge")
def vectors_merge() -> dict[str, Any]:
    """Fold the delta indexes into the main files now."""
    emb = embeddings.current()
    if emb is None or not store.vectors_available():
        raise HTTPException(400, "no embedder or no usearch")
    return store.merge_vectors(emb.name)


@router.post("/vectors/adopt")
def vectors_adopt(request: Request, model: str) -> dict[str, Any]:
    """Record that a model's index already holds these vectors.

    The way back from an embedder change. `chunk_embeddings` remembers
    one model per chunk, so re-embedding overwrote the record that the
    old model's vectors exist — but the vectors are still in its index
    file. Put `embeddings.model` back, restart the door, and ask for
    this: bookkeeping rather than compute.

    A job on a thread of its own; poll ``GET /jobs/{id}``. It was a
    synchronous write until 2026-09-26, which held the store's lock for
    minutes and wedged the door.
    """
    if not store.vectors_available():
        raise HTTPException(400, "no usearch")
    job = store.Job(_con(request), "adopt-vectors", note=model)

    def run() -> None:
        con = store.connect()
        try:
            with store.Job.existing(con, job.id) as mine:
                got = store.adopt_vectors(con, model, job=mine)
                lost = got["missing"]
                mine.note(
                    f"{got['chunks']:,} chunks, {got['documents']:,} documents"
                    + (f", {lost:,} keys with no row" if lost else "")
                )
        except Exception:  # the job row carries the error
            logging.getLogger("prax.adopt").exception("adopt failed")
        finally:
            con.close()

    threading.Thread(target=run, name="adopt-vectors", daemon=True).start()
    return {"job": job.id, "model": model}


@router.get("/changes")
def changes(request: Request) -> dict[str, Any]:
    """A stamp that changes when the store changed (this door's writes or
    another process's commits) and how many jobs are running: the UI polls
    it and re-renders a listing when the stamp moved."""
    con = request.app.state.con  # one fixed connection: its data_version moves
    # when any other connection commits, this door's threads included; a
    # connection is one thread's at a time (reads no longer take the
    # store's lock, which used to serialize these two by the way)
    with request.app.state.con_lock:
        return {
            "stamp": f"{store.data_version(con)}-{request.app.state.writes}",
            "jobs": store.running_jobs(con),
            # what the banner says while the door is busy with one
            "maintenance": store.running_of(con, MAINTENANCE),
        }


class HealReq(BaseModel):
    checks: list[str] | None = None  # None: every ailment that can be repaired


@router.get("/heal")
def heal_findings(
    request: Request, check: str | None = None, examples: int = 6
) -> dict[str, Any]:
    """What is wrong with the store: each ailment, how many rows it finds
    and a few to look at. Reads only; `POST /heal` is what repairs."""
    try:
        return store.health(_con(request), only=_split(check), examples=examples)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/heal")
def heal_apply(req: HealReq, request: Request) -> dict[str, Any]:
    """Repair what the named ailments find (every repairable one when none
    are named). Edges are invalidated, never deleted; the pass is a job."""
    try:
        return store.heal(_con(request), only=req.checks)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class MaintainReq(BaseModel):
    only: list[str] | None = None  # store.PASSES; None: every pass


@router.post("/maintain")
def maintain_start(req: MaintainReq, request: Request) -> dict[str, Any]:
    """The maintenance pass: the acronyms table, the document retrieval
    fields, the domain rules over documents without a set, the duplicate
    captures — what the store does to itself without a model or a
    decision. A job on a thread of its own; poll ``GET /jobs/{id}``."""
    chosen = [p for p in (req.only or []) if p] or list(store.PASSES)
    unknown = [p for p in chosen if p not in store.PASSES + store.ON_REQUEST]
    if unknown:
        raise HTTPException(
            400,
            f"no such pass: {unknown}; passes are {store.PASSES},"
            f" on request {store.ON_REQUEST}",
        )
    return _start_maintain(_con(request), chosen)


def _start_maintain(con: Any, only: list[str] | None) -> dict[str, Any]:
    chosen = [p for p in (only or []) if p] or list(store.PASSES)
    job = store.Job(con, "maintain", note=", ".join(chosen))

    def run() -> None:
        con = store.connect()
        try:
            with store.Job.existing(con, job.id) as mine:
                store.maintain(con, only=chosen, job=mine)
        except Exception:  # the job row carries the error
            logging.getLogger("prax.maintain").exception("maintenance failed")
        finally:
            con.close()

    threading.Thread(target=run, name="maintain", daemon=True).start()
    return {"job": job.id, "passes": chosen}


class LabelReq(BaseModel):
    entity_id: int
    label: str
    lang: str | None = None
    kind: str = "alt"  # pref (one per language) | alt
    producer: str | None = None
    confidence: str | None = None
    run: str | None = None


@router.post("/graph/label")
def label(req: LabelReq, request: Request) -> dict[str, Any]:
    """A name an entity is also known by (``store.add_label``).

    What a dictionary import or a person writes — which the function has
    said since migration 20 and which nothing could do, there being no
    route to it. A preferred label displaces the one already preferred in
    that language, and the shown name follows.
    """
    con = _con(request)
    try:
        wrote = store.add_label(
            con,
            req.entity_id,
            req.label,
            lang=req.lang,
            kind=req.kind,
            producer=req.producer,
            run=req.run,
            confidence=req.confidence,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "entity": req.entity_id,
        "written": wrote,
        "name": store.entity_name(con, req.entity_id),
    }


class RetireReq(BaseModel):
    run: str | None = None  # the pass to end
    producer: str | None = None  # or everything one producer wrote


@router.post("/graph/retire")
def retire(req: RetireReq, request: Request) -> dict[str, Any]:
    """End every live edge a run or a producer wrote (``store.retire_run``).

    "Upgrading a producer's work is `retire_run` plus a new pass"
    (invariant 8), and until now there was no way to do the first half
    except a script that opens the database, which invariant 4 forbids.
    Nothing is deleted: the edges keep their rows and get a `valid_to`,
    so what the graph said and when is still there.
    """
    if not (req.run or req.producer):
        raise HTTPException(400, "name a run or a producer")
    ended = store.retire_run(_con(request), run=req.run, producer=req.producer)
    return {"run": req.run, "producer": req.producer, "edges": ended}


class UnmergeReq(BaseModel):
    run: str  # the run to take back


@router.post("/graph/unmerge")
def unmerge(req: UnmergeReq, request: Request) -> dict[str, Any]:
    """Take a round of merging and renaming back (``store.unmerge_run``).

    A merge and a rename are claims like an edge, and a pass that claimed
    wrongly has to be undoable through the door — or the only way to undo
    it is a script that opens the database, which invariant 4 forbids.
    Every entity the run folded stands on its own again, every entity it
    renamed is called what it was called, and the labels it wrote are
    gone.
    """
    return {"run": req.run, "entities": store.unmerge_run(_con(request), req.run)}


class MergeReq(BaseModel):
    drop: int  # the entity that is the same thing as ``into``
    into: int
    across_types: bool = False  # a tool and a method that are one thing
    run: str | None = None  # the run it is filed under; one of its own by default


@router.post("/graph/merge")
def merge(req: MergeReq, request: Request) -> dict[str, Any]:
    """Say that two entities are one thing (``store.merge_entities``): a
    person's answer to what ``split-names`` lists, where resolution will
    not decide. Filed under a run like any merge, so ``/graph/unmerge``
    takes it back; nothing is deleted."""
    run = req.run or "merge-" + store.now().replace(":", "").replace("-", "")
    try:
        store.merge_entities(
            _con(request),
            req.drop,
            req.into,
            across_types=req.across_types,
            producer="human",
            run=run,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"merged": req.drop, "into": req.into, "run": run}


class ResolveReq(BaseModel):
    apply: bool = False  # False: the plan only
    type: str | None = None  # one entity type
    twins: bool = False  # merge a concept into the method of the same name
    subtypes: bool = False  # merge a name's general type into its specific one
    likely: bool = True  # the likely tier: the pairs a worker left
    show: int = 40  # candidates per tier in the plan


@router.post("/graph/resolve")
def resolve_entities(req: ResolveReq, request: Request) -> dict[str, Any]:
    """Entity resolution (``prax.graph.resolution``): the plan and, with
    ``apply``, a job that merges what is safe.

    The tiers: sure (equal after normalization, an initials form of one
    author name), subtypes (one name under a type and its subtype — an
    author who is also a person, a paper that is also a document),
    concept/method twins, and likely ones (close by name embedding,
    computed by a worker through the resolve step and kept until
    decided). ``apply`` merges the sure ones, and the subtypes and twins
    when asked; the likely ones are a person's decision, or an
    adjudicator's, and stay in the plan. Merges are pointers
    (``entities.canonical_id``): nothing is deleted.
    """
    from prax.graph import resolution

    con = _con(request)
    plan = resolution.plan(con, etype=req.type, likely=req.likely)
    tiers = {
        tier: {
            "count": len(items),
            "examples": [
                {
                    "type": c.type,
                    "score": round(c.score, 2),
                    "drop": c.drop_name,
                    "keep": c.keep_name,
                }
                for c in items[: req.show]
            ],
        }
        for tier, items in (
            ("sure", plan.sure),
            ("subtypes", plan.subtypes),
            ("twins", plan.twins),
            ("likely", plan.likely),
        )
    }
    tiers["likely"]["computed"] = store.candidate_runs(con)
    if not req.apply:
        return {"plan": tiers, "applied": False}
    job = _start_resolve(con, plan, twins=req.twins, subtypes=req.subtypes)
    return {"plan": tiers, "applied": True, "job": job}


def _start_resolve(
    con: Any, plan: Any = None, *, twins: bool = True, subtypes: bool = True
) -> int:
    """A round of the safe tiers as a job: the sure merges, and the twins
    and subtype folds when asked; the likely pairs stay a person's.

    On the door's clock (``schedule: resolve``) as well as on request.
    Run by hand, the round was run too seldom: 205 sure merges, 59
    subtype folds and 130 twins had piled up by 2026-09-27, the missed
    merges a traverse from a name then showed as several things
    (docs/eval/fractured-names-2026-09-27.md). Each round is one run,
    which ``unmerge_run`` takes back."""
    from prax.graph import resolution

    if plan is None:
        plan = resolution.plan(con, likely=False)
    job = store.Job(
        con,
        "resolve",
        total=len(plan.sure)
        + (len(plan.subtypes) if subtypes else 0)
        + (len(plan.twins) if twins else 0),
        note=f"{len(plan.sure)} sure" + (f", {len(plan.twins)} twins" if twins else ""),
    )

    def run() -> None:
        own = store.connect()
        try:
            with store.Job.existing(own, job.id) as mine:
                rep = resolution.apply(own, plan, twins=twins, subtypes=subtypes)
                mine.note(
                    f"done: merged {rep.merged_sure} sure,"
                    f" {rep.merged_subtypes} subtypes, {rep.merged_twins} twins;"
                    f" {len(plan.likely)} likely left for a person"
                )
        except Exception:
            logging.getLogger("prax.resolve").exception("resolution failed")
        finally:
            own.close()

    threading.Thread(target=run, name="resolve", daemon=True).start()
    return int(job.id)


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


class BackupReq(BaseModel):
    dest: str | None = None  # None: the paths.backup setting
    archive: bool = True  # False: the database, indexes and config only


@router.post("/backup")
def backup_start(req: BackupReq, request: Request) -> dict[str, Any]:
    """Copy the store to a directory on this host (``dest``, or the
    ``paths.backup`` setting): the database as a consistent snapshot, the
    vector indexes, the config, and the archive files the copy lacks
    (``archive: false`` leaves the originals out: what cannot be rebuilt,
    for a disk too small for them). The copy runs on a thread of its own
    and is a job; poll ``GET /jobs/{id}`` for its progress and its last
    note."""
    try:
        return _start_backup(_con(request), req.dest, archive=req.archive)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _start_backup(con: Any, where: str | None, *, archive: bool) -> dict[str, Any]:
    dest = store.backup_target(where)
    job = store.Job(con, "backup", note=str(dest))

    def run() -> None:
        con = store.connect()
        try:
            with store.Job.existing(con, job.id) as mine:
                store.backup(con, dest, archive=archive, job=mine)
        except Exception:  # the job row carries the error
            logging.getLogger("prax.backup").exception("backup to %s failed", dest)
        finally:
            con.close()

    threading.Thread(target=run, name="backup", daemon=True).start()
    return {"job": job.id, "dest": str(dest)}


# the passes the store does to itself, as the jobs they run under: the
# banner's list, and the Jobs view's (GET /maintenance)
MAINTENANCE = ("maintain", "heal", "resolve", "backup", "figures", "questions")


@router.get("/maintenance")
def maintenance(request: Request) -> dict[str, Any]:
    """Every pass the door runs on itself, in one shape: what it does,
    when the clock runs it, how it went last, and how to start it now.

    They were five commands with five shapes, and the UI could start none
    of them (niggles.txt). ``start`` is the request that begins one, given
    only where that is safe from a page: the passes are idempotent, a
    resolve round and a heal are taken back by their run, and backup and
    the rechunk are left to the command line, the one for its destination
    and the other for its cost. The health panel lists the ailments."""
    con = _con(request)
    clock = {e.name: e for e in schedule.entries()}

    def last(name: str) -> dict[str, Any] | None:
        j = store.last_job(con, name)
        if j is None:
            return None
        return {
            "status": j["status"],
            "started_at": j["started_at"],
            "finished_at": j.get("finished_at"),
            "note": j.get("note"),
        }

    def at(name: str) -> str | None:
        e = clock.get(name)
        return e.at.strftime("%H:%M") if e else None

    notes = store.pass_notes()
    passes: list[dict[str, Any]] = [
        {
            "name": "maintain",
            "what": "every pass below that the nightly runs, one job",
            "at": at("maintain"),
            "last": last("maintain"),
            "start": {"path": "/maintain", "body": {}},
        }
    ]
    for name in (*store.PASSES, *store.ON_REQUEST):
        passes.append(
            {
                "name": name,
                "what": notes.get(name, ""),
                "at": at("maintain") if name in store.PASSES else None,
                # the nightly's passes run inside it; the others only asked for
                "part_of": "maintain" if name in store.PASSES else None,
                "on_request": name in store.ON_REQUEST,
                "last": last("maintain") if name in store.ON_REQUEST else None,
                "start": None
                if name == "rechunk"
                else {"path": "/maintain", "body": {"only": [name]}},
            }
        )
    passes += [
        {
            "name": "resolve",
            "what": "the safe merges: equal names in a type, a type and its"
            " subtype, the concept/method twins; the likely pairs stay a"
            " person's",
            "at": at("resolve"),
            "last": last("resolve"),
            "start": {
                "path": "/graph/resolve",
                "body": {
                    "apply": True,
                    "twins": True,
                    "subtypes": True,
                    "likely": False,
                },
            },
        },
        {
            "name": "figures",
            "what": "a slice of the documents whose pictures the vision model"
            " has not read",
            "at": at("figures"),
            "last": last("figures"),
            "start": {"path": "/figures", "body": {}},
        },
        {
            "name": "questions",
            "what": "the standing questions asked again where the library"
            " learned something, then the day's briefing",
            "at": at("questions"),
            "last": last("questions"),
            "start": {"path": "/questions/run", "body": {}},
        },
        {
            "name": "backup",
            "what": "what cannot be rebuilt, copied to the backup path",
            "at": at("backup"),
            "last": last("backup"),
            "start": None,
        },
        {
            "name": "heal",
            "what": "the recurring ailments, each repaired as a job: the health"
            " panel lists them",
            "at": None,
            "last": last("heal"),
            "start": None,
            "where": "the health panel",
        },
    ]
    return {"passes": passes, "running": store.running_of(con, MAINTENANCE)}


class FiguresReq(BaseModel):
    documents: int | None = None  # the slice; FIGURES_SLICE by default


@router.post("/figures")
def figures_start(req: FiguresReq, request: Request) -> dict[str, Any]:
    """The figures slice now, as the clock asks for it at its hour."""
    return _start_figures(_con(request), req.documents)


FIGURES_SLICE = 150  # documents a night: about three hours of the local model


def _start_figures(con: Any, documents: int | None = None) -> dict[str, Any]:
    """The figures backlog, a slice a night (``schedule: figures``).

    13,070 captioned pictures in 1,984 documents had never been read on
    2026-09-27, and asked for at once they would have stood in front of
    every capture's parse for a day and a half: a reading that was asked
    for goes first. So the clock asks for the next ``documents`` whose
    pictures the vision model has not read, oldest first, and only when
    the last slice is done and the model is free. A document whose
    figures are captions with no picture behind them is not asked for:
    no reading changes those, and they are half the figure chunks.
    """
    from prax.capture import pipeline

    n = int(documents or FIGURES_SLICE)  # schedule: figures: {documents: N}
    with store.Job(con, "figures", note=f"a slice of {n}") as job:
        spec = models.resolve("vision")
        if spec is None or not pipeline.vision_is_free():
            job.update(note="the vision model is off or paid: nothing asked")
            return {"job": job.id, "requested": 0, "why": "vision model off or paid"}
        waiting = int(store.waiting_readings(con).get("figures", 0) or 0)
        if waiting:
            job.update(note=f"{waiting} figure readings still waiting: nothing asked")
            return {"job": job.id, "requested": 0, "waiting": waiting}
        chosen = []
        for doc_id in store.select_for_reading(con, unread_figures=True):
            if store.figures_to_read(con, doc_id, model=spec.runtime_name) > 0:
                chosen.append(doc_id)
                if len(chosen) >= n:
                    break
        got = store.request_readings(con, chosen, "figures", by="clock")
        job.update(note=f"asked for {got.get('requested', 0)} of {len(chosen)}")
        return {"job": job.id, **got}


@router.get("/stats")
def stats(request: Request) -> dict[str, Any]:
    """What the store holds: documents, chunks, vectors, the graph, the
    review queue, the ontology (`prax status`)."""
    return store.stats(_con(request))


@router.get("/spending")
def spending(request: Request, days: int = 30, limit: int = 20) -> dict[str, Any]:
    """What the paid steps have cost: the budget and what is left of it
    today and this month, then the ledger by step, by model and call by
    call over the last ``days`` (``prax.ml.budget``, ``store.spending``).
    A host with only local models has an empty ledger and no limits."""
    from prax.ml import budget

    con = _con(request)
    since = (datetime.now(UTC) - timedelta(days=max(1, min(days, 365)))).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "budget": budget.state(con),
        "since": since,
        "ledger": store.spending(con, since=since, limit=max(1, min(limit, 200))),
        "today": store.spending(con, since=budget.day_start(), limit=0),
    }


@router.get("/up")
def up_state(request: Request) -> dict[str, Any]:
    """What ``prax up`` is running on this host, what each group's
    resource is doing, what waits for the roles that are down, and the
    cards' memory: everything the Jobs view needs to offer a swap."""
    from prax import config
    from prax.host import hostinfo, up

    state = up.status(config.data_dir())
    return {
        "up": state,
        "demand": work.demand(_con(request)),
        "gpu": hostinfo.gpu(),
        "gpu_holders": hostinfo.holders(),
        "memory": hostinfo.memory(),
    }


class UpCommand(BaseModel):
    cmd: str  # start, stop, restart, swap, unswap
    name: str | None = None  # the role, for start/stop/restart
    to: str | None = None  # the role that takes the resource, for swap
    group: str | None = None  # for unswap
    back_when: str = "idle"


@router.post("/up/command")
def up_command(req: UpCommand, request: Request) -> dict[str, Any]:
    """Ask the supervisor on this host for a role change: the same
    commands ``prax up`` and the tray write, from the UI. The door does
    not supervise anything; it writes the command file and says whether
    a supervisor is there to read it. A swap also lets the deferred
    readings of that role go, so the worker offers them at once instead
    of waiting out its ten minutes."""
    from prax import config
    from prax.host import up

    if req.cmd not in ("start", "stop", "restart", "swap", "unswap"):
        raise HTTPException(400, "cmd must be start, stop, restart, swap or unswap")
    data_dir = config.data_dir()
    if up.running_pid(data_dir) is None:
        raise HTTPException(409, "prax up is not running on this host")
    role = req.to if req.cmd == "swap" else req.name
    if req.cmd in ("start", "stop", "restart", "swap") and not role:
        raise HTTPException(400, f"{req.cmd}: which role?")
    if req.cmd == "swap":
        up.swap(data_dir, str(role), back_when=req.back_when)
        released = work.release_deferred("parse")
    elif req.cmd == "unswap":
        up.unswap(data_dir, req.group or "all")
        released = 0
    else:
        up.command(data_dir, {"cmd": req.cmd, "name": role})
        released = work.release_deferred("parse") if req.cmd != "stop" else 0
    return {"asked": req.cmd, "role": role, "released": released}


@router.get("/jobs")
def jobs(request: Request, limit: int = 20) -> dict[str, Any]:
    """What runs and what ran lately, and what this door's host has left
    (free RAM, commit headroom) so a wall is visible before it is hit."""
    out = store.list_jobs(_con(request), limit=limit)
    out["host"] = {"name": socket.gethostname(), **hostinfo.memory()}
    return out


@router.get("/models/servers")
def model_servers() -> dict[str, Any]:
    """The model servers ``prax.yaml`` names (``openai`` models) and what
    each says about itself: reachable, model, slots, vision, and its load
    when it was started with ``--metrics`` (the Jobs page shows this)."""
    return {"servers": [models.server_status(s) for s in models.servers()]}


@router.get("/jobs/{job_id}")
def job(job_id: int, request: Request) -> dict[str, Any]:
    row = store.get_job(_con(request), job_id)
    if row is None:
        raise HTTPException(404, f"no job {job_id}")
    return row


@router.post("/vectors/release")
def vectors_release() -> dict[str, Any]:
    """Drop the door's memory-mapped index views so a batch job on this
    machine can replace the files; they reopen on the next query."""
    return {"released": store.release_vector_views()}


# ---------------------------------------------------------------- tokens
# The administrator's (a named token never reaches these: auth's
# RESTRICTED_ROUTES does not name them).


class TokenReq(BaseModel):
    name: str
    domains: list[str] | None = None  # the modules it sees; none: every one
    personal: bool = False  # sees personal documents too


@router.get("/tokens")
def tokens(request: Request) -> dict[str, Any]:
    """The named tokens, without their secrets."""
    return {"tokens": store.list_tokens(_con(request))}


@router.post("/tokens")
def token_add(req: TokenReq, request: Request) -> dict[str, Any]:
    """A new named token. The secret is in this answer and nowhere else."""
    from prax.graph import ontology

    known = set(ontology.current().modules) | {store.UNASSIGNED}
    unknown = sorted(set(req.domains or []) - known)
    if unknown:
        raise HTTPException(400, f"unknown modules: {', '.join(unknown)}")
    try:
        secret = store.add_token(
            _con(request), req.name, domains=req.domains, personal=req.personal
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"name": req.name, "secret": secret}


@router.delete("/tokens/{name}")
def token_remove(name: str, request: Request) -> dict[str, Any]:
    if not store.remove_token(_con(request), name):
        raise HTTPException(404, "no such token")
    return {"removed": name}


@router.get("/documents/suspected")
def documents_suspected(
    request: Request, offset: int = 0, limit: int = 30
) -> dict[str, Any]:
    """The documents the personal-document rules suspect and no person has
    decided about, with their cues (``store.suspected_page``): the Review
    page's "personal?" list. A decision is ``PUT /doc/{id}/sensitivity``."""
    return store.suspected_page(_con(request), offset=offset, limit=limit)


class SensitivityReq(BaseModel):
    state: str | None  # "personal", "suspected", or null: open


@router.put("/doc/{doc_id}/sensitivity")
def doc_sensitivity(
    doc_id: int, req: SensitivityReq, request: Request
) -> dict[str, Any]:
    """Mark a document personal (hidden from the tokens that may not see
    it), or open it again."""
    try:
        was = store.set_sensitivity(_con(request), doc_id, req.state)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"doc_id": doc_id, "state": req.state, "was": was}
