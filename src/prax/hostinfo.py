"""What this machine has left: free RAM, commit headroom, the cards'
memory, and this process's own footprint. No dependency: Windows
through the kernel's ``GlobalMemoryStatusEx`` and
``GetProcessMemoryInfo``, Linux through ``/proc``, the cards through
``nvidia-smi``; elsewhere the numbers are ``None`` or empty. The Jobs
view shows the door host's numbers so the commit wall is visible before
it is hit (a GPU model server on Windows charges system commit for its
VRAM), and ``prax up`` reads the cards to decide whether two roles that
both want one fit together (``docs/howto.md`` 4b).

``holders()`` says *which* processes hold the card, which the totals
cannot: on 2026-09-25 the embedder ran at 9 chunks/s instead of 331 for
most of a day because llama-server had 20.8 GB of a 24 GB card and
DirectML could not get a device, and prax could say the card was full
without saying by whom."""

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
_holders: tuple[float, list[dict[str, Any]]] | None = None


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


def holders(fresh: float = GPU_FRESH) -> list[dict[str, Any]]:
    r"""What each process holds of the cards, biggest first, as
    ``{pid, name, mb}``; ``[]`` where the host cannot say.

    ``nvidia-smi`` answers this on Linux. On Windows it reports
    ``[N/A]`` per process under WDDM, so the number comes from the
    performance counter ``\GPU Process Memory(*)\Dedicated Usage``
    instead — one PowerShell call, cached like the card totals.
    """
    global _holders
    now = time.monotonic()
    if _holders is not None and now - _holders[0] < fresh:
        return [dict(h) for h in _holders[1]]
    found: list[dict[str, Any]] = []
    with contextlib.suppress(Exception):  # a host that cannot say says nothing
        found = _windows_holders() if sys.platform == "win32" else _nvidia_holders()
    found.sort(key=lambda h: -h["mb"])
    _holders = (now, found)
    return [dict(h) for h in found]


def _nvidia_holders() -> list[dict[str, Any]]:
    out = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    ).stdout
    found = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3 or not parts[0].isdigit() or not parts[2].isdigit():
            continue
        found.append(
            {"pid": int(parts[0]), "name": Path(parts[1]).name, "mb": int(parts[2])}
        )
    return found


_HOLDERS_PS = (
    r"(Get-Counter '\GPU Process Memory(*)\Dedicated Usage'"
    " -ErrorAction SilentlyContinue).CounterSamples |"
    " Where-Object CookedValue -gt 52428800 |"
    " ForEach-Object { $p = ($_.InstanceName -split '_')[1];"
    " $n = (Get-Process -Id $p -ErrorAction SilentlyContinue).ProcessName;"
    ' "$p`t$n`t$([math]::Round($_.CookedValue/1MB))" }'
)


def _windows_holders() -> list[dict[str, Any]]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _HOLDERS_PS],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    ).stdout
    by_pid: dict[int, dict[str, Any]] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0].strip().isdigit():
            continue
        pid, name, mb = int(parts[0]), parts[1].strip(), parts[2].strip()
        if not mb.isdigit() or not name:
            continue
        # one process can hold several allocations; they add up
        row = by_pid.setdefault(pid, {"pid": pid, "name": name, "mb": 0})
        row["mb"] += int(mb)
    return list(by_pid.values())


def room(need_mb: int, *, card: int = 0) -> dict[str, Any]:
    """Whether ``need_mb`` of a card is free, and what is in the way.

    The question a batch should ask before it starts rather than after it
    is slow: an embedder that cannot get a device does not fail, it falls
    back to the CPU and runs at a thirtieth of the speed, which looks
    like a slow model rather than a full card (2026-09-25).

    Returns ``fits``, ``free_mb``, ``total_mb``, the ``holders`` biggest
    first, and ``free_by`` — the fewest of them whose memory would be
    enough, so a caller can say what to stop rather than only that it
    cannot run. Everything is ``None`` or empty on a host with no card,
    where ``fits`` is ``None``: not knowing is not the same as no.
    """
    cards = gpu()
    if not cards or card >= len(cards):
        return {
            "fits": None,
            "need_mb": need_mb,
            "free_mb": None,
            "total_mb": None,
            "holders": [],
            "free_by": [],
        }
    c = cards[card]
    free = int(c.get("free_mb") or 0)
    held = holders()
    want = max(0, need_mb - free)
    enough: list[dict[str, Any]] = []
    got = 0
    for h in held:  # biggest first, so the shortest list of things to stop
        if got >= want:
            break
        enough.append(h)
        got += int(h["mb"])
    return {
        "fits": free >= need_mb,
        "need_mb": need_mb,
        "free_mb": free,
        "total_mb": int(c.get("total_mb") or 0),
        "holders": held,
        "free_by": [] if free >= need_mb else enough,
    }


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
