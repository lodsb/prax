"""What every router of the door shares: the request's connection, the
small helpers the capture handlers and the work protocol both use, and
the body cap."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from prax import config, inbox, store


def _con(request: Request) -> Any:
    """The connection for this request's thread (reads in parallel, writes
    one at a time behind the store's lock)."""
    return store.thread_connection()


def _split(csv: str | None) -> list[str] | None:
    items = [s.strip() for s in (csv or "").split(",") if s.strip()]
    return items or None


def _capture_out(cap: inbox.Capture) -> dict[str, Any]:
    return {
        "doc_id": cap.doc_id,
        "created": cap.created,
        "indexed": cap.indexed,
        "domains": cap.domains,
        "previous_capture": cap.previous,
        "mime": cap.mime,
        "duplicate_of": cap.duplicate_of,
        "replaced": cap.replaced,
    }


def max_upload() -> int:
    """The largest request body the door takes, in bytes
    (``door.max_upload_mb`` in ``prax.yaml``, ``PRAX_MAX_UPLOAD_MB``;
    256 MB). An upload is read into memory before it is archived, and the
    serving host is Pi-class (invariant 7)."""
    return int(config.number("door.max_upload_mb", "PRAX_MAX_UPLOAD_MB", 256) * 1024**2)
