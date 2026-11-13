"""The Claude Code plugin: its files are well formed, its commands say
what they are, and its session-end hook syncs a project that opted in
and leaves every other project alone."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from prax_cli import main as cli

from prax.client import Door

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "clients" / "claude-plugin"


def test_the_plugin_is_well_formed() -> None:
    manifest = json.loads(
        (PLUGIN / ".claude-plugin" / "plugin.json").read_text("utf-8")
    )
    assert manifest["name"] == "prax"
    mcp = json.loads((PLUGIN / ".mcp.json").read_text("utf-8"))
    assert mcp["mcpServers"]["prax"]["args"] == ["-m", "prax.mcp_server"]
    assert "PRAX_DOOR" in mcp["mcpServers"]["prax"]["env"]
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text("utf-8"))
    (end,) = hooks["hooks"]["SessionEnd"]
    # a launcher that finds a Python, not a bare `python` (the Store's stub
    # on Windows, which never ran the hook)
    assert "scripts/sync.sh" in end["hooks"][0]["command"]
    assert (PLUGIN / "scripts" / "sync.sh").is_file()
    assert (PLUGIN / "scripts" / "sync.py").is_file()
    skill = (PLUGIN / "skills" / "prax" / "SKILL.md").read_text("utf-8")
    assert skill.startswith("---\nname: prax\ndescription: ")
    for name in ("scope", "research", "remember", "sync", "archive"):
        text = (PLUGIN / "commands" / f"{name}.md").read_text("utf-8")
        assert text.startswith("---\ndescription: "), name
    market = json.loads(
        (ROOT / ".claude-plugin" / "marketplace.json").read_text("utf-8")
    )
    assert market["plugins"][0]["source"] == "./clients/claude-plugin"
    # every tool the skill names exists on the server
    from prax import mcp_server

    tools = {t.name for t in __import__("asyncio").run(mcp_server.mcp.list_tools())}
    for tool in ("search", "get_chunk", "context", "documents", "traverse", "ask"):
        assert f"`{tool}(" in skill and tool in tools


def _sync_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "prax_sync", PLUGIN / "scripts" / "sync.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_session_end_hook_syncs_only_projects_that_opted_in(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prax.api import app

    sync = _sync_module()
    project = tmp_path / "gadget"
    project.mkdir()
    (project / "NOTES.md").write_text("# Gadget\n\nA decision.\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    with TestClient(app) as client:
        monkeypatch.setattr(
            cli,
            "_door_of",
            lambda a: Door("http://testserver", client=client, name="t"),
        )
        assert sync.main() == 0  # no .prax-project: nothing happens
        assert client.get("/documents").json()["total"] == 0
        (project / ".prax-project").write_text(
            "domains: [workshop]\n", encoding="utf-8"
        )
        assert sync.main() == 0
        docs = client.get("/documents", params={"tag": "project:gadget"}).json()
        assert docs["total"] == 1 and docs["items"][0]["meta"]["domains"] == [
            "workshop"
        ]
        # a rewritten note replaces itself on the next session's end
        (project / "NOTES.md").write_text(
            "# Gadget\n\nA better decision.\n", encoding="utf-8"
        )
        assert sync.main() == 0
        docs = client.get("/documents", params={"tag": "project:gadget"}).json()
        assert docs["total"] == 1
        assert "better" in client.get(f"/get/{docs['items'][0]['id']}").json()["text"]
    assert os.environ["CLAUDE_PROJECT_DIR"] == str(project)


def test_the_hook_archives_transcripts_and_memory_when_asked(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_importers import _transcript  # tests/ is on sys.path under pytest

    from prax.api import app
    from prax.importers import claude

    sync = _sync_module()
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    project = tmp_path / "gadget"
    project.mkdir()
    (project / "NOTES.md").write_text("# Gadget" + chr(10), encoding="utf-8")
    (project / ".prax-project").write_text(
        "domains: [workshop]" + chr(10) + "archive: [transcripts, memory]" + chr(10),
        encoding="utf-8",
    )
    assert sync.archive_choices(project) == ["transcripts", "memory"]
    store_dir = claude.transcripts_for(project)
    (store_dir / "memory").mkdir(parents=True)
    (store_dir / "memory" / "MEMORY.md").write_text(
        "- a fact" + chr(10), encoding="utf-8"
    )
    _transcript(store_dir / "s1.jsonl", "s1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    with TestClient(app) as client:
        monkeypatch.setattr(
            cli,
            "_door_of",
            lambda a: Door("http://testserver", client=client, name="t"),
        )
        assert sync.main() == 0
        rows = client.get("/documents", params={"limit": 50}).json()["items"]
        # the project's documents, its page (made by the first sync), the session
        assert {d["meta"]["source"] for d in rows} == {"project", "wiki", "claude"}
        memory = client.get("/documents", params={"tag": "project:gadget-memory"})
        assert memory.json()["total"] == 1
        sessions = client.get("/documents", params={"source": "claude"})
        assert sessions.json()["total"] == 1


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
def test_the_hook_finds_a_python_and_never_the_stores_stub(tmp_path: Path) -> None:
    """The hook's command was a bare ``python``, on a Windows machine the
    Microsoft Store's stub: the sync never started and nothing said so
    (the tt01 session, 2026-11-13). The launcher takes ``PRAX_PYTHON``
    first and skips a stub under ``WindowsApps``."""
    launcher = PLUGIN / "scripts" / "sync.sh"

    def fake(folder: Path, name: str, says: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        script = folder / name
        script.write_bytes(f'#!/bin/sh\necho "{says} $*"\n'.encode())
        script.chmod(0o755)
        return script

    chosen = fake(tmp_path / "venv", "python", "chosen")
    env = {**os.environ, "PRAX_PYTHON": str(chosen)}
    got = subprocess.run(
        ["sh", str(launcher)], env=env, capture_output=True, text=True, check=True
    )
    assert got.stdout.startswith("chosen ") and "sync.py" in got.stdout
    stub = fake(tmp_path / "WindowsApps", "python", "STUB")
    bare = os.pathsep.join(
        [str(stub.parent), *(p for p in ("/usr/bin", "/bin") if Path(p).is_dir())]
    )
    env = {k: v for k, v in os.environ.items() if k != "PRAX_PYTHON"}
    got = subprocess.run(
        ["sh", str(launcher)],
        env={**env, "PATH": bare},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "STUB" not in got.stdout
