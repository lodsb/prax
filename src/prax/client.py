"""The door as its clients see it: a few JSON calls, one download and one
upload over HTTP. The worker (``prax.worker``) and the MCP server
(``prax.mcp_server``) speak to the store through this and nothing else, so
neither opens the database (CLAUDE.md invariant 4). ``client`` may be an
httpx client or a test client of the same shape."""

from __future__ import annotations

import mimetypes
import socket
from pathlib import Path
from typing import Any


class DoorError(RuntimeError):
    """The door answered with an error status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"the door answered {status}: {detail}")
        self.status = status
        self.detail = detail


class Door:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        client: Any = None,
        name: str | None = None,
        timeout: float = 600.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = name or socket.gethostname()
        self.headers = {"X-Prax-Worker": self.name}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        if client is None:
            import httpx

            client = httpx.Client(base_url=self.base_url, timeout=timeout)
        self.client = client

    def _check(self, res: Any) -> Any:
        if res.status_code >= 400:
            detail = ""
            try:
                detail = res.json().get("detail", "")
            except Exception:  # noqa: BLE001
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

    def put_json(self, path: str, body: dict[str, Any]) -> Any:
        return self._check(
            self.client.put(path, json=body, headers=self.headers)
        ).json()

    def delete_json(self, path: str) -> Any:
        return self._check(self.client.delete(path, headers=self.headers)).json()

    def get_bytes(self, path: str) -> bytes:
        return self._check(self.client.get(path, headers=self.headers)).content

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
