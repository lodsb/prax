"""The supervisor's files and the processes it starts: the pid file, the
status it writes and the commands a client leaves for it (stop, start,
swap, restart), a role's log, and on Windows the job object that takes
the children down with it. What the tray, the autostart entry, the CLI
and the door read and write to talk to a running ``prax up``."""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

KEEP_LOGS = 10
PIDFILE = "up.pid"  # the files under <data dir>/run/
STATUS = "up.json"
COMMANDS = "commands"  # a directory: one file per command, taken in order
LOADS = "loads.json"  # each role's last load times, start to ready


# ------------------------------------------------------------- the files


def run_dir(data_dir: Path) -> Path:
    return data_dir / "run"


def logs_dir(data_dir: Path) -> Path:
    return data_dir / "logs"


def rotate(logs: Path, name: str) -> Path:
    """``<name>.log``, the last one moved aside with its time, ten kept."""
    logs.mkdir(parents=True, exist_ok=True)
    current = logs / f"{name}.log"
    if current.exists() and current.stat().st_size > 0:
        stamp = datetime.fromtimestamp(current.stat().st_mtime, tz=UTC).strftime(
            "%Y%m%d-%H%M%S"
        )
        target = logs / f"{name}.{stamp}.log"
        n = 1
        while target.exists():  # two starts within a second
            n += 1
            target = logs / f"{name}.{stamp}-{n}.log"
        with contextlib.suppress(OSError):
            current.replace(target)
    old = sorted(
        logs.glob(f"{name}.20*.log"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for stale in old[KEEP_LOGS:]:
        with contextlib.suppress(OSError):
            stale.unlink()
    return current


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        still = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(still) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_pid(data_dir: Path) -> int | None:
    """The supervisor's pid when one is alive; a stale file is removed."""
    path = run_dir(data_dir) / PIDFILE
    try:
        pid = int(path.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    if _alive(pid):
        return pid
    with contextlib.suppress(OSError):
        path.unlink()
    return None


def status(data_dir: Path) -> dict[str, Any] | None:
    """The supervisor's last status, or None when none is running."""
    if running_pid(data_dir) is None:
        return None
    try:
        got: dict[str, Any] | None = json.loads(
            (run_dir(data_dir) / STATUS).read_text(encoding="utf-8")
        )
        return got
    except (OSError, ValueError):
        return None


_commands_sent = itertools.count()


def command(data_dir: Path, what: dict[str, Any]) -> None:
    """Queue a command for the running supervisor: one JSON file in the
    commands directory, written under a temporary name and renamed into
    place, so the supervisor never sees a command half written and
    commands a moment apart all arrive, in order. (One file appended to
    and read whole lost commands two ways: a line appended between the
    supervisor's read and its unlink, and the open that created the file
    an instant before a tick read it empty and removed it — both seen on
    the CI runners, where a tick is 50 ms.)"""
    queue = run_dir(data_dir) / COMMANDS
    queue.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns():020d}-{os.getpid()}-{next(_commands_sent):06d}"
    tmp = queue / (name + ".tmp")
    tmp.write_text(json.dumps(what) + "\n", encoding="utf-8")
    os.replace(tmp, queue / (name + ".json"))


def _clear_commands(run_dir_: Path) -> None:
    """Whatever an earlier supervisor left unread, or this one is leaving."""
    with contextlib.suppress(OSError):
        for path in (run_dir_ / COMMANDS).iterdir():
            with contextlib.suppress(OSError):
                path.unlink()


def stop(data_dir: Path, *, wait: float = 45.0, name: str | None = None) -> bool:
    """Ask the running supervisor to stop — everything, or with ``name``
    one role, which stays paused until ``start`` (the card free for an
    hour, the door and the worker untouched); True once it is so."""
    pid = running_pid(data_dir)
    if pid is None:
        return True
    if name and name != "all":
        command(data_dir, {"cmd": "stop", "name": name})
        return _await_state(data_dir, name, {"paused"}, wait)
    command(data_dir, {"cmd": "stop"})
    deadline = time.monotonic() + wait
    pidfile = run_dir(data_dir) / PIDFILE
    while time.monotonic() < deadline:
        # the supervisor removes its pid file as it leaves; a killed one
        # cannot, so a dead pid counts too
        if not pidfile.exists() or not _alive(pid):
            with contextlib.suppress(OSError):
                pidfile.unlink()
            return True
        time.sleep(0.2)
    return False


def swap(data_dir: Path, to: str, *, back_when: str = "idle") -> bool:
    """Ask the supervisor for the group's resource for ``to`` (the card
    for marker, say): what holds it stops unless both fit, and it goes
    back when the door says nothing waits for the borrower."""
    if running_pid(data_dir) is None:
        return False
    command(data_dir, {"cmd": "swap", "to": to, "back_when": back_when})
    return True


def unswap(data_dir: Path, group: str = "all") -> bool:
    """Give a group's resource back now, whatever the borrower still has."""
    if running_pid(data_dir) is None:
        return False
    command(data_dir, {"cmd": "unswap", "group": group})
    return True


def restart(data_dir: Path, name: str) -> bool:
    if running_pid(data_dir) is None:
        return False
    command(data_dir, {"cmd": "restart", "name": name})
    return True


def start(data_dir: Path, name: str, *, wait: float = 45.0) -> bool:
    """Start a paused role again (``all``: every paused one); True once
    it is running or on its way."""
    if running_pid(data_dir) is None:
        return False
    command(data_dir, {"cmd": "start", "name": name})
    if name == "all":
        return True
    return _await_state(data_dir, name, {"starting", "up", "down"}, wait)


def _await_state(data_dir: Path, name: str, states: set[str], wait: float) -> bool:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        snap = status(data_dir)
        if snap is None:  # the file mid-replace, or the supervisor gone
            if running_pid(data_dir) is None:
                return False
            time.sleep(0.1)
            continue
        role = snap.get("roles", {}).get(name)
        if role is None:
            return False  # no such role
        if role.get("state") in states:
            return True
        time.sleep(0.2)
    return False


# ------------------------------------------------------ the process tree


def _spawn_kwargs(*, detached: bool = False) -> dict[str, Any]:
    """No console on Windows, a session of its own elsewhere: nothing a
    terminal does reaches the process."""
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        if detached:
            flags |= subprocess.CREATE_BREAKAWAY_FROM_JOB
        return {"creationflags": flags}
    return {"start_new_session": True}


class _JobObject:
    """A Windows job object with kill-on-close: every child assigned to it
    ends when the supervisor's handle goes, which is when the supervisor
    goes, however it went. A no-op elsewhere."""

    def __init__(self) -> None:
        self.handle: Any = None
        # kernel32, declared here: on Linux mypy reads the Windows branch
        # below as unreachable and could not tell the attribute's type
        self._k32: Any = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_ulonglong)
                for n in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.TerminateJobObject.argtypes = [ctypes.c_void_p, wintypes.UINT]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = (
            0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if k32.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            self.handle = handle
            self._k32 = k32
        else:
            k32.CloseHandle(handle)

    def assign(self, proc: subprocess.Popen[bytes]) -> bool:
        if self.handle is None:
            return False
        return bool(self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)))  # type: ignore[attr-defined,unused-ignore]

    def terminate(self) -> bool:
        """End every process in the job: the role's tree, not just its root
        (marker leaves a llama-server behind otherwise)."""
        if self.handle is None:
            return False
        return bool(self._k32.TerminateJobObject(self.handle, 1))


def healthy(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            got: bool = 200 <= r.status < 300
            return got
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
