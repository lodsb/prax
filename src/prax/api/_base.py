"""What every router of the door shares: the request's connection, the
small helpers the capture handlers and the work protocol both use, and
the body cap."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Request
from pydantic_core import core_schema

from prax import config, store
from prax.capture import inbox


@dataclass(frozen=True)
class AtMost:
    """A list route's ``limit``: 1 to ``most``, a number outside clamped
    rather than refused (a client that over-asks gets the most), the bounds
    shown in the OpenAPI schema the clients read. Invariant 6 and 7 rest
    on these caps; until 2026-11-09 twelve routes clamped by hand and the
    rest passed any number to the store (structure review finding 47)."""

    most: int

    def __get_pydantic_core_schema__(self, source: Any, handler: Any) -> Any:
        return core_schema.no_info_after_validator_function(
            lambda v: max(1, min(int(v), self.most)), handler(source)
        )

    def __get_pydantic_json_schema__(self, schema: Any, handler: Any) -> Any:
        out = handler(schema)
        out.update(minimum=1, maximum=self.most)
        return out


# a page of a list a person reads, or a client pages through: the largest
# caller asks for 500 (the UI's region labels, the feed importer)
PAGE_MOST = 500
Page = Annotated[int, AtMost(PAGE_MOST)]


def _con(request: Request) -> Any:
    """The connection for this request's thread (reads in parallel, writes
    one at a time behind the store's lock)."""
    return store.thread_connection()


def _split(csv: str | None) -> list[str] | None:
    items = [s.strip() for s in (csv or "").split(",") if s.strip()]
    return items or None


def ui_url(request: Request, doc_id: int) -> str:
    """Where a person opens the document: the UI at the address the caller
    reached the door by (the first client found ``#doc/N`` by reading the
    UI's source, 2026-10-03)."""
    return f"{str(request.base_url).rstrip('/')}/ui/#doc/{doc_id}"


def _capture_out(cap: inbox.Capture, request: Request) -> dict[str, Any]:
    hidden = store.hidden_documents(_con(request))
    if cap.doc_id in hidden:
        # the same bytes as a document the caller may not see: accepted,
        # nothing created, nothing named
        return {"doc_id": None, "created": False, "indexed": False, "mime": cap.mime}

    def shown(doc: int | None) -> int | None:
        return None if doc is None or doc in hidden else doc

    return {
        "doc_id": cap.doc_id,
        "url": ui_url(request, cap.doc_id),
        "created": cap.created,
        "indexed": cap.indexed,
        "domains": cap.domains,
        "previous_capture": shown(cap.previous),
        "mime": cap.mime,
        "duplicate_of": shown(cap.duplicate_of),
        "replaced": shown(cap.replaced),
    }


def max_upload() -> int:
    """The largest request body the door takes, in bytes
    (``door.max_upload_mb`` in ``prax.yaml``, ``PRAX_MAX_UPLOAD_MB``;
    256 MB). An upload is read into memory before it is archived, and the
    serving host is Pi-class (invariant 7)."""
    return int(config.number("door.max_upload_mb", "PRAX_MAX_UPLOAD_MB", 256) * 1024**2)
