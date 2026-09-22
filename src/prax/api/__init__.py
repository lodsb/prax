"""The HTTP door: the FastAPI app, its lifespan, middleware and the UI.
The endpoints are the routers beside this file, one per area.

The store connection is opened in the app lifespan and shared by all
handlers; prax.store serializes access.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from prax import auth, config, embeddings, inbox, schedule, store

from . import ask, capture, documents, graph, jobs, pages, process
from ._base import _con, max_upload
from .jobs import _start_backup, _start_maintain
from .pages import _answering, _answering_lock, _start_questions

__all__ = ["_answering", "_answering_lock", "_con", "app", "max_upload"]

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def _warm() -> None:
    try:
        emb = embeddings.serving()
        if emb is not None and store.vectors_available():
            store.warm_indexes(emb.name)
        con = store.connect()
        try:
            store.warm_fts(con)
        finally:
            con.close()
    except Exception:
        logging.getLogger("prax.door").exception("warming the indexes failed")


def _scan_inbox(app: FastAPI, stop: threading.Event, every: float) -> None:
    """The door consumes its own drop folder (``data/inbox/``): what lands
    there is registered through the store without any other process.
    Parsing and the model passes are a worker's business (``prax.worker``)."""
    con = store.connect()
    try:
        while not stop.wait(every):
            try:
                rep = inbox.scan(con, inbox.inbox_dir())
                if rep.registered or rep.failed:
                    logging.getLogger("prax.inbox").info("drop folder: %s", rep)
                if store.job_reap(con):
                    logging.getLogger("prax.jobs").info(
                        "closed jobs whose process is gone"
                    )
            except Exception:
                logging.getLogger("prax.inbox").exception("drop folder scan failed")
    finally:
        con.close()


def _clock(stop: threading.Event, every: float) -> None:
    """The door's clock (``schedule:``): maintain and backup at their
    hours, as the jobs their endpoints start; the jobs table remembers
    what ran, so a restart does not run the night again."""
    con = store.connect()
    try:
        while not stop.wait(every):
            try:
                schedule.tick(
                    con,
                    {
                        "maintain": lambda o: _start_maintain(con, o.get("only")),
                        "backup": lambda o: _start_backup(
                            con, o.get("dest"), archive=o.get("archive", True)
                        ),
                        "questions": lambda o: _start_questions(
                            con, briefing=o.get("briefing", True)
                        ),
                    },
                )
            except Exception:
                logging.getLogger("prax.schedule").exception("the clock failed")
    finally:
        con.close()


class _QuietResets(logging.Filter):
    """A client that goes away mid-response — a tab closed, a poll cut,
    a curl that timed out — makes asyncio's proactor on Windows log a
    traceback ("_call_connection_lost … forcibly closed by the remote
    host") that says nothing about the door; the log keeps the rest."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not (
            isinstance(exc, ConnectionResetError)
            and "connection_lost" in record.getMessage()
        )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.getLogger("asyncio").addFilter(_QuietResets())
    con = store.connect()
    store.init_db(con)
    app.state.con = con  # the main connection: migrations, the change stamp
    app.state.con_lock = threading.Lock()  # one thread on it at a time
    store.job_reap(con)  # sessions left behind by a killed door or worker
    _say_auth()
    # the index views opened and the keyword index read through, beside
    # the serving: a search that comes first waits on the index lock for
    # the load (seconds) rather than the door being down for it — a cold
    # start read for a minute and a half with llama-server on the disk
    threading.Thread(target=_warm, name="prax-warm", daemon=True).start()
    stop = threading.Event()
    every = config.number("door.inbox_scan_seconds", "PRAX_INBOX_SCAN", 20.0)
    scanner = None
    if every > 0:
        scanner = threading.Thread(
            target=_scan_inbox, args=(app, stop, every), name="prax-inbox", daemon=True
        )
        scanner.start()
    clock = None
    if schedule.entries():
        tick = config.number("door.clock_seconds", "PRAX_CLOCK", schedule.TICK)
        clock = threading.Thread(
            target=_clock, args=(stop, tick), name="prax-clock", daemon=True
        )
        clock.start()
    try:
        yield
    finally:
        stop.set()
        for thread in (scanner, clock):
            if thread is not None:
                thread.join(timeout=5)
        con.close()


SHORT_TOKEN = 24  # characters; under this a token is guessable over a network


def _say_auth() -> None:
    """One line in the door's log at start: what gates it. A short token
    gets a warning, since the door has no lock-out and a LAN or a tailnet
    is a network."""
    log = logging.getLogger("prax.door")
    secret = auth.token()
    if secret is None:
        log.info("auth: no PRAX_TOKEN; loopback clients only, others are refused")
    elif len(secret) < SHORT_TOKEN:
        log.warning(
            "auth: the token is %d characters; a network can guess that. Use 32"
            " random ones (python -c 'import secrets; print(secrets.token_hex(24))')"
            " and keep door.token readable by you alone",
            len(secret),
        )
    else:
        log.info("auth: token (%d characters)", len(secret))


app = FastAPI(title="prax", version="0.0.1", lifespan=_lifespan)
app.middleware("http")(auth.middleware)
app.state.writes = 0


SLOW_SECONDS = 2.0  # a request slower than this is logged with what else was on
app.state.in_flight = 0


@app.middleware("http")
async def _cap_body(request: Request, call_next: Any) -> Any:
    """A body larger than ``max_upload`` is refused at the door with 413
    before it is read, when the client says how large it is."""
    length = request.headers.get("content-length")
    cap = max_upload()
    if length and length.isdigit() and int(length) > cap:
        return JSONResponse(
            {"detail": f"request body over {cap >> 20} MB (door.max_upload_mb)"},
            status_code=413,
        )
    return await call_next(request)


@app.middleware("http")
async def _count_writes(request: Request, call_next: Any) -> Any:
    """Every mutating request bumps a counter: with SQLite's data_version
    (other writers) it makes the change stamp the UI polls. A slow
    request is logged with how many others were in flight and which jobs
    ran, so "loading takes ages" leaves a trace of what the door was
    doing at the time (a GET of one document took 77 s once, on
    2026-09-17, with three book-sized readings being taken in)."""
    state = request.app.state
    state.in_flight += 1
    others = state.in_flight - 1
    started = time.monotonic()
    try:
        response = await call_next(request)
    finally:
        state.in_flight -= 1
    seconds = time.monotonic() - started
    if request.method not in ("GET", "HEAD", "OPTIONS") and response.status_code < 400:
        state.writes += 1
    if seconds >= SLOW_SECONDS and not request.url.path.startswith("/ask"):
        jobs = ""
        with contextlib.suppress(Exception):
            running = store.list_jobs(store.thread_connection(), limit=1)["running"]
            jobs = ", ".join(j["name"] for j in running)
        detail = getattr(request.state, "detail", None)
        where = ""
        if isinstance(detail, dict) and detail:
            where = "; " + ", ".join(f"{k} {v:.1f} s" for k, v in detail.items())
        logging.getLogger("prax.door").warning(
            "slow: %s %s took %.1f s (%d other requests in flight%s%s)",
            request.method,
            request.url.path,
            seconds,
            others,
            f"; jobs: {jobs}" if jobs else "",
            where,
        )
    return response


# A browser extension calls the door from its own origin
# (chrome-extension://…, moz-extension://…). Every extension origin is
# answered: the token is what gates the door, not the origin, and a
# browser that withholds its host permission (Firefox until granted,
# Chrome in some contexts) then runs the request through CORS.
# door.cors_origins in prax.yaml (or PRAX_CORS_ORIGINS, comma-separated)
# adds web origins; the UI itself is same-origin.
from fastapi.middleware.cors import CORSMiddleware

EXTENSION_ORIGINS = r"^(chrome|moz|safari-web)-extension://.+$"
_cors = [o for o in config.words("door.cors_origins", "PRAX_CORS_ORIGINS")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors,
    allow_origin_regex=EXTENSION_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    # Chrome's private network access: a request from an extension (or a
    # public page) into a private address asks in its preflight whether
    # the target consents; a door on the private network does
    allow_private_network=True,
)


class SessionReq(BaseModel):
    token: str


@app.get("/health", include_in_schema=False)
def health() -> dict[str, Any]:
    return {"ok": True, "auth": "token" if auth.token() else "loopback-only"}


@app.post("/session")
def session(req: SessionReq, request: Request) -> JSONResponse:
    """Exchange the bearer token for the session cookie the UI uses."""
    if auth.token() is None:
        if not auth.is_loopback(request):
            raise HTTPException(401, "no token configured")
        return JSONResponse({"ok": True, "cookie": False})
    if not auth.valid(req.token):
        raise HTTPException(401, "invalid token")
    response = JSONResponse({"ok": True, "cookie": True})
    auth.session_cookie(response, req.token, secure=request.url.scheme == "https")
    return response


@app.delete("/session")
def end_session() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


class UIError(BaseModel):
    kind: str = "error"
    message: str
    stack: str | None = None
    hash: str | None = None
    agent: str | None = None


@app.post("/ui/error", include_in_schema=False)
def ui_error(err: UIError, request: Request) -> dict[str, bool]:
    """A client-side error, logged by the door so it can be read later."""
    logging.getLogger("prax.ui").warning(
        "browser %s at %s: %s\n%s\n%s",
        err.kind,
        err.hash,
        err.message,
        err.stack or "",
        err.agent or "",
    )
    return {"logged": True}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


# What the UI page may load and run. The UI renders Markdown that other
# people wrote (captured pages, a model's answer) into HTML, and marked
# passes raw HTML through; this is what keeps a <script> or an onerror=
# in that text from running with the session cookie: only the UI's own
# files run as script, nothing inline, no javascript: links, no forms
# posting elsewhere, no framing, images only from the door itself.
UI_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    # a video document's player, loaded only when the reader presses play
    "frame-src https://www.youtube-nocookie.com; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


class _UIFiles(StaticFiles):
    """Static files that browsers revalidate on every load (ETag makes
    that cheap), so a redeploy never leaves a stale app.js behind, and
    that carry the UI's content security policy."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Content-Security-Policy"] = UI_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


app.mount("/ui", _UIFiles(directory=UI_DIR, html=True), name="ui")


for _router in (capture, documents, graph, pages, ask, process, jobs):
    app.include_router(_router.router)
