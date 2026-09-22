"""What this machine has left: free RAM, commit headroom, the cards'
memory, and this process's own footprint. No dependency: Windows
through the kernel's ``GlobalMemoryStatusEx`` and
``GetProcessMemoryInfo``, Linux through ``/proc``, the cards through
``nvidia-smi``; elsewhere the numbers are ``None`` or empty. The Jobs
view shows the door host's numbers so the commit wall is visible before
it is hit (a GPU model server on Windows charges system commit for its
VRAM), and ``prax up`` reads the cards to decide whether two roles that
both want one fit together (``docs/howto.md`` 4b)."""

from __future__ import annotations

import contextlib
import ctypes
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

MB = 1024 * 1024
GPU_FRESH = 2.0  # seconds a reading of the cards is served again
_gpu: tuple[float, list[dict[str, Any]]] | None = None


def memory() -> dict[str, int | None]:
    """``ram_total_mb``, ``ram_free_mb``, ``commit_limit_mb``,
    ``commit_free_mb`` (commit: RAM plus page file on Windows,
    ``CommitLimit`` minus ``Committed_AS`` on Linux)."""
    out: dict[str, int | None] = {
        "ram_total_mb": None,
        "ram_free_mb": None,
        "commit_limit_mb": None,
        "commit_free_mb": None,
    }
    with contextlib.suppress(Exception):  # a missing number is not an error
        if sys.platform == "win32":
            out.update(_windows_memory())
        elif Path("/proc/meminfo").exists():
            out.update(_linux_memory())
    return out


def gpu(fresh: float = GPU_FRESH) -> list[dict[str, Any]]:
    """Each card as ``{index, name, total_mb, used_mb, free_mb}``, newest
    reading kept ``fresh`` seconds; ``[]`` without ``nvidia-smi`` (no
    card, another make, a machine that has none). Never raises: a host
    that cannot say is a host with no numbers."""
    global _gpu
    now = time.monotonic()
    if _gpu is not None and now - _gpu[0] < fresh:
        return [dict(c) for c in _gpu[1]]
    cards: list[dict[str, Any]] = []
    with contextlib.suppress(Exception):  # no nvidia-smi, or it said nothing
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 5 or not parts[0].isdigit():
                continue
            cards.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "total_mb": int(float(parts[2])),
                    "used_mb": int(float(parts[3])),
                    "free_mb": int(float(parts[4])),
                }
            )
    _gpu = (now, cards)
    return [dict(c) for c in cards]


def vram_free_mb() -> int | None:
    """The freest card's free memory, or None when there is no card to
    ask: what decides whether a role that wants the card can have it
    beside the one that holds it."""
    cards = gpu()
    return max((c["free_mb"] for c in cards), default=None) if cards else None


def process_mb() -> int | None:
    """This process's footprint: private bytes on Windows (what it charges
    against commit), resident set on Linux."""
    try:
        if sys.platform == "win32":
            return _windows_process_mb()
        if Path("/proc/self/status").exists():
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:  # noqa: BLE001
        return None
    return None


# ---- Windows


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _windows_memory() -> dict[str, int]:
    kernel32: Any = ctypes.windll.kernel32  # type: ignore[attr-defined]
    st = _MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        raise OSError("GlobalMemoryStatusEx failed")
    return {
        "ram_total_mb": st.ullTotalPhys // MB,
        "ram_free_mb": st.ullAvailPhys // MB,
        "commit_limit_mb": st.ullTotalPageFile // MB,  # RAM + page file
        "commit_free_mb": st.ullAvailPageFile // MB,
    }


def _windows_process_mb() -> int:
    kernel32: Any = ctypes.windll.kernel32  # type: ignore[attr-defined]
    psapi: Any = ctypes.windll.psapi  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p  # a pseudo-handle
    pmc = _PROCESS_MEMORY_COUNTERS_EX()
    pmc.cb = ctypes.sizeof(pmc)
    if not psapi.GetProcessMemoryInfo(
        ctypes.c_void_p(kernel32.GetCurrentProcess()), ctypes.byref(pmc), pmc.cb
    ):
        raise OSError("GetProcessMemoryInfo failed")
    return int(pmc.PrivateUsage) // MB


# ---- Linux


def _linux_memory() -> dict[str, int]:
    kb: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            kb[key] = int(parts[0])
    limit = kb.get("CommitLimit", 0)
    return {
        "ram_total_mb": kb.get("MemTotal", 0) // 1024,
        "ram_free_mb": kb.get("MemAvailable", 0) // 1024,
        "commit_limit_mb": limit // 1024,
        "commit_free_mb": max(0, limit - kb.get("Committed_AS", 0)) // 1024,
    }
