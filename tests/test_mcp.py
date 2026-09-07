"""FastMCP proxy: tools called through the in-process client.

Tools run on a worker thread inside FastMCP, so this also covers the
thread-affinity regression from the skeleton.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from prax import mcp_server

EXPECTED_TOOLS = {
    "search", "get", "get_chunk", "traverse", "link", "ingest", "ingest_file"
}


@pytest.fixture(autouse=True)
def fresh_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test gets its own lazily-opened connection in its own tmp dir."""
    monkeypatch.setattr(mcp_server, "_con", None)
    yield
    if mcp_server._con is not None:
        mcp_server._con.close()


def _data(result: Any) -> Any:
    data = getattr(result, "data", None)
    if data is not None:
        return data
    return json.loads(result.content[0].text)


def call(name: str, **args: Any) -> Any:
    async def _run() -> Any:
        async with Client(mcp_server.mcp) as client:
            return _data(await client.call_tool(name, args))

    return asyncio.run(_run())


def test_tools_are_exposed() -> None:
    async def _run() -> set[str]:
        async with Client(mcp_server.mcp) as client:
            return {t.name for t in await client.list_tools()}

    assert asyncio.run(_run()) == EXPECTED_TOOLS


def test_import_has_no_side_effects(data_dir: Path) -> None:
    assert not (data_dir / "prax.db").exists()


def test_ingest_search_get() -> None:
    r = call("ingest", text="mcp hello world", title="m")
    assert r["created"]
    hits = call("search", query="hello")
    assert hits[0]["doc_id"] == r["doc_id"]
    doc = call("get", doc_id=r["doc_id"])
    assert doc["text"] == "mcp hello world"
    page = call("get", doc_id=r["doc_id"], offset=4, max_chars=5)
    assert page["text"] == "hello" and page["truncated"]


def test_get_unknown_returns_error() -> None:
    assert "error" in call("get", doc_id=999)


def test_search_with_punctuation() -> None:
    call("ingest", text="STFT-based analysis")
    assert call("search", query="STFT-based: (what's) this?")


def test_link_and_traverse() -> None:
    for a, b in [("A", "B"), ("B", "C"), ("C", "D")]:
        r = call("link", src=a, src_type="concept", rel="extends",
                 dst=b, dst_type="concept")
        assert "edge_id" in r
    one = call("traverse", entity="A", hops=1)
    assert {(e["src"], e["dst"]) for e in one} == {("A", "B")}
    two = call("traverse", entity="A", hops=2)
    assert {(e["src"], e["dst"]) for e in two} == {("A", "B"), ("B", "C")}


def test_link_bad_confidence_returns_error() -> None:
    r = call("link", src="A", src_type="x", rel="r", dst="B", dst_type="x",
             confidence="GUESS")
    assert "error" in r


def test_ingest_file(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("granular synthesis from a file", encoding="utf-8")
    r = call("ingest_file", path=str(f))
    assert r["created"]
    doc = call("get", doc_id=r["doc_id"])
    assert doc["mime"] == "text/markdown" and doc["title"] == "note.md"
    assert call("search", query="granular")[0]["doc_id"] == r["doc_id"]


def test_ingest_file_missing_returns_error(tmp_path: Path) -> None:
    assert "error" in call("ingest_file", path=str(tmp_path / "nope.pdf"))

def test_search_reports_kind_and_get_chunk() -> None:
    table = "Table 1: sizes\n\n| part | mm |\n|---|---|\n| bolt | 12 |\n"
    r = call("ingest", text=table, title="t")
    hits = call("search", query="bolt", kind="table")
    assert hits and hits[0]["kind"] == "table" and hits[0]["doc_id"] == r["doc_id"]
    chunk = call("get_chunk", chunk_id=hits[0]["chunk_id"])
    assert chunk["data"]["rows"] == [["bolt", "12"]]
    assert "error" in call("get_chunk", chunk_id=999_999)
    assert "error" in call("search", query="bolt", kind="audio")[0]
