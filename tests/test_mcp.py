"""The MCP server is a proxy of the door: every tool is one HTTP call, the
process imports no store module, errors come back as {"error": ...}."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import mcp_server

EXPECTED_TOOLS = {
    "search",
    "get",
    "get_chunk",
    "traverse",
    "link",
    "ingest",
    "ingest_file",
    "get_page",
    "write_page",
    "append_page",
    "ask",
    "promote",
    "set_domains",
    "capture_url",
}


@pytest.fixture(autouse=True)
def proxied(data_dir: Path) -> Iterator[TestClient]:
    """The proxy talks to a door started on this test's data directory."""
    from prax.api import app

    with TestClient(app) as client:
        mcp_server.configure(client=client, base_url="http://testserver", token="")
        yield client
    mcp_server._door = None


def _data(result: Any) -> Any:
    sc = result.structured_content
    if sc is not None:
        return sc.get("result", sc) if isinstance(sc, dict) and "result" in sc else sc
    texts = [c.text for c in result.content]
    if len(texts) == 1:
        return json.loads(texts[0])
    return [json.loads(t) for t in texts]


def call(name: str, **args: Any) -> Any:
    return _data(asyncio.run(mcp_server.mcp.call_tool(name, args)))


def test_tools_are_exposed() -> None:
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS


def test_the_proxy_imports_no_store() -> None:
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, prax.mcp_server;"
                " print(sorted(m for m in sys.modules if m.startswith('prax.')))"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env={**__import__("os").environ, "PYTHONPATH": "src"},
    )
    loaded = json.loads(out.stdout.replace("'", '"'))
    assert "prax.store" not in loaded and "prax.api" not in loaded
    assert loaded == ["prax.client", "prax.mcp_server"]


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


def test_link_and_traverse(proxied: TestClient) -> None:
    for a, b in [("A", "B"), ("B", "C"), ("C", "D")]:
        r = call(
            "link", src=a, src_type="concept", rel="extends", dst=b, dst_type="concept"
        )
        assert "edge_id" in r
    one = call("traverse", entity="A", hops=1)
    assert {(e["src"], e["dst"]) for e in one} == {("A", "B")}
    assert one[0]["producer"] == "agent"
    two = call("traverse", entity="A", hops=2)
    assert {(e["src"], e["dst"]) for e in two} == {("A", "B"), ("B", "C")}


def test_link_bad_confidence_returns_error() -> None:
    r = call(
        "link",
        src="A",
        src_type="x",
        rel="r",
        dst="B",
        dst_type="x",
        confidence="GUESS",
    )
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


def test_page_tools() -> None:
    r = call("write_page", slug="fdn-notes", text="# FDN\n\nAgent text.", kind="topic")
    assert r["created"] and r["revision"] == 1
    page = call("get_page", slug="fdn-notes")
    assert page["text"].startswith("# FDN") and page["author"] == "agent"
    assert (
        call("append_page", slug="fdn-notes", section="More.", heading="Later")[
            "revision"
        ]
        == 2
    )
    assert "Later" in call("get_page", slug="fdn-notes")["text"]
    assert "error" in call("get_page", slug="missing")
    assert "error" in call("write_page", slug="x", text="t", kind="diary")


def test_domains_and_promote_as_the_agent(proxied: TestClient) -> None:
    r = call("ingest", text="a paper about reverb " * 20, title="p")
    assert call("set_domains", doc_id=r["doc_id"], domains=["research"]) == {
        "domains": ["research"]
    }
    assert proxied.get(f"/doc/{r['doc_id']}/domains").json()["domains"] == ["research"]
    p = call("promote", doc_id=r["doc_id"], reason="central")
    assert p.get("by") == "agent" or p.get("promote", {}).get("by") == "agent"
    assert "error" in call("promote", doc_id=99_999)


def test_ask_returns_the_bundle() -> None:
    call("ingest", text="feedback delay networks make reverberation " * 10, title="r")
    r = call("ask", question="how is reverberation made?")
    assert r["passages"] and r["answer"] is None


def test_unreachable_door_is_an_error_not_a_crash() -> None:
    mcp_server.configure(base_url="http://127.0.0.1:9", token="")
    r = call("search", query="anything")
    assert "error" in r[0] and "not reachable" in r[0]["error"]
