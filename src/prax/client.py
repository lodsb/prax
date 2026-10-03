"""The door as its clients see it: a few JSON calls, one download and one
upload over HTTP. The worker (``prax.worker``) and the MCP server
(``prax.mcp_server``) speak to the store through this and nothing else, so
neither opens the database (CLAUDE.md invariant 4). ``client`` may be an
httpx client or a test client of the same shape."""

from __future__ import annotations

import json
import mimetypes
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx


class DoorError(RuntimeError):
    """The door answered with an error status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"the door answered {status}: {detail}")
        self.status = status
        self.detail = detail


DEFAULT_DOOR = "http://127.0.0.1:8000"  # the door on this machine


def door_url(text: str) -> str:
    """A door's address as a base URL: ``host:8000`` is
    ``http://host:8000`` (2026-10-03: a ``PRAX_DOOR`` written without
    its scheme failed every call while the MCP server looked connected)."""
    url = text.strip().rstrip("/")
    return url if "://" in url else f"http://{url}"


class Door:
    @classmethod
    def from_env(
        cls, *, name: str | None = None, timeout: float = 600.0, client: Any = None
    ) -> Door:
        """The door ``PRAX_DOOR`` names (this machine's when unset or empty),
        with ``PRAX_TOKEN``: how a script or the MCP proxy finds it."""
        return cls(
            os.environ.get("PRAX_DOOR") or DEFAULT_DOOR,
            token=os.environ.get("PRAX_TOKEN") or None,
            client=client,
            name=name,
            timeout=timeout,
        )

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        client: Any = None,
        name: str | None = None,
        timeout: float = 600.0,
    ) -> None:
        self.base_url = door_url(base_url)
        self.name = name or socket.gethostname()
        self.headers = {"X-Prax-Worker": self.name}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        if client is None:
            import httpx

            client = httpx.Client(base_url=self.base_url, timeout=timeout)
        self.client = client

    def _check(self, res: httpx.Response) -> httpx.Response:
        if res.status_code >= 400:
            detail = ""
            try:
                detail = res.json().get("detail", "")
            except (ValueError, AttributeError):  # not JSON, or not an object
                detail = res.text[:200] if hasattr(res, "text") else ""
            raise DoorError(res.status_code, str(detail))
        return res

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._check(
            self.client.get(path, params=params, headers=self.headers)
        ).json()

    def post_json(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._check(
            self.client.post(path, json=body or {}, headers=self.headers)
        ).json()

    def post_lines(self, path: str, body: dict[str, Any] | None = None) -> Any:
        """A POST whose answer is one JSON object per line, yielded as it
        arrives (``/ask`` with ``stream``)."""
        with self.client.stream(
            "POST", path, json=body or {}, headers=self.headers
        ) as res:
            if res.status_code >= 400:
                res.read()
                self._check(res)
            for line in res.iter_lines():
                if line.strip():
                    yield json.loads(line)

    def put_json(self, path: str, body: dict[str, Any]) -> Any:
        return self._check(
            self.client.put(path, json=body, headers=self.headers)
        ).json()

    def delete_json(self, path: str) -> Any:
        return self._check(self.client.delete(path, headers=self.headers)).json()

    def post_bytes(
        self,
        path: str,
        data: bytes,
        params: dict[str, Any] | None = None,
        content_type: str = "application/octet-stream",
    ) -> Any:
        """A POST whose body is a file as it is (``/graph/import``)."""
        headers = {**self.headers, "Content-Type": content_type}
        return self._check(
            self.client.post(path, content=data, params=params, headers=headers)
        ).json()

    def get_bytes_with(self, path: str, params: dict[str, Any]) -> bytes:
        return self._check(
            self.client.get(path, params=params, headers=self.headers)
        ).content

    def get_bytes(self, path: str) -> bytes:
        return self._check(self.client.get(path, headers=self.headers)).content

    def post_form(
        self,
        path: str,
        fields: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> Any:
        """A multipart POST: form fields and, when given, files as
        ``{field: (name, bytes, media type)}``."""
        return self._check(
            self.client.post(path, data=fields, files=files, headers=self.headers)
        ).json()

    def upload(self, path: Path, fields: dict[str, str]) -> Any:
        with path.open("rb") as fh:
            files = {
                "file": (
                    path.name,
                    fh,
                    mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                )
            }
            res = self.client.post(
                "/ingest/file", files=files, data=fields, headers=self.headers
            )
        return self._check(res).json()


# ------------------------------------------------------------- a project
#
# The client half of syncing a project's written knowledge
# (``POST /projects/sync``): which files of a working copy are documents,
# read on the machine that has them, and sent in one call. The door plans
# and keeps the manifest; this decides only what is worth sending, so a
# repository's source and vendored build trees never leave the machine.
# The rule is here, beside the call, because the MCP proxy may import
# nothing else of prax (invariant 5); ``prax.importers.project`` and the
# door use the same one.

PROJECT_DOC_SUFFIXES = (".md", ".markdown", ".rst", ".txt", ".adoc")
PROJECT_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "dist",
        "build",
        "target",
        "out",
        ".idea",
        ".vscode",
        "vendor",
        "third_party",
        "site-packages",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".claude",
        # CMake's: what FetchContent and a build tree leave in a working
        # copy (the first client took in about 90 such files, 2026-10-03)
        "_deps",
        "CMakeFiles",
    }
)
PROJECT_SKIP_DIR_GLOBS = ("*-subbuild", "*-build", "cmake-build-*")
PROJECT_SKIP_FILES = ("license", "licence", "copying", "notice", "requirements")
PROJECT_MAX_BYTES = 2_000_000
PROJECT_EXAMPLES = 5  # paths shown for each reason a file was left out


def glob_matches(posix: str, globs: list[str] | tuple[str, ...]) -> bool:
    """fnmatch with ``**/`` meaning "any depth, including none"."""
    import fnmatch

    return any(
        fnmatch.fnmatch(posix, g) or fnmatch.fnmatch(posix, g.replace("**/", ""))
        for g in globs
    )


def project_skip(
    posix: str,
    size: int,
    *,
    include: list[str] | tuple[str, ...] = (),
    exclude: list[str] | tuple[str, ...] = (),
) -> str | None:
    """Why a file of a project is not one of its documents, or None when it
    is: a build or vendored folder, not a document file (or not included),
    excluded, a licence or requirements file, or over the size cap."""
    import fnmatch

    parts = posix.split("/")
    for part in parts[:-1]:
        if part in PROJECT_SKIP_DIRS or any(
            fnmatch.fnmatch(part, g) for g in PROJECT_SKIP_DIR_GLOBS
        ):
            return "a build or vendored folder"
    name = parts[-1]
    if include:
        if not glob_matches(posix, include):
            return "not included"
    elif not name.lower().endswith(PROJECT_DOC_SUFFIXES):
        return "not a document file"
    if exclude and glob_matches(posix, exclude):
        return "excluded"
    if not include and name.lower().startswith(PROJECT_SKIP_FILES):
        return "a licence or requirements file"
    if size > PROJECT_MAX_BYTES:
        return "over 2 MB"
    return None


def canonical_remote(url: str | None) -> str | None:
    """A git remote as one key however it is written: ``host/owner/repo``,
    without the scheme, a login, a port or ``.git``. ``git@github.com:a/b.git``
    and ``https://user:secret@github.com/a/b`` are both ``github.com/a/b``,
    and no secret leaves the machine."""
    import re

    if not url or not url.strip():
        return None
    u = url.strip()
    scp = re.match(r"^[\w.-]+@([\w.-]+):(?!\d+/)(.+)$", u)  # git@host:path
    if scp:
        host, path = scp.group(1), scp.group(2)
    else:
        u = re.sub(r"^[a-z][a-z0-9+.-]*://", "", u, flags=re.IGNORECASE)
        head, _, path = u.partition("/")
        host = head.rsplit("@", 1)[-1]
    host = host.split(":")[0].lower()
    path = path.strip("/")
    path = path.removesuffix(".git")
    return f"{host}/{path}" if host and path else None


def _git(cwd: Path, *args: str) -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            check=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.decode("utf-8", "replace")


def project_files(
    root: str | Path,
    *,
    include: list[str] | tuple[str, ...] = (),
    exclude: list[str] | tuple[str, ...] = (),
    tracked_only: bool = True,
    texts: bool = True,
) -> dict[str, Any]:
    """A working copy's documents as ``POST /projects/sync`` takes them:
    ``remote`` (``canonical_remote`` of ``origin``), ``prefix`` (``root``
    within its repository, so a subdirectory is a project of its own),
    ``files`` (path relative to ``root``, bytes, sha256, modified, and the
    text unless ``texts`` is false, which a dry run needs not) and
    ``skipped`` (per reason, a count and a few paths). In a git working
    copy only tracked files count, unless ``tracked_only`` is false."""
    import hashlib
    from datetime import UTC, datetime

    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"{root}: not a directory")
    top_text = _git(base, "rev-parse", "--show-toplevel")
    top = Path(top_text.strip()).resolve() if top_text and top_text.strip() else None
    remote = None
    if top is not None:
        remote = canonical_remote(_git(top, "config", "--get", "remote.origin.url"))
    prefix = base.relative_to(top).as_posix() if top and base != top else ""
    listed = _git(base, "ls-files", "-z") if top and tracked_only else None
    if listed is not None:
        paths = sorted(p for p in listed.split("\0") if p)
    else:
        paths = sorted(
            p.relative_to(base).as_posix()
            for p in base.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(base).parts
        )
    files: list[dict[str, Any]] = []
    skipped: dict[str, dict[str, Any]] = {}

    def skip(why: str, path: str) -> None:
        row = skipped.setdefault(why, {"count": 0, "examples": []})
        row["count"] += 1
        if len(row["examples"]) < PROJECT_EXAMPLES:
            row["examples"].append(path)

    for rel in paths:
        path = base / rel
        try:
            size = path.stat().st_size
        except OSError:
            continue  # listed but gone: a deletion not yet committed
        why = project_skip(rel, size, include=include, exclude=exclude)
        if why:
            skip(why, rel)
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            skip("not text", rel)
            continue
        if not text.strip():
            skip("empty", rel)
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        row: dict[str, Any] = {
            "path": rel,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "modified": modified.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if texts:
            row["text"] = text
        files.append(row)
    return {
        "root_name": base.name,
        "remote": remote,
        "prefix": prefix,
        "tracked": listed is not None,
        "files": files,
        "skipped": skipped,
    }
