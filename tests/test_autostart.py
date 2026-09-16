"""The login entry per platform: what gets written and what gets run,
with the shell-outs replaced — no test registers anything real."""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path
from typing import Any

import pytest

from prax import autostart, up


@pytest.fixture()
def interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake venv: python.exe with pythonw.exe beside it."""
    exe = tmp_path / "venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    exe.with_name("pythonw.exe").write_bytes(b"MZ")
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe


def test_the_command_has_no_console_on_windows(
    interpreter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    cmd = autostart.command(tmp_path / "data")
    assert cmd[0].endswith("pythonw.exe")
    assert cmd[1:] == [
        "-m",
        "prax_cli.main",
        "up",
        "--data-dir",
        str(tmp_path / "data"),
    ]
    # the children of a pythonw supervisor still get the interpreter with a console
    monkeypatch.setattr(sys, "executable", str(interpreter.with_name("pythonw.exe")))
    assert up.prax_command("serve")[0].endswith("python.exe")
    interpreter.with_name("pythonw.exe").unlink()
    assert autostart.command(tmp_path)[0].endswith("python.exe")
    monkeypatch.setattr(sys, "platform", "linux")
    assert autostart.command(tmp_path)[0] == str(interpreter)


def test_the_systemd_unit(
    interpreter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    text = autostart.unit_text(tmp_path / "data")
    assert (
        f"ExecStart={interpreter} -m prax_cli.main up --data-dir {tmp_path / 'data'}"
        in text
    )
    assert f"Environment=PRAX_DATA_DIR={tmp_path / 'data'}" in text
    assert "Restart=always" in text and "KillMode=mixed" in text
    assert "WantedBy=default.target" in text


def test_the_launchd_agent(
    interpreter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    data = tmp_path / "data"
    agent = autostart.plist(data)
    assert agent["Label"] == autostart.LABEL
    assert agent["ProgramArguments"] == autostart.command(data)
    assert agent["KeepAlive"] is True and agent["RunAtLoad"] is True
    assert agent["StandardOutPath"] == str(data / "logs" / "up.log")
    plistlib.dumps(agent)  # what launchd will read


def test_install_on_windows_registers_one_task_and_starts_it(
    interpreter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERDOMAIN", "BOX")
    monkeypatch.setenv("USERNAME", "me")
    scripts: list[str] = []
    monkeypatch.setattr(autostart, "_ps", lambda script: scripts.append(script) or "")
    data = tmp_path / "data dir"  # a space: the argument must be quoted
    lines = autostart.install(data)
    assert len(scripts) == 2  # register, then start
    register, start = scripts
    assert "Register-ScheduledTask" in register
    assert "-TaskPath '\\prax\\' -TaskName 'prax up'" in register
    assert f"-Execute '{interpreter.with_name('pythonw.exe')}'" in register
    assert f"-Argument '-m prax_cli.main up --data-dir \"{data}\"'" in register
    assert "-UserId 'BOX\\me'" in register
    assert "-LogonType Interactive -RunLevel Limited" in register
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in register
    assert "$trigger.Delay = 'PT15S'" in register
    assert "Start-ScheduledTask" in start
    assert any("started it now" in line for line in lines)
    # with a supervisor already running, the task waits for the next logon
    scripts.clear()
    monkeypatch.setattr(up, "running_pid", lambda d: 4242)
    lines = autostart.install(data)
    assert len(scripts) == 1
    assert any("already running" in line for line in lines)
    # uninstall reports what it found
    monkeypatch.setattr(autostart, "_ps", lambda script: "removed\n")
    assert any("removed" in line for line in autostart.uninstall())
    monkeypatch.setattr(autostart, "_ps", lambda script: "none\n")
    assert any("no " in line for line in autostart.uninstall())


def test_install_on_linux_writes_the_unit_and_enables_it(
    interpreter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    calls: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(autostart, "_run", lambda argv: calls.append(argv) or Done())
    lines = autostart.install(tmp_path / "data")
    unit = tmp_path / "cfg" / "systemd" / "user" / autostart.UNIT
    assert unit.is_file()
    assert "Restart=always" in unit.read_text()
    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", autostart.UNIT],
    ]
    assert any("enable-linger" in line for line in lines)
    calls.clear()
    lines = autostart.uninstall()
    assert not unit.exists()
    assert calls[0] == ["systemctl", "--user", "disable", "--now", autostart.UNIT]
    assert any("removed" in line for line in lines)
    assert any("no " in line for line in autostart.uninstall())


def test_install_on_macos_writes_the_agent_and_loads_it(
    interpreter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(os, "getuid", lambda: 501, raising=False)
    calls: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(autostart, "_run", lambda argv: calls.append(argv) or Done())
    data = tmp_path / "data"
    lines = autostart.install(data)
    agent = tmp_path / "home" / "Library" / "LaunchAgents" / f"{autostart.LABEL}.plist"
    assert agent.is_file()
    with open(agent, "rb") as f:
        loaded: dict[str, Any] = plistlib.load(f)
    assert loaded["ProgramArguments"] == autostart.command(data)
    assert (data / "logs").is_dir()  # launchd writes there from the first line
    assert calls == [
        ["launchctl", "bootout", f"gui/501/{autostart.LABEL}"],
        ["launchctl", "bootstrap", "gui/501", str(agent)],
    ]
    assert any("loaded" in line for line in lines)
    calls.clear()
    assert any("removed" in line for line in autostart.uninstall())
    assert not agent.exists()
    assert calls == [["launchctl", "bootout", f"gui/501/{autostart.LABEL}"]]
