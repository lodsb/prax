"""The maths pack's route (docs/symbolic-maths.md): the calculator, for the
MCP tool and anyone else the door serves. A host that does not name the
pack in ``packs:`` answers that it has none."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from prax import config

from ._base import _con

router = APIRouter()


class MathsReq(BaseModel):
    op: str  # read, same, simplify, substitute, solve, diff, integrate, ...
    a: str  # LaTeX, plain notation, or chunk:<id> of a display formula
    b: str | None = None  # the second formula of `same`
    notation: str = "latex"  # or plain: x**2 + 1
    args: dict[str, Any] | None = None  # var, values, lower, upper, at, to, ...
    mapping: dict[str, str] | None = None  # same: b's symbols as a's


@router.post("/maths")
def maths(req: MathsReq, request: Request) -> dict[str, Any]:
    """One operation of the calculator. Every answer says how each formula
    was read; a formula the tool cannot read whole is an ``error``, never
    an answer about part of it."""
    if "maths" not in config.host_packs():
        raise HTTPException(
            404, "the maths pack is not on this host (packs: in prax.yaml)"
        )
    from prax.packs.maths import tool

    body = req.model_dump(exclude_none=True)
    try:
        return tool.calculate(_con(request), body)
    except tool.MathsUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
