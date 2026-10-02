"""The work protocol: a worker's session and heartbeat, the batches it is
handed and the results it posts (``prax.work``, ``prax.steps``), what the
steps' models are asked for, and the vector index's merge and release."""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from prax import store, work
from prax.ml import embeddings

from ._base import _con
from .passes import MAINTENANCE

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
def work_demand(request: Request, plan: bool = False) -> dict[str, Any]:
    """What waits for a role that has to be running to do it (``prax.work``
    ``ROLE_WORK``): the reading requests per extractor and per role. The
    supervisor asks this to know when a borrowed card can go back, and
    the Jobs view shows it beside the roles. With ``plan`` it carries the
    card's plan too (``GET /work/plan``), which is what ``prax up``
    follows: one request a look."""
    out = work.demand(_con(request))
    if plan:
        from prax import config
        from prax.host import plan as card_plan
        from prax.host import up

        out["plan"] = card_plan.for_host(out, up.status(config.data_dir()))
    return out


@router.get("/work/status")
def work_status(request: Request, ids: str = "") -> dict[str, Any]:
    """Where each document is on its way to being read, and whether a
    worker is about (``work.status``): ``ids`` comma-separated, none for
    the worker alone. A named token's documents only (stage U)."""
    try:
        doc_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(400, "ids: comma-separated document ids") from exc
    if len(doc_ids) > 200:
        raise HTTPException(400, "ids: 200 at most")
    return work.status(_con(request), doc_ids)


@router.get("/work/plan")
def work_plan(request: Request) -> dict[str, Any]:
    """What the card does next (stage AI): each group of waiting work
    with what its swap, its work and its wait cost, a decision, and the
    order the card would serve them in (``prax.host.plan``). The swap
    costs are ``prax up``'s measured load times; without a supervisor
    here they are guesses, and the plan says so."""
    from prax import config
    from prax.host import plan, up

    return plan.for_host(work.demand(_con(request)), up.status(config.data_dir()))


class NowReq(BaseModel):
    role: str
    action: str | None = None  # an extractor or a step; none is all of it


@router.post("/work/now")
def work_now(req: NowReq) -> dict[str, Any]:
    """ "Do it now" for what waits on a role (stage AI): its deferrals go,
    and ``prax up`` gives it the card at its next look at the demand,
    unless an ask holds the card (``after_ask`` says so). The door
    supervises nothing; it says what is wanted, the supervisor acts."""
    try:
        return work.do_now(req.role, req.action)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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


@router.post("/vectors/release")
def vectors_release() -> dict[str, Any]:
    """Drop the door's memory-mapped index views so a batch job on this
    machine can replace the files; they reopen on the next query."""
    return {"released": store.release_vector_views()}
