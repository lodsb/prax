"""A project's written knowledge: the documentation files under a
directory, one document each.

What a project decides and why lives in its Markdown — the README, the
design notes, the ADRs, `CLAUDE.md`, a `NOTES.md` — while the source
lives in git and is not knowledge to keep twice. This reader walks a
directory for those files (``.md``, ``.rst``, ``.txt``, ``.adoc``; never
`.git`, `node_modules`, a venv, build output, vendored code), keys each
by ``<project>/<relative path>`` and versions it by the content's hash,
so ``--refresh`` replaces a rewritten design note instead of adding a
second copy. Tagged ``project:<name>``; the ontology modules come from
the command line or from the project's own ``.prax-project`` file:

    name: synth-firmware          # default: the directory's name
    domains: [workshop, studio]   # modules the documents are read against
    tags: [firmware]              # extra tags on every document
    include: ["docs/**/*.md", "README.md", "adr/*.md"]   # default: every doc file
    exclude: ["docs/generated/**"]

That file is also what the Claude Code plugin's session-end hook looks
for before it syncs a project (``clients/claude-plugin/``).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from prax import client

from .feed import Item

SOURCE = "project"
SETTINGS_FILE = ".prax-project"
# which files are a project's documents is one rule, the client's
# (``prax.client.project_skip``), shared with ``sync_project`` and the door
DOC_SUFFIXES = client.PROJECT_DOC_SUFFIXES
MAX_BYTES = client.PROJECT_MAX_BYTES
_HEADING = re.compile(r"^#{1,3}\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class Settings:
    name: str
    domains: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)


def settings(root: Path, *, name: str | None = None) -> Settings:
    """The project's ``.prax-project`` when it has one, else the defaults;
    ``name`` overrides the file's."""
    cfg: dict[str, Any] = {}
    path = root / SETTINGS_FILE
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{path}: expected a mapping")
        cfg = loaded

    def words(key: str) -> list[str]:
        value = cfg.get(key) or []
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",")]
        return [str(v) for v in value if str(v).strip()]

    return Settings(
        name=name or str(cfg.get("name") or root.resolve().name),
        domains=words("domains"),
        tags=words("tags"),
        include=words("include"),
        exclude=words("exclude"),
    )


def files(root: Path, cfg: Settings) -> Iterator[Path]:
    """The documentation files, in one order on every platform."""
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        posix = path.relative_to(root).as_posix()
        why = client.project_skip(
            posix, path.stat().st_size, include=cfg.include, exclude=cfg.exclude
        )
        if why is None:
            yield path


def items(root: Path, cfg: Settings) -> Iterator[Item]:
    for path in files(root, cfg):
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue  # not text after all
        if not text.strip():
            continue
        rel = path.relative_to(root).as_posix()
        m = (
            _HEADING.search(text)
            if path.suffix.lower() in (".md", ".markdown")
            else None
        )
        heading = m.group(1).strip() if m else None
        title = f"{heading or rel} ({cfg.name})"
        digest = hashlib.sha256(raw).hexdigest()[:16]
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        yield Item(
            key=f"{cfg.name}/{rel}",
            title=title,
            text=text,
            tags=[f"project:{cfg.name}", *cfg.tags],
            meta={
                "name": cfg.name,
                "path": rel,
                "bytes": len(raw),
                "modified": modified.isoformat(timespec="seconds"),
            },
            version=digest,
        )
