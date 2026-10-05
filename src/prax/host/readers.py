"""What a reader is (docs/PLAN.md, AJ): a reader of pages that runs as
processes of its own — marker and its OCR server — declared here as data,
the way a pack declares its parts (``prax.packs.base``).

A manifest says which roles of ``prax up`` the reader needs, what each
holds of the machine (the card, RAM, the time to load), the settings prax
passes it, the environment it is pinned to (a lock file beside this
module, upgraded on purpose and measured), and the logs it writes itself,
which prax trims. ``prax.host.roles`` builds the roles from it, and the
door's demand (``prax.work.role_work``) names the role a reading waits
for from it. Nothing on the card is started behind ``prax up``'s back: a
companion process the reader would start for itself is a role instead,
and the reader is told where it is.

Nothing here starts, reads or imports anything heavy: the numbers are
the manifest's word until ``prax up`` has measured a load
(``run/loads.json``) and the host its memory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

LOCKS = Path(__file__).with_name("locks")  # one pinned environment per reader
LOG_KEEP_MB = 2  # what is kept of a reader's own log, its last lines


@dataclass(frozen=True)
class Process:
    """One process a reader needs, and what it holds when it runs."""

    role: str  # the role of ``prax up``
    vram_mb: int | None = None  # of the card, with its model on it
    ram_mb: int | None = None  # resident at its peak
    load_s: float | None = None  # start to ready, until prax has measured it
    note: str = ""  # where the numbers come from


@dataclass(frozen=True)
class Companion:
    """A process the reader would otherwise start itself, run as a role of
    ``prax up`` that moves with the reader in a swap. The reader is told
    where it is (``env``, the server's base URL and ``path``), and the
    server must answer to ``alias``, the model name the reader checks."""

    role: str
    env: str  # the reader's setting that points it at the server
    path: str = ""  # appended to the server's base URL
    alias: str | None = None  # the model name the reader expects to be served


@dataclass(frozen=True)
class Reader:
    name: str
    role: str  # its own process, a role of ``prax up``
    extractors: tuple[str, ...]  # the extractors whose readings it serves
    processes: tuple[Process, ...]  # its own first, then its companions'
    companions: tuple[Companion, ...] = ()
    env: dict[str, str] = field(default_factory=dict)  # settings it is always passed
    package: str | None = None  # the distribution whose version it reads as
    lock: str | None = None  # the lock file under ``locks/``: its pinned venv
    logs: tuple[str, ...] = ()  # the files it writes itself (``~`` expanded)

    def process(self, role: str) -> Process | None:
        return next((p for p in self.processes if p.role == role), None)

    def companion(self, role: str) -> Companion | None:
        return next((c for c in self.companions if c.role == role), None)

    def alone(self) -> Process:
        """The reader's own process when no companion role runs: it starts
        them inside its own tree, so it holds their share as well."""
        own, rest = self.processes[0], self.processes[1:]

        def total(values: list[int | None]) -> int | None:
            got = [v for v in values if v]
            return sum(got) if got else None

        return Process(
            own.role,
            vram_mb=total([own.vram_mb, *(p.vram_mb for p in rest)]),
            ram_mb=total([own.ram_mb, *(p.ram_mb for p in rest)]),
            load_s=own.load_s,
            note=own.note,
        )


# marker 2.0 (howto 3h): its layout and OCR-error helpers run inside its
# server; its OCR is surya-ocr-2, a GGUF vision model behind a llama-server
# surya starts for itself unless SURYA_INFERENCE_URL names one. The
# numbers are the night of 2026-10-02 (docs/PLAN.md, AJ step 1), before
# the prompt cache was capped: the OCR server's RAM is the one to measure
# again
MARKER = Reader(
    name="marker",
    role="marker",
    extractors=("marker",),
    processes=(
        Process(
            "marker",
            vram_mb=1600,
            ram_mb=5200,
            note="2026-10-02: its server 3.4 GB, the layout and OCR-error"
            " helpers 1.8 GB; the card: 5 GB in all less the OCR server's",
        ),
        Process(
            "ocr-server",
            vram_mb=3400,
            ram_mb=2000,
            note="2026-10-02: 3.4 GB of the card; 10.3 GB of RAM with"
            " llama.cpp's 8 GB prompt cache, which --cache-ram 0 removes",
        ),
    ),
    companions=(
        Companion(
            "ocr-server",
            env="SURYA_INFERENCE_URL",
            path="/v1",
            alias="datalab-to/surya-ocr-2",  # surya refuses a server that is not
        ),
    ),
    env={"SURYA_INFERENCE_BACKEND": "llamacpp"},
    package="marker-pdf",
    lock="marker.txt",
    logs=(
        # surya's servers write here and never rotate (78 MB after one night)
        "~/.cache/datalab/surya/llamacpp_server.log",
        "~/.cache/datalab/surya/fast_layout_server.log",
        "~/.cache/datalab/surya/ocr_error_server.log",
    ),
)

READERS: dict[str, Reader] = {MARKER.name: MARKER}


def of_role(role: str) -> Reader | None:
    """The reader whose own process is ``role``."""
    return next((r for r in READERS.values() if r.role == role), None)


def companion_of(role: str) -> tuple[Reader, Companion] | None:
    """The reader that ``role`` is a companion of, and how."""
    for reader in READERS.values():
        found = reader.companion(role)
        if found is not None:
            return reader, found
    return None


def role_work() -> dict[str, tuple[str, ...]]:
    """Which role each reader's readings wait for: its own."""
    return {r.role: r.extractors for r in READERS.values()}


# ------------------------------------------------------- the pinned venv


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked(reader: Reader) -> dict[str, str]:
    """The reader's lock file as ``{distribution: version}``: ``pip
    freeze`` lines, comments and options left out."""
    if not reader.lock:
        return {}
    path = LOCKS / reader.lock
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" not in line or line.startswith("-"):
            continue
        name, version = line.split("==", 1)
        out[_canonical(name.split("[", 1)[0])] = version.strip()
    return out


def site_packages(venv: Path) -> Path | None:
    """The venv's site-packages: ``Lib/`` on Windows, ``lib/pythonX.Y/``
    elsewhere."""
    for candidate in (
        venv / "Lib" / "site-packages",
        *venv.glob("lib/python*/site-packages"),
    ):
        if candidate.is_dir():
            return candidate
    return None


def installed(venv: Path) -> dict[str, str]:
    """What the venv holds, ``{distribution: version}``, from its
    ``*.dist-info`` folders: no interpreter started."""
    site = site_packages(venv)
    if site is None:
        return {}
    out: dict[str, str] = {}
    for info in site.glob("*.dist-info"):
        name, _, version = info.name[: -len(".dist-info")].partition("-")
        if version:
            out[_canonical(name)] = version
    return out


def drift(reader: Reader, venv: Path) -> list[str]:
    """Where the venv differs from the reader's lock, one line each
    (``marker-pdf 2.1.0, locked 2.0.0``; ``missing``; ``not locked``),
    the reader's own package first. Empty when they agree or the reader
    has no lock."""
    want = locked(reader)
    if not want:
        return []
    have = installed(venv)
    if not have:
        return [f"no packages found in {venv}"]
    out: list[str] = []
    for name in sorted(set(want) | set(have)):
        a, b = want.get(name), have.get(name)
        if a == b:
            continue
        if a is None:
            out.append(f"{name} {b}, not locked")
        elif b is None:
            out.append(f"{name} missing, locked {a}")
        else:
            out.append(f"{name} {b}, locked {a}")
    first = _canonical(reader.package or "")
    out.sort(key=lambda line: not line.startswith(first + " ") if first else False)
    return out


def write_lock(reader: Reader, venv: Path) -> Path:
    """The venv's packages as the reader's lock: an upgrade made on
    purpose becomes the pinned environment (and is measured)."""
    if not reader.lock:
        raise ValueError(f"reader {reader.name!r} has no lock file")
    have = installed(venv)
    if not have:
        raise ValueError(f"no packages found in {venv}")
    path = LOCKS / reader.lock
    head = [
        f"# {reader.name}'s pinned venv (prax.host.readers): install with",
        f"#   pip install -r {reader.lock}",
        "# and change it only with `prax up --lock`, after the upgrade is measured",
    ]
    lines = [f"{name}=={version}" for name, version in sorted(have.items())]
    path.write_text("\n".join(head + lines) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------- its own logs


def trim_log(path: Path, keep_mb: float = LOG_KEEP_MB) -> int:
    """The file cut to its last ``keep_mb``, from a line start; the bytes
    it gave up (0 when it was small or not there). Only for a file nobody
    holds open: a process appending to it would write past the new end."""
    keep = int(keep_mb * (1 << 20))
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if size <= keep:
        return 0
    with path.open("rb") as f:
        f.seek(size - keep)
        tail = f.read()
    cut = tail.find(b"\n")
    tail = tail[cut + 1 :] if 0 <= cut < len(tail) - 1 else tail
    path.write_bytes(tail)
    return size - len(tail)
