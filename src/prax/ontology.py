"""The versioned ontology: which entity and relation types the graph accepts.

``ontology.yaml`` (repo root, or ``PRAX_ONTOLOGY``) is the single source of
truth. ``store.link`` validates every edge against the current version, so
misfits never enter the graph (CLAUDE.md invariant 9). Growing the ontology
is a version bump in the file; edges keep the ``ontology_version`` they were
written under, so older edges stay interpretable after the schema of types
changes.

File format (all forms are accepted so the file can stay small):

    version: "1"
    entity_types:
      paper: {description: ...}     # mapping form, or
      - concept                     # list of names
    relation_types:
      authored_by:
        domain: [paper]             # allowed src types (omit = any)
        range: [author]             # allowed dst types (omit = any)
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import config


@dataclass(frozen=True)
class Relation:
    name: str
    domain: frozenset[str] = frozenset()  # empty = unconstrained
    range: frozenset[str] = frozenset()
    description: str = ""


@dataclass(frozen=True)
class Ontology:
    version: str
    entity_types: frozenset[str]
    relations: dict[str, Relation] = field(default_factory=dict)

    @property
    def relation_types(self) -> frozenset[str]:
        return frozenset(self.relations)

    def check_edge(self, src_type: str, rel: str, dst_type: str) -> None:
        """Raise ``ValueError`` unless the edge fits this ontology version."""
        for t in (src_type, dst_type):
            if t not in self.entity_types:
                raise ValueError(
                    f"unknown entity type {t!r} (ontology v{self.version} has"
                    f" {sorted(self.entity_types)})"
                )
        r = self.relations.get(rel)
        if r is None:
            raise ValueError(
                f"unknown relation {rel!r} (ontology v{self.version} has"
                f" {sorted(self.relations)})"
            )
        if r.domain and src_type not in r.domain:
            raise ValueError(f"{rel!r} does not accept src type {src_type!r}")
        if r.range and dst_type not in r.range:
            raise ValueError(f"{rel!r} does not accept dst type {dst_type!r}")


def _names(section: Any, what: str) -> dict[str, dict[str, Any]]:
    """Normalize a list-or-mapping section to ``{name: attrs}``."""
    if section is None:
        return {}
    if isinstance(section, dict):
        return {str(k): dict(v or {}) for k, v in section.items()}
    if isinstance(section, list):
        out: dict[str, dict[str, Any]] = {}
        for entry in section:
            if isinstance(entry, str):
                out[entry] = {}
            elif isinstance(entry, dict) and len(entry) == 1:
                ((k, v),) = entry.items()
                out[str(k)] = dict(v or {})
            else:
                raise ValueError(f"bad {what} entry: {entry!r}")
        return out
    raise ValueError(f"{what} must be a list or mapping")


def parse(text: str) -> Ontology:
    data = yaml.safe_load(text) or {}
    if "version" not in data:
        raise ValueError("ontology has no version")
    entities = _names(data.get("entity_types"), "entity_types")
    relations = {}
    for name, attrs in _names(data.get("relation_types"), "relation_types").items():
        for side in ("domain", "range"):
            unknown = set(attrs.get(side) or []) - set(entities)
            if unknown:
                raise ValueError(
                    f"relation {name!r} {side} names unknown types {unknown}"
                )
        relations[name] = Relation(
            name=name,
            domain=frozenset(attrs.get("domain") or []),
            range=frozenset(attrs.get("range") or []),
            description=str(attrs.get("description") or ""),
        )
    return Ontology(
        version=str(data["version"]),
        entity_types=frozenset(entities),
        relations=relations,
    )


def path() -> Path:
    return Path(os.environ.get("PRAX_ONTOLOGY", config.ONTOLOGY_PATH))


@functools.lru_cache(maxsize=4)
def _load(p: Path, mtime_ns: int) -> Ontology:
    return parse(p.read_text(encoding="utf-8"))


def current() -> Ontology:
    """The ontology on disk; re-parsed only when the file changes."""
    p = path()
    return _load(p, p.stat().st_mtime_ns)
