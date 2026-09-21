"""The login entry: the one thing an operating system does for prax.

``prax up --install`` writes an entry that starts ``prax up`` when you log
in and starts it again if it is gone — a Task Scheduler task on Windows
(``\\prax\\prax up``, run by ``pythonw.exe`` so it never has a console), a
systemd user unit on Linux (``~/.config/systemd/user/prax.service``), a
launchd agent on macOS (``~/Library/LaunchAgents/io.github.lodsb.prax.plist``).
Under your own account, nothing system-wide, no password stored: the
services run while you are logged on. Secrets never go into the entry;
``prax.up.environment`` says where they come from.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from prax import config, up

LABEL = "io.github.lodsb.prax"
TASK_PATH = "\\prax\\"
TASK_NAME = "prax up"
UNIT = "prax.service"
DESCRIPTION = "prax up: the door, the worker and the model server (prax.yaml, run:)"


def _python_without_console() -> str:
    exe = Path(up._python())
    if sys.platform == "win32":
        quiet = exe.with_name("pythonw.exe")
        if quiet.is_file():
            return str(quiet)
    return str(exe)


def command(data_dir: Path, *, tray: bool | None = None) -> list[str]:
    """The login entry's command: ``prax up`` on the store, with a tray
    icon on a desktop (Windows, macOS) when the tray library is here;
    a Linux user unit runs without one (no display in a unit's session)."""
    argv = [
        _python_without_console(),
        "-m",
        "prax_cli.main",
        "up",
        "--data-dir",
        str(data_dir),
    ]
    if tray is None:
        tray = sys.platform in ("win32", "darwin") and tray_available()
    if tray:
        argv.append("--tray")
    return argv


def tray_available() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def where() -> str:
    """Where the entry lives on this platform."""
    if sys.platform == "win32":
        return f"Task Scheduler, {TASK_PATH}{TASK_NAME}"
    if sys.platform == "darwin":
        return str(_plist_path())
    return str(_unit_path())


def install(data_dir: Path) -> list[str]:
    """Write the entry and start it now; the lines to show."""
    if sys.platform == "win32":
        return _install_windows(data_dir)
    if sys.platform == "darwin":
        return _install_macos(data_dir)
    return _install_linux(data_dir)


def uninstall() -> list[str]:
    if sys.platform == "win32":
        return _uninstall_windows()
    if sys.platform == "darwin":
        return _uninstall_macos()
    return _uninstall_linux()


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


# ------------------------------------------------------------ Windows


def _ps(script: str) -> str:
    """Run a PowerShell script; its error text raises."""
    done = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script])
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip() or "PowerShell failed")
    return done.stdout


def _q(text: str) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + text.replace("'", "''") + "'"


def _install_windows(data_dir: Path) -> list[str]:
    exe, *args = command(data_dir)
    argument = " ".join(f'"{a}"' if " " in a else a for a in args)
    user = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
    script = f"""
$action = New-ScheduledTaskAction -Execute {_q(exe)} -Argument {_q(argument)} `
    -WorkingDirectory {_q(str(config.REPO_ROOT))}
$trigger = New-ScheduledTaskTrigger -AtLogOn -User {_q(user)}
$trigger.Delay = 'PT15S'
$principal = New-ScheduledTaskPrincipal -UserId {_q(user)} `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskPath {_q(TASK_PATH)} -TaskName {_q(TASK_NAME)} `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description {_q(DESCRIPTION)} -Force | Out-Null
"""
    _ps(script)
    lines = [
        f"registered {TASK_PATH}{TASK_NAME} in Task Scheduler: at logon, under {user},",
        f"  {exe} {argument}",
    ]
    if exe.lower().endswith("python.exe"):
        lines.append(
            "  (no pythonw.exe beside the interpreter: a console window will show)"
        )
    if up.running_pid(data_dir) is None:
        _ps(f"Start-ScheduledTask -TaskPath {_q(TASK_PATH)} -TaskName {_q(TASK_NAME)}")
        lines.append("started it now")
    else:
        lines.append(
            "prax up is already running; the task takes over at the next logon"
        )
    return lines


def _uninstall_windows() -> list[str]:
    script = f"""
$t = Get-ScheduledTask -TaskPath {_q(TASK_PATH)} -TaskName {_q(TASK_NAME)} `
    -ErrorAction SilentlyContinue
if (-not $t) {{ 'none'; exit 0 }}
Unregister-ScheduledTask -TaskPath {_q(TASK_PATH)} -TaskName {_q(TASK_NAME)} `
    -Confirm:$false
'removed'
"""
    said = _ps(script).strip()
    if said == "none":
        return [f"no {TASK_PATH}{TASK_NAME} task to remove"]
    return [
        f"removed {TASK_PATH}{TASK_NAME} from Task Scheduler",
        "(a running prax up keeps running: prax up --stop)",
    ]


# -------------------------------------------------------------- Linux


def _unit_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "systemd" / "user" / UNIT


def unit_text(data_dir: Path) -> str:
    exe, *args = command(data_dir)
    return f"""[Unit]
Description={DESCRIPTION}
Documentation=https://github.com/lodsb/prax
After=network-online.target

[Service]
Type=simple
WorkingDirectory={config.REPO_ROOT}
Environment=PRAX_DATA_DIR={data_dir}
Environment=PYTHONUNBUFFERED=1
ExecStart={exe} {" ".join(args)}
# the supervisor gets SIGTERM and stops its processes in order; whatever is
# left after the timeout is killed with it
KillMode=mixed
TimeoutStopSec=45
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def _install_linux(data_dir: Path) -> list[str]:
    path = _unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text(data_dir), encoding="utf-8")
    lines = [f"wrote {path}"]
    for argv in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", UNIT],
    ):
        done = _run(argv)
        if done.returncode != 0:
            lines.append(f"{' '.join(argv)}: {(done.stderr or done.stdout).strip()}")
            return lines
    lines.append(f"enabled and started {UNIT} (systemctl --user status {UNIT})")
    lines.append(
        "it runs from login to logout; `loginctl enable-linger $USER` keeps it"
        " running without a session"
    )
    return lines


def _uninstall_linux() -> list[str]:
    path = _unit_path()
    lines = []
    if path.exists():
        _run(["systemctl", "--user", "disable", "--now", UNIT])
        path.unlink()
        _run(["systemctl", "--user", "daemon-reload"])
        lines.append(f"stopped, disabled and removed {path}")
    else:
        lines.append(f"no {path} to remove")
    return lines


# -------------------------------------------------------------- macOS


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def plist(data_dir: Path) -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": command(data_dir),
        "WorkingDirectory": str(config.REPO_ROOT),
        "EnvironmentVariables": {
            "PRAX_DATA_DIR": str(data_dir),
            "PYTHONUNBUFFERED": "1",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ExitTimeOut": 45,
        "StandardOutPath": str(up.logs_dir(data_dir) / "up.log"),
        "StandardErrorPath": str(up.logs_dir(data_dir) / "up.log"),
    }


def _install_macos(data_dir: Path) -> list[str]:
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    up.logs_dir(data_dir).mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(plist(data_dir), f)
    domain = f"gui/{os.getuid()}"
    _run(["launchctl", "bootout", f"{domain}/{LABEL}"])  # an older copy, if any
    done = _run(["launchctl", "bootstrap", domain, str(path)])
    lines = [f"wrote {path}"]
    if done.returncode != 0:
        lines.append(f"launchctl bootstrap: {(done.stderr or done.stdout).strip()}")
    else:
        lines.append(f"loaded it (launchctl print {domain}/{LABEL})")
    return lines


def _uninstall_macos() -> list[str]:
    path = _plist_path()
    if not path.exists():
        return [f"no {path} to remove"]
    _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
    path.unlink()
    return [f"unloaded and removed {path}"]
