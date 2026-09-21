"""The tray icon's model: what it says and offers for a supervisor's
status, without a display; the login entry carries --tray on a desktop."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from prax import autostart, tray

UP = {
    "pid": 1,
    "roles": {
        "llama-server": {"state": "up"},
        "marker": {"state": "paused"},
        "door": {"state": "up"},
        "worker": {"state": "down", "exit": 1},
    },
}


def test_title_and_wellness() -> None:
    assert tray.title_of(None) == "prax — not running"
    assert tray.title_of({"pid": 1, "roles": {}}) == "prax — no roles"
    assert (
        tray.title_of(UP)
        == "prax — llama-server up · marker paused · door up · worker down"
    )
    assert tray.is_well(UP) is False  # the worker is down
    well = {"pid": 1, "roles": {"door": {"state": "up"}, "marker": {"state": "paused"}}}
    assert tray.is_well(well) is True
    assert (
        tray.is_well(None) is False and tray.is_well({"pid": 1, "roles": {}}) is False
    )


def test_the_menu_as_data() -> None:
    rows = tray.entries_of(UP, supervising=True)
    assert rows[0] == {
        "label": "Open prax",
        "action": "open",
        "default": True,
        "enabled": True,
    }
    roles = [r for r in rows if r.get("children")]
    assert [r["label"] for r in roles] == [
        "llama-server: up",
        "marker: paused",
        "door: up",
        "worker: down",
    ]
    assert [c["action"] for c in roles[0]["children"]] == ["restart", "stop"]
    assert roles[1]["children"] == [
        {"label": "start", "action": "start", "args": "marker"}
    ]
    assert rows[-2]["action"] == "logs" and rows[-1]["label"] == "Quit prax"
    # nothing running: the tray alone offers to start it; the supervising one cannot
    alone = tray.entries_of(None, supervising=False)
    assert [r["action"] for r in alone] == ["open", None, "start-all", "logs", "quit"]
    assert alone[-1]["label"] == "Quit the tray"
    with_up = tray.entries_of(None, supervising=True)
    assert [r["action"] for r in with_up] == ["open", None, "logs", "quit"]
    # beside a supervisor of its own: stop it all, or only close the icon
    beside = tray.entries_of(UP, supervising=False)
    assert [r["action"] for r in beside[-3:]] == ["logs", "stop-all", "quit"]
    assert beside[-2]["label"] == "Stop prax" and beside[-1]["label"] == "Quit the tray"


def test_the_door_url_follows_the_run_section(monkeypatch: pytest.MonkeyPatch) -> None:
    from prax import config

    monkeypatch.setattr(config, "setting", lambda *a, **k: {"door": {"port": 8765}})
    assert tray.door_url() == "http://127.0.0.1:8765/ui/"
    monkeypatch.setattr(config, "setting", lambda *a, **k: None)
    assert tray.door_url() == "http://127.0.0.1:8000/ui/"


def test_the_login_entry_carries_the_tray_on_a_desktop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(autostart, "tray_available", lambda: True)
    assert autostart.command(tmp_path)[-1] == "--tray"
    monkeypatch.setattr(autostart, "tray_available", lambda: False)
    assert "--tray" not in autostart.command(tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(autostart, "tray_available", lambda: True)
    assert "--tray" not in autostart.command(tmp_path)  # a unit has no display
    assert autostart.command(tmp_path, tray=True)[-1] == "--tray"


def test_the_icon_ships_with_the_package() -> None:
    assert tray.ICON.is_file() and tray.ICON.stat().st_size > 500
