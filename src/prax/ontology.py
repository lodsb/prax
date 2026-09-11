"""The versioned ontology: which entity and relation types the graph accepts.

``ontology/`` (repo root, or ``PRAX_ONTOLOGY``) holds one YAML file per
module: ``core.yaml`` with the types every domain shares (person,
organization, document, place, event, work, concept, tool) and one file
per domain, ``research.yaml`` today, a ``family.yaml`` or ``production.yaml``
tomorrow. ``store.link`` validates every edge against the composed
ontology, so misfits never enter the graph (CLAUDE.md invariant 9).

A module file::

    module: research
    version: 5
    requires: [core]
    entity_types:
      author: {parent: person, description: ...}
      paper: {parent: document, description: ...}
    relation_types:
      advised_by: {domain: [author], range: [author], description: ...}
    type_aliases: {technique: method}          # what a model may call it
    relation_aliases: {supervised_by: advised_by}

Rules of composition: type and relation names are unique across modules
(the loader refuses a collision); a subtype is accepted wherever its
parent is allowed, so ``affiliated_with`` declared on ``person`` holds for
``author``; a module may only use types of the modules it requires. The
composed version is the modules' versions joined, ``core1+research5``,
stamped on edges and extraction stamps; bumping one module changes the
string, which re-selects documents the way a bump always did.
``for_domains`` narrows the ontology to the core plus named modules, the
prompt a document of that domain gets. A single file without ``module:``
is still accepted as one module with a plain version (tests, small
experiments).
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import config

CORE = "core"


@dataclass(frozen=True)
class EntityType:
    name: str
    module: str
    parent: str | None = None
    description: str = ""


@dataclass(frozen=True)
class Relation:
    name: str
    domain: frozenset[str] = frozenset()  # empty = unconstrained
    range: frozenset[str] = frozenset()
    description: str = ""
    module: str = CORE


@dataclass(frozen=True)
class Module:
    name: str
    version: str
    requires: tuple[str, ...]
    types: dict[str, EntityType]
    relations: dict[str, Relation]
    type_aliases: dict[str, str]
    relation_aliases: dict[str, str]


@dataclass(frozen=True)
class Ontology:
    version: str
    modules: dict[str, Module] = field(default_factory=dict)
    types: dict[str, EntityType] = field(default_factory=dict)
    relations: dict[str, Relation] = field(default_factory=dict)
    type_aliases: dict[str, str] = field(default_factory=dict)
    relation_aliases: dict[str, str] = field(default_factory=dict)

    @property
    def entity_types(self) -> frozenset[str]:
        return frozenset(self.types)

    @property
    def relation_types(self) -> frozenset[str]:
        return frozenset(self.relations)

    def describe(self, name: str) -> str:
        t = self.types.get(name)
        return t.description if t else ""

    def parent(self, name: str) -> str | None:
        t = self.types.get(name)
        return t.parent if t else None

    def ancestors(self, name: str) -> list[str]:
        """The type and its parents, nearest first."""
        out = []
        seen: set[str] = set()
        cur: str | None = name
        while cur and cur not in seen and cur in self.types:
            out.append(cur)
            seen.add(cur)
            cur = self.types[cur].parent
        return out

    def is_a(self, name: str, ancestor: str) -> bool:
        return ancestor in self.ancestors(name)

    def canonical_type(self, name: str) -> str:
        """A type a model named, as this ontology spells it (aliases)."""
        return self.type_aliases.get(name, name)

    def canonical_relation(self, name: str) -> str:
        return self.relation_aliases.get(name, name)

    def _allowed(self, allowed: frozenset[str], t: str) -> bool:
        return not allowed or any(a in allowed for a in self.ancestors(t))

    def check_edge(self, src_type: str, rel: str, dst_type: str) -> None:
        """Raise ``ValueError`` unless the edge fits this ontology: a subtype
        passes wherever its parent is allowed."""
        for t in (src_type, dst_type):
            if t not in self.types:
                raise ValueError(
                    f"unknown entity type {t!r} (ontology {self.version} has"
                    f" {sorted(self.types)})"
                )
        r = self.relations.get(rel)
        if r is None:
            raise ValueError(
                f"unknown relation {rel!r} (ontology {self.version} has"
                f" {sorted(self.relations)})"
            )
        if not self._allowed(r.domain, src_type):
            raise ValueError(f"{rel!r} does not accept src type {src_type!r}")
        if not self._allowed(r.range, dst_type):
            raise ValueError(f"{rel!r} does not accept dst type {dst_type!r}")

    def for_domains(self, domains: list[str] | set[str] | None) -> Ontology:
        """The core plus the named modules (and what they require): the
        ontology a document of those domains is extracted against. None
        or an empty set is the whole ontology."""
        if not domains:
            return self
        wanted: set[str] = {CORE}
        stack = [d for d in domains if d in self.modules]
        while stack:
            m = stack.pop()
            if m in wanted:
                continue
            wanted.add(m)
            stack.extend(self.modules[m].requires)
        return compose([self.modules[m] for m in self.modules if m in wanted])


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


def parse_module(text: str, *, name: str | None = None) -> Module:
    """One module file. Without ``module:`` the file is a module named
    ``name`` (default ``main``) that requires nothing."""
    data = yaml.safe_load(text) or {}
    if "version" not in data:
        raise ValueError("ontology module has no version")
    mod = str(data.get("module") or name or "main")
    types = {
        n: EntityType(
            name=n,
            module=mod,
            parent=str(a["parent"]) if a.get("parent") else None,
            description=str(a.get("description") or "").strip(),
        )
        for n, a in _names(data.get("entity_types"), "entity_types").items()
    }
    relations = {
        n: Relation(
            name=n,
            domain=frozenset(a.get("domain") or []),
            range=frozenset(a.get("range") or []),
            description=str(a.get("description") or "").strip(),
            module=mod,
        )
        for n, a in _names(data.get("relation_types"), "relation_types").items()
    }
    return Module(
        name=mod,
        version=str(data["version"]),
        requires=tuple(str(r) for r in (data.get("requires") or [])),
        types=types,
        relations=relations,
        type_aliases={
            str(k): str(v) for k, v in (data.get("type_aliases") or {}).items()
        },
        relation_aliases={
            str(k): str(v) for k, v in (data.get("relation_aliases") or {}).items()
        },
    )


def compose(modules: list[Module]) -> Ontology:
    """Merge modules into one ontology, checking names, parents, domains
    and requirements. A single unnamed module keeps its plain version."""
    by_name = {m.name: m for m in modules}
    types: dict[str, EntityType] = {}
    relations: dict[str, Relation] = {}
    type_aliases: dict[str, str] = {}
    relation_aliases: dict[str, str] = {}
    for m in modules:
        for req in m.requires:
            if req not in by_name:
                raise ValueError(
                    f"module {m.name!r} requires {req!r}, which is not loaded"
                )
        for n, t in m.types.items():
            if n in types:
                raise ValueError(
                    f"entity type {n!r} is declared by both {types[n].module!r}"
                    f" and {m.name!r}"
                )
            types[n] = t
        for n, r in m.relations.items():
            if n in relations:
                raise ValueError(
                    f"relation {n!r} is declared by both {relations[n].module!r}"
                    f" and {m.name!r}"
                )
            relations[n] = r
        type_aliases.update(m.type_aliases)
        relation_aliases.update(m.relation_aliases)
    for t in types.values():
        if t.parent and t.parent not in types:
            raise ValueError(f"type {t.name!r} has unknown parent {t.parent!r}")
    for r in relations.values():
        for side, names in (("domain", r.domain), ("range", r.range)):
            unknown = set(names) - set(types)
            if unknown:
                raise ValueError(
                    f"relation {r.name!r} {side} names unknown types {unknown}"
                )
    for alias, target in type_aliases.items():
        if target not in types or alias in types:
            raise ValueError(f"type alias {alias!r} -> {target!r} is not valid")
    for alias, target in relation_aliases.items():
        if target not in relations or alias in relations:
            raise ValueError(f"relation alias {alias!r} -> {target!r} is not valid")
    if len(modules) == 1 and modules[0].name == "main":
        version = modules[0].version
    else:
        ordered = sorted(modules, key=lambda m: (m.name != CORE, m.name))
        version = "+".join(f"{m.name}{m.version}" for m in ordered)
    return Ontology(
        version=version,
        modules={m.name: m for m in modules},
        types=types,
        relations=relations,
        type_aliases=type_aliases,
        relation_aliases=relation_aliases,
    )


def parse(text: str) -> Ontology:
    """A single file as a whole ontology (the legacy form)."""
    return compose([parse_module(text)])


def load_dir(directory: Path) -> Ontology:
    files = sorted(directory.glob("*.yaml"))
    if not files:
        raise ValueError(f"no ontology modules in {directory}")
    return compose(
        [parse_module(f.read_text(encoding="utf-8"), name=f.stem) for f in files]
    )


def path() -> Path:
    return Path(os.environ.get("PRAX_ONTOLOGY", config.ONTOLOGY_PATH))


def _stamp(p: Path) -> tuple[Any, ...]:
    if p.is_dir():
        return tuple((f.name, f.stat().st_mtime_ns) for f in sorted(p.glob("*.yaml")))
    return (p.name, p.stat().st_mtime_ns)


@functools.lru_cache(maxsize=4)
def _load(p: Path, stamp: tuple[Any, ...]) -> Ontology:
    return load_dir(p) if p.is_dir() else parse(p.read_text(encoding="utf-8"))


def current() -> Ontology:
    """The ontology on disk; re-parsed only when a file changes."""
    p = path()
    return _load(p, _stamp(p))
