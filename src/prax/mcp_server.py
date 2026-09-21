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
    a figure hit's ``figure`` is the reference of its image (the
    snippet is what a vision model saw in it). ``kind`` restricts to
    one kind, e.g. ``kind="table"`` for documents with a table about
    the query, ``kind="figure"`` for pictures. ``rerank=True`` rescores the top hits
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
    history: list[dict[str, str]] | None = None,
    steps: int | None = None,
) -> dict[str, Any]:
    """The context for answering a question from the library: one numbered
    passage per document from the hybrid search (best chunk, with chunk
    and document ids) and what the graph records about those documents.
    Answer from it and cite passages as [n]; ``append_page`` keeps an
    answer worth keeping. ``answer=True`` also runs the door host's
    configured model and returns its answer with resolved citations: it
    surfs first — searches again, reads on, walks the graph, drops what
    is beside the point — for ``steps`` steps (the host's default; 0 is
    one shot, a local model then takes tens of seconds instead of a
    minute or two) and the result carries the ``trail``. ``doctype``
    keeps pdf, web, image, text, note or page documents. ``history`` is
    the conversation so far as ``[{question, answer}, …]``: a follow-up
    ("and the second method?") searches in the earlier question's
    neighbourhood and the model sees the earlier turns.
    """
    body: dict[str, Any] = {"question": question, "limit": limit, "doctype": doctype}
    if history:
        body["history"] = history
    if steps is not None:
        body["steps"] = steps
    if not answer:
        body["backend"] = "none"
    return _guard(lambda: door().post_json("/ask", body))


@mcp.tool()
def set_domains(doc_id: int, domains: list[str] | None) -> dict[str, Any]:
    """Which ontology modules a document is read against (its domains, e.g.
    ["research"], ["family", "research"] for a document that is both, or
    null for every module). A document extracted afterwards, or in a re-run
    per domain, uses only those modules' types and relations; a change
    under an extraction makes it stale (``reread`` true in the answer): the
    worker's next extract pass reads the document again against the new
    set and retires the old reading."""
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


def _brief(rows: Any, keys: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    out = []
    for r in list(rows or [])[:limit]:
        if isinstance(r, dict):
            out.append({k: r[k] for k in keys if k in r})
    return out


@mcp.tool()
def context(
    doc_id: int | None = None, slug: str | None = None, limit: int = 8
) -> dict[str, Any]:
    """What places a document (by id) or a page (by slug) in the library,
    compactly: its title and summary, the entities its edges point at,
    what it cites and what cites it, the nearest documents, the notes on
    it, and — for a project page — its members. Where ``search`` finds
    things, ``context`` says what the library already knows around one;
    a session on a project starts with ``context(slug="project/<name>")``.
    Follow an id with ``get`` or ``get_chunk``; a name with ``traverse``.
    """

    def call() -> dict[str, Any]:
        d = door()
        if doc_id is None:
            if not slug:
                return {"error": "give a doc_id or a page slug"}
            page = d.get_json(f"/page/{slug}")
            target = page["doc_id"]
        else:
            target = doc_id
        ctx = d.get_json(f"/doc/{target}/context", {"limit": limit})
        return {
            "doc_id": ctx.get("doc_id"),
            "title": ctx.get("title"),
            "summary": (ctx.get("summary") or "")[:1200],
            "page": ctx.get("page"),
            "entities": _brief(ctx.get("entities"), ("name", "type", "rel"), 40),
            "cites": _brief(ctx.get("cites"), ("doc_id", "title"), limit),
            "cited_by": _brief(ctx.get("cited_by"), ("doc_id", "title"), limit),
            "similar": _brief(ctx.get("similar"), ("doc_id", "title", "score"), limit),
            "notes": _brief(ctx.get("notes"), ("doc_id", "slug", "title"), limit),
            "members": _brief(ctx.get("members"), ("doc_id", "title"), 40),
        }

    return _guard(call)


@mcp.tool()
def documents(
    domain: str | None = None,
    tag: str | None = None,
    source: str | None = None,
    title: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List documents, newest first, without their text: by ontology
    module (``domain``), by tag (``project:<name>``, ``chat:<name>``,
    ``github:<topic>``), by source (``zotero``, ``capture``, ``github``,
    ``chat``, ``project``), or by a title substring. Each row has the id,
    title, mime, source and tags; read one with ``get``."""

    def call() -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        for k, v in (
            ("domain", domain),
            ("tag", tag),
            ("source", source),
            ("title", title),
        ):
            if v:
                params[k] = v
        page = door().get_json("/documents", params)
        rows = []
        for r in page.get("items") or []:
            meta = r.get("meta") or {}
            rows.append(
                {
                    "doc_id": r.get("id"),
                    "title": r.get("title"),
                    "mime": r.get("mime"),
                    "source": meta.get("source"),
                    "tags": meta.get("tags") or [],
                    "domains": meta.get("domains"),
                    "created_at": r.get("created_at"),
                }
            )
        return rows

    return _guarded_list(call)


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
    chunk ids or document ids in the text so readers can check; a link
    ``[title](#doc/N)`` in the text is an edge to that document. A
    standing question inside the page is an ask block, ``<!-- prax:ask
    id=q1 "the question" -->`` on one line and ``<!-- /prax:ask id=q1
    -->`` on the next: the door answers it between the markers and asks
    again when the library learns something (``get_page`` lists them as
    ``blocks``)."""
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
