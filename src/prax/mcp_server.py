"""FastMCP server — thin proxy over prax.store. Run: python -m prax.mcp_server

No business logic here (CLAUDE.md invariant 5). Same process as the store.
The connection is opened lazily on the first tool call, so importing this
module has no side effects.
"""
from __future__ import annotations

import mimetypes
import sqlite3
import threading
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from . import store

mcp = FastMCP("prax")
_con: sqlite3.Connection | None = None
_init_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    global _con
    with _init_lock:
        if _con is None:
            con = store.connect()
            store.init_db(con)
            _con = con
        return _con


@mcp.tool
def search(
    query: str, limit: int = 10, kind: str | None = None, mode: str = "hybrid"
) -> list[dict[str, Any]]:
    """Search the knowledge base. Returns compact snippets + ids.

    ``hybrid`` (default) fuses BM25 keyword search with vector similarity;
    ``fts`` or ``vec`` force one side. Each hit names its chunk ``kind``
    (text, table, figure, code), section ``heading`` path and ``page``;
    ``kind`` restricts to one kind, e.g. ``kind="table"`` for documents
    with a table about the query. Fetch a hit in full with ``get_chunk``.
    """
    try:
        return store.search(_db(), query, limit, kind=kind, mode=mode)
    except ValueError as exc:
        return [{"error": str(exc)}]


@mcp.tool
def get_chunk(chunk_id: int) -> dict[str, Any]:
    """Fetch one chunk in full (ids come from search results).

    Returns its text, kind, heading path, locator (character range and page
    in the document) and, for tables, ``data`` with header and rows.
    """
    return store.get_chunk(_db(), chunk_id) or {"error": "no such chunk"}


@mcp.tool
def get(doc_id: int, offset: int = 0, max_chars: int = 20000) -> dict[str, Any]:
    """Fetch one document by id (ids come from search results).

    Text is windowed: ``text_len`` and ``truncated`` say whether more
    remains; call again with a larger ``offset`` to page.
    """
    doc = store.get_document(_db(), doc_id, offset=offset, max_chars=max_chars)
    return doc or {"error": "no such document"}


@mcp.tool
def traverse(entity: str, hops: int = 1) -> list[dict[str, Any]]:
    """Expand the knowledge graph 1-2 hops from a named entity."""
    return store.traverse(_db(), entity, hops)


@mcp.tool
def link(
    src: str,
    src_type: str,
    rel: str,
    dst: str,
    dst_type: str,
    confidence: str = "EXTRACTED",
    source_doc: int | None = None,
) -> dict[str, Any]:
    """Add a graph edge between two entities (created if missing).

    ``confidence`` is EXTRACTED, INFERRED, or AMBIGUOUS.
    """
    edge = store.Edge(src, src_type, rel, dst, dst_type)
    try:
        eid = store.link(_db(), edge, confidence=confidence, source_doc=source_doc)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"edge_id": eid}


@mcp.tool
def ingest(
    text: str, title: str | None = None, source_url: str | None = None
) -> dict[str, Any]:
    """Ingest raw text as a new document (deduped by content hash)."""
    return store.ingest_text(_db(), text, title=title, source_url=source_url)


@mcp.tool
def ingest_file(
    path: str, title: str | None = None, source_url: str | None = None
) -> dict[str, Any]:
    """Ingest a file from a path on the server's machine.

    Text files are indexed immediately; binaries (PDF, HTML) are archived
    and left for the parse queue.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return {"error": f"no such file: {path}"}
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return store.ingest_file(
        _db(),
        p.read_bytes(),
        mime=mime,
        title=title or p.name,
        source_url=source_url,
        original_path=str(p),
    )


if __name__ == "__main__":
    mcp.run()  # stdio transport
