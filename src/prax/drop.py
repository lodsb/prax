"""A drop folder's rules, shared by the door (``prax.inbox`` consumes its
own folder) and the worker (``prax.worker`` uploads a folder of its own
to the door). Nothing here touches the store.

A file is taken once it has settled (``SETTLE_SECONDS`` since its last
write: a download still growing is left for the next scan) unless its
name says it is not finished (``SKIP_SUFFIXES``) or hidden. A sidecar
``report.pdf.json`` beside ``report.pdf`` carries what the sender said
about it (title, source_url, domains, tags, session, by) and travels
with the file; ``notes.json`` on its own is a file. What the store
refuses is moved to ``failed/`` beside the rest, and never looked at
again.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("prax.drop")

SETTLE_SECONDS = 2.0  # a file still being written is left for the next scan
SKIP_SUFFIXES = (".part", ".crdownload", ".tmp", ".download")
FAILED_DIR = "failed"


@dataclass
class Dropped:
    """One file ready to be taken: its path, its sidecar (None when it
    has none) and what the sidecar said."""

    path: Path
    sidecar: Path | None
    extra: dict[str, Any] = field(default_factory=dict)


def sidecar(path: Path) -> tuple[Path | None, dict[str, Any]]:
    """The sidecar beside ``path`` and its contents; an unreadable one is
    reported and counts as empty."""
    side = path.with_name(path.name + ".json")
    if not side.is_file():
        return None, {}
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("%s: unreadable sidecar: %s", side, exc)
        return side, {}
    return side, data if isinstance(data, dict) else {}


def is_sidecar_name(path: Path) -> bool:
    """``report.pdf.json`` is a sidecar for ``report.pdf``; ``notes.json``
    is a file of its own."""
    stem = path.name[:-5]
    return "." in stem and not stem.startswith(".")


def settled(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime >= SETTLE_SECONDS
    except OSError:
        return False


def walk(folder: Path) -> Iterator[Dropped | None]:
    """Every file of the folder that is ready, oldest name first, each with
    its sidecar; ``None`` for one that is still coming (a file not yet
    settled, a sidecar whose file is not there), which a caller counts as
    waiting. The ``failed/`` directory is left alone."""
    failed_dir = folder / FAILED_DIR
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        if failed_dir in path.parents or not path.exists():
            continue  # a sidecar moved with its file
        if path.name.startswith(".") or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.suffix == ".json" and is_sidecar_name(path):
            if not path.with_name(path.name[:-5]).exists():
                yield None  # the file it belongs to is still coming
            continue  # handled with its file
        if not settled(path):
            yield None
            continue
        side, extra = sidecar(path)
        yield Dropped(path, side, extra)


def fail(folder: Path, item: Dropped) -> None:
    """Move a refused file and its sidecar to ``failed/``."""
    import shutil

    failed_dir = folder / FAILED_DIR
    failed_dir.mkdir(exist_ok=True)
    shutil.move(str(item.path), failed_dir / item.path.name)
    if item.sidecar:
        shutil.move(str(item.sidecar), failed_dir / item.sidecar.name)


def taken(item: Dropped) -> None:
    """Remove a file the store took, and its sidecar."""
    item.path.unlink()
    if item.sidecar:
        item.sidecar.unlink()
