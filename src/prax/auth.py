"""Bearer-token access control for the HTTP door.

One shared secret, ``PRAX_TOKEN``. Every request must carry it, either as
``Authorization: Bearer <token>`` (scripts, the extension, the agent over
HTTP) or as the ``prax_session`` cookie the UI obtains from ``POST /session``
so that plain links to originals work in a browser tab. The static UI files
and ``/health`` are open: they reveal nothing, and the UI needs its own code
before it can ask for the token.

With no token configured the door admits loopback clients only and refuses
everything else, so a development server on this machine works unchanged
while a misconfigured deployment cannot expose the store. Comparison is
constant-time. The MCP server over stdio is unaffected: it is a local
process talking to the store directly.
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

COOKIE = "prax_session"
OPEN_PREFIXES = ("/ui/", "/health")
OPEN_PATHS = ("/", "/ui", "/session")  # /session validates the token itself
LOOPBACK = ("127.0.0.1", "::1", "localhost", "testclient")

log = logging.getLogger("prax.auth")


def token() -> str | None:
    value = os.environ.get("PRAX_TOKEN", "").strip()
    return value or None


def is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in LOOPBACK


def presented(request: Request) -> str | None:
    """The credential a request carries, from the header or the cookie."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(COOKIE)


def valid(candidate: str | None) -> bool:
    expected = token()
    if expected is None or candidate is None:
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())


def allowed(request: Request) -> bool:
    path = request.url.path
    if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
        return True
    if token() is None:
        return is_loopback(request)
    return valid(presented(request))


async def middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if allowed(request):
        return await call_next(request)
    reason = (
        "no token configured; only loopback clients are admitted"
        if token() is None
        else "missing or invalid token"
    )
    # a line in the door's log: who was turned away and why, which is what
    # a client's "network error" from another machine comes down to
    client = request.client.host if request.client else "?"
    log.warning(
        "refused %s %s from %s: %s", request.method, request.url.path, client, reason
    )
    return JSONResponse(
        {"detail": reason},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


def session_cookie(response: Response, value: str, *, secure: bool) -> None:
    response.set_cookie(
        COOKIE,
        value,
        httponly=True,
        samesite="strict",
        secure=secure,
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
