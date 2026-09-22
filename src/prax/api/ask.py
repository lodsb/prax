"""Ask: the config, the question (one shot or a surf, streamed), keeping
an answer on a page."""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import AsyncIterator
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import (
    StreamingResponse,
)
from pydantic import BaseModel

from prax import ask as ask_mod
from prax import (
    store,
)

from ._base import _con

router = APIRouter()


# ------------------------------------------------------------------- ask


class AskReq(BaseModel):
    question: str
    limit: int = ask_mod.PASSAGES
    doctype: str | None = None
    backend: str | None = None  # local | claude | none; None: the host default
    history: list[dict[str, Any]] | None = None  # earlier turns: question, answer
    steps: int | None = None  # surfing steps before the answer; 0: one shot
    tokens: int | None = None  # the reading budget of the steps
    stream: bool = False  # the trail as it happens, one JSON line per event


class SaveReq(BaseModel):
    slug: str
    result: dict[str, Any]
    heading: str | None = None
    create: str | None = None  # a page kind: create the page when the slug is new


@router.get("/ask/config")
def ask_config() -> dict[str, Any]:
    """The backend this host answers with, for the UI's choice."""
    return ask_mod.describe()


@router.post("/ask")
def ask(req: AskReq, request: Request) -> Any:
    """Passages and graph facts for a question, and an answer citing them
    when a backend is configured. With ``steps`` the model surfs first
    (``prax.surf``): the budgets default to the host's and are clamped
    to what the model holds; the result carries the trail. ``stream``
    answers with one JSON object per line as it goes — ``step`` events,
    ``answering``, then ``answer`` with the result (or ``error``) — for a
    client that shows the trail while the model works. Generation runs
    outside the store lock; a local model answers in tens of seconds, a
    surf in a minute or two."""
    try:
        answerer = (
            ask_mod.answerer_named(req.backend) if req.backend else ask_mod.current()
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    steps = req.steps
    if steps is None and answerer is not None:
        steps = ask_mod.default_steps()
    kw: dict[str, Any] = {
        "limit": max(1, min(req.limit, 20)),
        "doctype": req.doctype,
        "answerer": answerer,
        "history": req.history,
        "steps": max(0, steps or 0),
        "tokens": req.tokens,
    }
    if not req.stream:
        try:
            return ask_mod.ask(_con(request), req.question, **kw)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
    return _ask_stream(req.question, kw)


def _ask_stream(question: str, kw: dict[str, Any]) -> StreamingResponse:
    """The surf on its own thread with its own connection, each event
    handed to the response as a line. A client that goes away stops the
    surf at its next step (the response's ``finally`` sets ``stop``; the
    model is not asked again for nobody)."""
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()
    stop = threading.Event()

    def run() -> None:
        con = store.connect()
        try:
            result = ask_mod.ask(con, question, on_event=events.put, stop=stop, **kw)
            events.put({"event": "answer", "result": result})
        except Exception as exc:  # noqa: BLE001 - the client gets the reason
            events.put({"event": "error", "detail": str(exc)})
        finally:
            con.close()
            events.put(None)

    threading.Thread(target=run, name="prax-ask", daemon=True).start()

    def next_event() -> dict[str, Any] | None | bool:
        try:
            return events.get(timeout=1.0)
        except queue.Empty:
            return False  # nothing yet: look again (and notice a cancel)

    async def lines() -> AsyncIterator[str]:
        try:
            while True:
                event = await anyio.to_thread.run_sync(
                    next_event, abandon_on_cancel=True
                )
                if event is None:
                    break
                if event is False:
                    continue
                yield json.dumps(event, ensure_ascii=False) + "\n"
        finally:
            stop.set()

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@router.post("/ask/save")
def ask_save(req: SaveReq, request: Request) -> dict[str, Any]:
    """Append an answer (the result of ``POST /ask``) to a page as the agent."""
    try:
        return ask_mod.save(
            _con(request),
            req.result,
            req.slug,
            heading=req.heading,
            create=req.create,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
