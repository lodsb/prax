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

from prax import (
    embeddings,
    hostinfo,
    models,
    ontology,
    store,
    work,
)

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
    same: list[bool] | None = None  # adjudicate: one decision per item


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


class ResolveReq(BaseModel):
    apply: bool = False  # False: the plan only
    type: str | None = None  # one entity type
    twins: bool = False  # merge a concept into the method of the same name
    subtypes: bool = False  # merge a name's general type into its specific one
    likely: bool = True  # the likely tier: the pairs a worker left
    show: int = 40  # candidates per tier in the plan


@router.post("/graph/resolve")
def resolve_entities(req: ResolveReq, request: Request) -> dict[str, Any]:
    """Entity resolution (``prax.resolution``): the plan and, with
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
    from prax import resolution

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
    job = store.Job(
        con,
        "resolve",
        total=len(plan.sure)
        + (len(plan.subtypes) if req.subtypes else 0)
        + (len(plan.twins) if req.twins else 0),
        note=f"{len(plan.sure)} sure"
        + (f", {len(plan.twins)} twins" if req.twins else ""),
    )

    def run() -> None:
        own = store.connect()
        try:
            with store.Job.existing(own, job.id) as mine:
                rep = resolution.apply(
                    own, plan, twins=req.twins, subtypes=req.subtypes
                )
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
    return {"plan": tiers, "applied": True, "job": job.id}


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


def _start_backup(con: Any, dest: str | None, *, archive: bool) -> dict[str, Any]:
    dest = store.backup_target(dest)
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


@router.get("/stats")
def stats(request: Request) -> dict[str, Any]:
    """What the store holds: documents, chunks, vectors, the graph, the
    review queue, the ontology (`prax status`)."""
    return store.stats(_con(request))


@router.get("/spending")
def spending(request: Request, days: int = 30, limit: int = 20) -> dict[str, Any]:
    """What the paid steps have cost: the budget and what is left of it
    today and this month, then the ledger by step, by model and call by
    call over the last ``days`` (``prax.budget``, ``store.spending``).
    A host with only local models has an empty ledger and no limits."""
    from prax import budget

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
    from prax import config, hostinfo, up

    state = up.status(config.data_dir())
    return {
        "up": state,
        "demand": work.demand(_con(request)),
        "gpu": hostinfo.gpu(),
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
    from prax import config, up

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
