"""The wall (stages U and V): named tokens, the rules that suspect a
document is personal, and a person's answer about one."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from prax import store
from prax.graph import ontology

from ._base import _con

router = APIRouter()


# ---------------------------------------------------------------- tokens
# The administrator's (a named token never reaches these: auth's
# RESTRICTED_ROUTES does not name them).


class TokenReq(BaseModel):
    name: str
    domains: list[str] | None = None  # the modules it sees; none: every one
    personal: bool = False  # sees personal documents too
    role: str = "reader"  # reader | contributor (store.TOKEN_ROLES)


class TokenRoleReq(BaseModel):
    role: str


@router.get("/tokens")
def tokens(request: Request) -> dict[str, Any]:
    """The named tokens, without their secrets, and the modules one may be
    given (the admin page's form)."""

    return {
        "tokens": store.list_tokens(_con(request)),
        "modules": [*sorted(ontology.current().modules), store.UNASSIGNED],
        "roles": list(store.TOKEN_ROLES),
    }


@router.post("/tokens")
def token_add(req: TokenReq, request: Request) -> dict[str, Any]:
    """A new named token. The secret is in this answer and nowhere else."""

    known = set(ontology.current().modules) | {store.UNASSIGNED}
    unknown = sorted(set(req.domains or []) - known)
    if unknown:
        raise HTTPException(400, f"unknown modules: {', '.join(unknown)}")
    try:
        secret = store.add_token(
            _con(request),
            req.name,
            domains=req.domains,
            personal=req.personal,
            role=req.role,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"name": req.name, "secret": secret}


@router.put("/tokens/{name}")
def token_role(name: str, req: TokenRoleReq, request: Request) -> dict[str, Any]:
    """Change what a named token may do; its secret stays the same."""
    try:
        found = store.set_token_role(_con(request), name, req.role)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not found:
        raise HTTPException(404, "no such token")
    return {"name": name, "role": req.role}


@router.delete("/tokens/{name}")
def token_remove(name: str, request: Request) -> dict[str, Any]:
    if not store.remove_token(_con(request), name):
        raise HTTPException(404, "no such token")
    return {"removed": name}


@router.get("/private/rules")
def private_rules(request: Request) -> dict[str, Any]:
    """The personal-document rules in force (stage V): the defaults, what
    ``private:`` in prax.yaml adds, and what they have marked. The owner's
    names are counted, never shown: the page may be on a screen others see.
    Edited in prax.yaml, which the door does not write."""
    from prax import config
    from prax.wall import private

    rules = private.rules()
    return {
        "strong": list(private.STRONG),
        "weak": [list(g) for g in private.WEAK],
        "added": {
            "strong": config.words("private.strong"),
            "weak": config.words("private.weak"),
            "paths": list(rules.paths),
            "names": len(rules.names),
        },
        "weak_needed": rules.weak_needed,
        "head": rules.head,
        "stamp": rules.stamp(),
        "documents": store.sensitivity_counts(_con(request)),
    }


@router.get("/documents/suspected")
def documents_suspected(
    request: Request,
    offset: int = 0,
    limit: int = 30,
    state: str = "suspected",
    referenced: bool = False,
) -> dict[str, Any]:
    """The documents the personal-document rules suspect and no person has
    decided about, with their cues (``store.suspected_page``): the Review
    page's "personal?" list, without the referenced photos and recordings
    unless ``referenced``. A decision is ``PUT /doc/{id}/sensitivity``."""
    try:
        return store.suspected_page(
            _con(request),
            state=state,
            offset=offset,
            limit=limit,
            referenced=referenced,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/media")
def media(request: Request, by: str = "folder", under: str = "") -> dict[str, Any]:
    """The photos and recordings prax references, one level of a grouping
    (``store.media_groups``): a folder's folders, a year's months, or the
    events; with how many of each are suspected, open or personal."""
    try:
        got = store.media_groups(_con(request), by=by, under=under)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**got, "counts": store.media_counts(_con(request))}


@router.get("/media/pictures")
def media_pictures(
    request: Request,
    by: str,
    key: str,
    offset: int = 0,
    limit: int = store.MEDIA_PAGE,
) -> dict[str, Any]:
    """One page of a group's photos and recordings, for the gallery."""
    try:
        return store.media_pictures(_con(request), by, key, offset=offset, limit=limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class MediaDecideReq(BaseModel):
    by: str
    key: str
    state: str  # open | personal


@router.post("/media/decide")
def media_decide(req: MediaDecideReq, request: Request) -> dict[str, Any]:
    """A person's answer for a group (``store.decide_media``): released, or
    personal for good; what a person decided one by one stays."""
    try:
        return store.decide_media(_con(request), req.by, req.key, req.state)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class MediaUndoReq(BaseModel):
    run: str


@router.post("/media/undo")
def media_undo(req: MediaUndoReq, request: Request) -> dict[str, Any]:
    """A group's answer taken back (``store.undo_media``)."""
    return store.undo_media(_con(request), req.run)


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
