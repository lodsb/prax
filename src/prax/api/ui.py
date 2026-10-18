"""The UI's files as the door serves them.

The page and its stylesheets name every file of the UI they load with
its version, the start of its sha256 (``core.js?v=3f2a…``). A file asked
for with a version is cached by the browser for good, since a change to
it is a new name; the page itself and a file asked for without one are
revalidated on every load (ETag makes that cheap). Before 2026-10-18
every one of the twenty files was revalidated, twenty round trips
through a door that may be busy with a worker's hand-out, on every
load.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import anyio
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

UI_DIR = Path(__file__).resolve().parent.parent / "ui"

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

KEPT = "public, max-age=31536000, immutable"
VERSION_CHARS = 12

# a relative reference in the page and in a stylesheet: no scheme, no
# leading slash, no fragment or query of its own
_HTML_REF = re.compile(r'(\b(?:src|href)=")([^"#?:/][^"#?:]*)(")')
_CSS_REF = re.compile(r"""(url\(\s*(["']?))([^"')#?:/][^"')#?:]*)(\2\s*\))""")
_REWRITTEN = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8"}

_versions: dict[tuple[str, int, int], str] = {}
_versions_lock = threading.Lock()


def version(path: Path) -> str | None:
    """The start of the file's sha256, read again only when its size or
    modification time moves; None for what is not a file."""
    try:
        st = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _versions_lock:
        held = _versions.get(key)
    if held is None:
        held = hashlib.sha256(path.read_bytes()).hexdigest()[:VERSION_CHARS]
        with _versions_lock:
            _versions[key] = held
    return held


def versioned(text: str, here: Path, root: Path, pattern: re.Pattern[str]) -> str:
    """``text`` with every reference to a file under ``root`` carrying its
    version; a reference to anything else is left as written."""
    ref_group = 2 if pattern is _HTML_REF else 3

    def one(m: re.Match[str]) -> str:
        ref = m.group(ref_group)
        target = (here / ref).resolve()
        if not target.is_relative_to(root):
            return m.group(0)
        v = version(target)
        if v is None:
            return m.group(0)
        start, end = m.span(ref_group)
        whole = m.group(0)
        offset = m.start(0)
        return f"{whole[: start - offset]}{ref}?v={v}{whole[end - offset :]}"

    return pattern.sub(one, text)


class UIFiles(StaticFiles):
    """The UI's static files, with the page and its stylesheets naming
    each file they load by version, and the UI's content security
    policy on every answer."""

    def __init__(self, *, directory: os.PathLike[str] | str, **kw: Any) -> None:
        super().__init__(directory=directory, **kw)
        self.root = Path(directory).resolve()

    async def get_response(self, path: str, scope: Scope) -> Response:
        name = "index.html" if path in ("", ".") else path
        kind = _REWRITTEN.get(Path(name).suffix)
        response: Response
        if kind is not None and (self.root / name).resolve().is_relative_to(self.root):
            response = await anyio.to_thread.run_sync(
                self._rewritten, name, kind, scope
            )
        else:
            response = await super().get_response(path, scope)
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        fresh = "v" in query and response.status_code in (200, 304)
        response.headers["Cache-Control"] = KEPT if fresh else "no-cache"
        response.headers["Content-Security-Policy"] = UI_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def _rewritten(self, name: str, kind: str, scope: Scope) -> Response:
        """The page or a stylesheet with its references versioned, with an
        ETag of what is sent (a version moves it as much as an edit)."""
        full = (self.root / name).resolve()
        if not full.is_file():
            return Response("Not Found", status_code=404)
        text = full.read_text(encoding="utf-8")
        pattern = _HTML_REF if full.suffix == ".html" else _CSS_REF
        body = versioned(text, full.parent, self.root, pattern).encode("utf-8")
        etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
        headers = {"ETag": etag}
        asked = dict(scope.get("headers") or []).get(b"if-none-match", b"").decode()
        if etag in [t.strip() for t in asked.split(",")]:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type=kind, headers=headers)
