"""Bearer-token access control for the HTTP door.

The administrator's secret is ``PRAX_TOKEN``; named tokens beside it
(``prax token add``, stage U) see only what they are given. A request must
carry one of them, either as
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
import re
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


def _routes(*pairs: tuple[str, str]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((method, re.compile(pattern)) for method, pattern in pairs)


# what a named token may call, by its role (migration 58). A reader's are
# reads: the routes the MCP tools read through (prax.mcp_server) and the
# web page's for its search, document, pages, ask and graph walks (a token
# signs in there too, POST /session), each filtered by the viewer in the
# store. A contributor may also add to the library and change it. Anything
# else is the administrator's, so no route the wall does not cover is in
# reach: the people, faces, regions, review and admin views among them.
READER_ROUTES = _routes(
    ("GET", r"/search"),
    ("GET", r"/get/\d+"),
    ("GET", r"/chunk/\d+"),
    ("GET", r"/traverse"),
    ("GET", r"/edge/\d+/why"),
    ("GET", r"/graph/changes"),
    ("GET", r"/graph/connect"),
    ("GET", r"/documents"),
    ("GET", r"/doc/\d+/context"),
    ("GET", r"/doc/\d+/references"),
    # what a figure line shows, and a page of the original drawn
    ("GET", r"/doc/\d+/figure/[0-9a-f]+"),
    ("GET", r"/doc/\d+/page/\d+"),
    ("POST", r"/references/missing"),
    ("GET", r"/page/[^/]+"),
    # a reader's ask answers with no paid model (api.ask)
    ("POST", r"/ask"),
    ("POST", r"/maths"),
    ("GET", r"/work/status"),
    ("GET", r"/changes"),
    ("GET", r"/ontology"),
    ("GET", r"/genres"),
    ("GET", r"/ask/config"),
    ("GET", r"/domains/counts"),
    ("GET", r"/doc/\d+/(original|text|chunks|thumb|preview)"),
    ("GET", r"/pages"),
    ("GET", r"/entities"),
    ("GET", r"/graph/overview"),
    ("GET", r"/graph/documents"),
    ("GET", r"/graph/document/\d+"),
)
CONTRIBUTOR_ROUTES = READER_ROUTES + _routes(
    ("PUT", r"/page/[^/]+"),
    ("POST", r"/page/[^/]+/append"),
    ("PUT", r"/page/[^/]+/section"),
    ("POST", r"/link"),
    ("PUT", r"/doc/\d+/domains"),
    ("POST", r"/doc/\d+/promote"),
    ("POST", r"/doc/\d+/reading"),
    ("POST", r"/ingest"),
    ("POST", r"/ingest/url"),
    ("POST", r"/ingest/urls"),
    ("PUT", r"/doc/\d+/title"),
    ("POST", r"/ingest/file"),
    # a plan of any project, a sync of one the administrator registered
    # (prax.capture.projects._named_may)
    ("POST", r"/projects/sync"),
)
ROLE_ROUTES = {"reader": READER_ROUTES, "contributor": CONTRIBUTOR_ROUTES}
# every route some named token may call (tests/test_wall.py walks them all)
RESTRICTED_ROUTES = CONTRIBUTOR_ROUTES


def whose(candidate: str | None) -> dict[str, object] | None:
    """Who a secret is: ``{"admin": True}`` for the administrator's,
    ``{"admin": False, "name": …, "personal": …}`` for a named token, or
    None for neither."""
    if valid(candidate):
        return {"admin": True}
    if not candidate or not candidate.startswith("prax_"):
        return None
    from prax import store

    viewer = store.token_viewer(store.thread_connection(), candidate)
    if viewer is None:
        return None
    return {
        "admin": False,
        "name": viewer.name,
        "personal": viewer.personal,
        "role": viewer.role,
    }


def restricted_may(request: Request, role: str = "reader") -> bool:
    """Whether a named token of this role may call the request's route."""
    path = request.url.path
    return any(
        request.method == method and pattern.fullmatch(path)
        for method, pattern in ROLE_ROUTES.get(role, ())
    )


def named_viewer(request: Request) -> object | None:
    """The viewer of a named token the request carries, or None."""
    candidate = presented(request)
    if not candidate or not candidate.startswith("prax_"):
        return None
    from prax import store

    con = store.thread_connection()
    viewer = store.token_viewer(con, candidate)
    if viewer is not None:
        store.note_token_use(con, viewer.name)
    return viewer


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
    viewer = named_viewer(request)
    if viewer is not None:
        role = str(getattr(viewer, "role", "reader"))
        if not restricted_may(request, role):
            log.warning(
                "refused %s %s to token %s (%s): not a route it may call",
                request.method,
                request.url.path,
                getattr(viewer, "name", "?"),
                role,
            )
            return JSONResponse(
                {"detail": f"a {role}'s token may not call that route"},
                status_code=403,
            )
        from prax import store

        # every store read of this request answers for this viewer (the
        # context is copied into the thread a handler runs in)
        mark = store.VIEWER.set(viewer)  # type: ignore[arg-type]
        try:
            return await call_next(request)
        finally:
            store.VIEWER.reset(mark)
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
