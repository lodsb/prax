"""What this machine has left: free RAM, commit headroom, and this
process's own footprint. No dependency: Windows through the kernel's
``GlobalMemoryStatusEx`` and ``GetProcessMemoryInfo``, Linux through
``/proc``; elsewhere the numbers are ``None``. The Jobs view shows the
door host's numbers so the commit wall is visible before it is hit (a
GPU model server on Windows charges system commit for its VRAM)."""

from __future__ import annotations

import contextlib
import ctypes
import sys
from pathlib import Path
from typing import Any

MB = 1024 * 1024


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
