"""FastMCP server — thin proxy over prax.store. Run: python -m prax.mcp_server

No business logic here (CLAUDE.md invariant 5). Same process as the store.
"""
from __future__ import annotations

from fastmcp import FastMCP

from . import store

mcp = FastMCP("prax")
_con = store.connect()
store.init_db(_con)


@mcp.tool
def search(query: str, limit: int = 10) -> list[dict]:
    """Search the knowledge base (BM25). Returns compact snippets + ids."""
    return store.search(_con, query, limit)


@mcp.tool
def get(doc_id: int) -> dict:
    """Fetch one document fully by id (use ids from search results)."""
    return store.get_document(_con, doc_id) or {"error": "no such document"}


@mcp.tool
def traverse(entity: str, hops: int = 1) -> list[dict]:
    """Expand the knowledge graph 1-2 hops from a named entity."""
    return store.traverse(_con, entity, hops)


@mcp.tool
def link(src: str, src_type: str, rel: str, dst: str, dst_type: str) -> dict:
    """Add a graph edge between two entities (created if missing)."""
    edge = store.Edge(src, src_type, rel, dst, dst_type)
    return {"edge_id": store.link(_con, edge)}


@mcp.tool
def ingest(text: str, title: str | None = None,
           source_url: str | None = None) -> dict:
    """Ingest raw text as a new document (deduped by content hash)."""
    return store.ingest_text(_con, text, title=title, source_url=source_url)


if __name__ == "__main__":
    mcp.run()  # stdio transport
