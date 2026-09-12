"""MCP server: a proxy of the HTTP door. Run: python -m prax.mcp_server

Every tool is one call to the door (``PRAX_DOOR``, default
``http://127.0.0.1:8000``, with ``PRAX_TOKEN`` when the door asks for one)
through ``prax.client.Door``; this process imports no store module and
opens no database (CLAUDE.md invariants 4 and 5). No business logic here:
the door's handlers are the contract, and an error from the door comes
back as ``{"error": ...}`` so the model can read it.
"""

from __future__ import annotations

import mimetypes
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from prax.client import Door, DoorError

mcp = MCPServer(
    "prax",
    instructions=(
        "A personal research library: search returns compact hits with ids,"
        " get and get_chunk open them, traverse walks the graph, ask bundles"
        " passages for a question, pages keep what is worth keeping."
    ),
)
_door: Door | None = None


def configure(
    *, base_url: str | None = None, token: str | None = None, client: Any = None
) -> Door:
    """Point the proxy at a door (tests pass a test client)."""
    global _door
    _door = Door(
        base_url or os.environ.get("PRAX_DOOR") or "http://127.0.0.1:8000",
        token=token if token is not None else os.environ.get("PRAX_TOKEN"),
        client=client,
        name="mcp",
    )
    return _door


def door() -> Door:
    return _door or configure()


def _guard(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except DoorError as exc:
        return {"error": exc.detail or str(exc)}
    except (OSError, httpx.HTTPError) as exc:  # not running, not reachable
        return {
            "error": f"the door is not reachable ({exc}); PRAX_DOOR={door().base_url}"
        }


def _guarded_list(call: Callable[[], Any]) -> list[dict[str, Any]]:
    out = _guard(call)
    return out if isinstance(out, list) else [out]


@mcp.tool()
def search(
    query: str,
    limit: int = 10,
    kind: str | None = None,
    mode: str = "hybrid",
    rerank: bool | None = None,
    domain: str | None = None,
    doctype: str | None = None,
) -> list[dict[str, Any]]:
    """Search the knowledge base. Returns compact snippets + ids.

    ``hybrid`` (default) fuses BM25 keyword search with vector similarity;
    ``fts`` or ``vec`` force one side. Each hit names its chunk ``kind``
    (text, table, figure, code), section ``heading`` path and ``page``;
    ``kind`` restricts to one kind, e.g. ``kind="table"`` for documents
    with a table about the query. ``rerank=True`` rescores the top hits
    with a cross-encoder when one is configured. ``domain`` keeps the
    documents of one ontology module (``research``, ``studio``; documents
    without a domain set are in every module); ``doctype`` keeps pdf,
    web, image, text, note or page documents. Fetch a hit in full with
    ``get_chunk``.
    """
    params: dict[str, Any] = {"q": query, "limit": limit, "mode": mode}
    for k, v in (
        ("kind", kind),
        ("rerank", rerank),
        ("domain", domain),
        ("doctype", doctype),
    ):
        if v is not None:
            params[k] = v
    return _guarded_list(lambda: door().get_json("/search", params))


@mcp.tool()
def get_chunk(chunk_id: int) -> dict[str, Any]:
    """Fetch one chunk in full (ids come from search results).

    Returns its text, kind, heading path, locator (character range and page
    in the document) and, for tables, ``data`` with header and rows.
    """
    return _guard(lambda: door().get_json(f"/chunk/{chunk_id}"))


@mcp.tool()
def get(doc_id: int, offset: int = 0, max_chars: int = 20000) -> dict[str, Any]:
    """Fetch one document by id (ids come from search results).

    Text is windowed: ``text_len`` and ``truncated`` say whether more
    remains; call again with a larger ``offset`` to page.
    """
    return _guard(
        lambda: door().get_json(
            f"/get/{doc_id}", {"offset": offset, "max_chars": max_chars}
        )
    )


@mcp.tool()
def traverse(entity: str, hops: int = 1) -> list[dict[str, Any]]:
    """Expand the knowledge graph 1-2 hops from a named entity."""
    return _guarded_list(
        lambda: door().get_json("/traverse", {"entity": entity, "hops": hops})
    )


@mcp.tool()
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
    return _guard(
        lambda: door().post_json(
            "/link",
            {
                "src": src,
                "src_type": src_type,
                "rel": rel,
                "dst": dst,
                "dst_type": dst_type,
                "confidence": confidence,
                "source_doc": source_doc,
                "producer": "agent",
            },
        )
    )


@mcp.tool()
def ask(
    question: str,
    limit: int = 8,
    doctype: str | None = None,
    answer: bool = False,
) -> dict[str, Any]:
    """The context for answering a question from the library: one numbered
    passage per document from the hybrid search (best chunk, with chunk
    and document ids) and what the graph records about those documents.
    Answer from it and cite passages as [n]; ``append_page`` keeps an
    answer worth keeping. ``answer=True`` also runs the door host's
    configured model (a local model takes seconds to tens of seconds) and
    returns its answer with resolved citations. ``doctype`` keeps pdf,
    web, image, text, note or page documents.
    """
    body: dict[str, Any] = {"question": question, "limit": limit, "doctype": doctype}
    if not answer:
        body["backend"] = "none"
    return _guard(lambda: door().post_json("/ask", body))


@mcp.tool()
def set_domains(doc_id: int, domains: list[str] | None) -> dict[str, Any]:
    """Which ontology modules a document is read against (its domains, e.g.
    ["research"], ["family", "research"] for a document that is both, or
    null for every module). A document extracted afterwards, or in a re-run
    per domain, uses only those modules' types and relations."""
    return _guard(
        lambda: door().put_json(
            f"/doc/{doc_id}/domains", {"domains": domains, "by": "agent"}
        )
    )


@mcp.tool()
def promote(doc_id: int, reason: str | None = None) -> dict[str, Any]:
    """Flag a document for the expensive model's pass (a richer extraction
    with claims and relations between methods) when it turned out to matter:
    cited in an answer, central to a question, worth a page. The pass itself
    runs later as a batch; this only queues."""
    return _guard(
        lambda: door().post_json(
            f"/doc/{doc_id}/promote", {"reason": reason, "by": "agent"}
        )
    )


@mcp.tool()
def get_page(slug: str) -> dict[str, Any]:
    """A page of the library's wiki: its Markdown text, kind (addendum,
    project, topic), author of the latest revision and revision list."""
    return _guard(lambda: door().get_json(f"/page/{slug}"))


@mcp.tool()
def write_page(
    slug: str,
    text: str,
    title: str | None = None,
    kind: str = "topic",
    annotates: list[int] | None = None,
    part_of: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Create a page (kind addendum, project or topic) or replace its text
    as the agent. Refused when a person wrote the latest revision: use
    append_page then. ``annotates`` lists document ids the page is about;
    ``part_of`` names a project page's slug. Cite what you read as
    chunk ids or document ids in the text so readers can check."""
    return _guard(
        lambda: door().put_json(
            f"/page/{slug}",
            {
                "text": text,
                "title": title,
                "kind": kind,
                "author": "agent",
                "note": note,
                "annotates": annotates,
                "part_of": part_of,
            },
        )
    )


@mcp.tool()
def append_page(
    slug: str, section: str, heading: str | None = None, note: str | None = None
) -> dict[str, Any]:
    """Add a section to an existing page as the agent, leaving what a
    person wrote untouched."""
    return _guard(
        lambda: door().post_json(
            f"/page/{slug}/append",
            {"section": section, "heading": heading, "author": "agent", "note": note},
        )
    )


@mcp.tool()
def ingest(
    text: str, title: str | None = None, source_url: str | None = None
) -> dict[str, Any]:
    """Ingest raw text as a new document (deduped by content hash)."""
    return _guard(
        lambda: door().post_json(
            "/ingest", {"text": text, "title": title, "source_url": source_url}
        )
    )


@mcp.tool()
def capture_url(
    url: str, title: str | None = None, domains: list[str] | None = None
) -> dict[str, Any]:
    """Fetch a web page or file by URL and keep it: a page is indexed at
    once, a PDF waits for the worker. ``domains`` names the ontology
    modules it belongs to (e.g. ["research"])."""
    return _guard(
        lambda: door().post_json(
            "/ingest/url",
            {"url": url, "title": title, "domains": domains, "by": "agent"},
        )
    )


@mcp.tool()
def ingest_file(
    path: str, title: str | None = None, source_url: str | None = None
) -> dict[str, Any]:
    """Ingest a file from a path on this machine (the one running the MCP
    server); it is uploaded to the door.

    Text files are indexed immediately; binaries (PDF, HTML) are archived
    and left for the worker.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return {"error": f"no such file: {path}"}
    fields = {"title": title or p.name}
    if source_url:
        fields["source_url"] = source_url
    mimetypes.guess_type(p.name)  # the upload names the type from the file name
    return _guard(lambda: door().upload(p, fields))


if __name__ == "__main__":
    mcp.run()  # stdio transport
