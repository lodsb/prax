"""What the door says about itself and does to itself: heal and
maintenance, backups, the figures slice, stats and spending, the
supervisor's state, and the jobs."""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from prax import models, store, work
from prax.host import hostinfo, schedule

from . import passes
from ._base import _con, _split
from .passes import MAINTENANCE

router = APIRouter()


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
    return passes.start_maintain(_con(request), chosen)


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
        return passes.start_backup(_con(request), req.dest, archive=req.archive)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
    return passes.start_figures(_con(request), req.documents)


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
    resource is doing, what waits for the roles that are down, the card's
    plan for it (``GET /work/plan``), and the cards' memory: everything
    the Jobs view needs to offer a swap."""
    from prax import config
    from prax.host import hostinfo, plan, up

    state = up.status(config.data_dir())
    demand = work.demand(_con(request))
    return {
        "up": state,
        "demand": demand,
        "plan": plan.for_host(demand, state),
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
    out: dict[str, Any] = dict(store.list_jobs(_con(request), limit=limit))
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
    return dict(row)  # a plain dict: the annotation is FastAPI's response model
