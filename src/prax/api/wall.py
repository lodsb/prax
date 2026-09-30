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


@router.get("/tokens")
def tokens(request: Request) -> dict[str, Any]:
    """The named tokens, without their secrets, and the modules one may be
    given (the admin page's form)."""

    return {
        "tokens": store.list_tokens(_con(request)),
        "modules": [*sorted(ontology.current().modules), store.UNASSIGNED],
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
    request: Request, offset: int = 0, limit: int = 30, state: str = "suspected"
) -> dict[str, Any]:
    """The documents the personal-document rules suspect and no person has
    decided about, with their cues (``store.suspected_page``): the Review
    page's "personal?" list. A decision is ``PUT /doc/{id}/sensitivity``."""
    try:
        return store.suspected_page(
            _con(request), state=state, offset=offset, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
