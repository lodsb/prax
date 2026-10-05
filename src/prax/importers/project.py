"""A project's ``.prax-project`` file: what the command line sends with
a sync when the file is there.

A project's documents go to the door one way, ``POST /projects/sync``
(``prax.capture.projects``), from ``prax sync``, ``prax import project``
and the MCP tool ``sync_project`` alike; which files count is
``prax.client.project_skip``. Before prax kept a project's settings
itself, they lived in this file, and the plugin's session-end hook still
syncs a project that has one (``clients/claude-plugin/``):

    name: synth-firmware          # default: the directory's name
    domains: [workshop, studio]   # modules the documents are read against
    tags: [firmware]              # extra tags on every document
    include: ["docs/**/*.md", "README.md", "adr/*.md"]   # default: every doc file
    exclude: ["docs/generated/**"]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SETTINGS_FILE = ".prax-project"


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
