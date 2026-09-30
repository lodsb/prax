"""A hand on the graph: a label given, an edge retired, a merge made or taken
back, and a round of entity resolution started."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from prax import store

from . import passes
from ._base import _con

router = APIRouter()


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
    job = passes.start_resolve(con, plan, twins=req.twins, subtypes=req.subtypes)
    return {"plan": tiers, "applied": True, "job": job}
