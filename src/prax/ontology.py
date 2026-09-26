"""The versioned ontology: which entity and relation types the graph accepts.

``ontology/`` (repo root, or ``ontology.dir`` in prax.yaml) holds one YAML file per
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
    relation_aliases:
      supervised_by: advised_by
      mentioned_in: {to: mentions, reversed: true}   # said the other way round

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
    # how the type's names behave, "proper" or "common"; empty inherits
    # from the parent, and a root that says nothing is proper (see
    # ``Ontology.naming``)
    naming: str = ""


NAMINGS = ("proper", "common")


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
    # the aliases that name the relation the other way round: ``X
    # mentioned_in Y`` is ``Y mentions X``; the ends are swapped with it
    reversed_aliases: frozenset[str] = frozenset()
    # what the document being extracted may be in this module (a paper;
    # a manual, datasheet, schematic or article); empty: a plain document
    self_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ontology:
    version: str
    modules: dict[str, Module] = field(default_factory=dict)
    types: dict[str, EntityType] = field(default_factory=dict)
    relations: dict[str, Relation] = field(default_factory=dict)
    type_aliases: dict[str, str] = field(default_factory=dict)
    relation_aliases: dict[str, str] = field(default_factory=dict)
    reversed_aliases: frozenset[str] = frozenset()
    self_types: tuple[str, ...] = ()
    # composed subsets by domain set (``for_domains`` is asked per document)
    _subsets: dict[frozenset[str], Ontology] = field(
        default_factory=dict, repr=False, compare=False
    )

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

    def naming(self, name: str) -> str:
        """Whether the type's names are ``proper`` or ``common``.

        A proper name belongs to one particular thing — a person, a
        publisher, a product, a title — and is the same string in every
        language: *Niklas Klügel* is not translated, and neither is
        *Einführung in die Softwaretechnik*. A common name is the name of
        a kind of thing, which every language has its own word for:
        *Olivenöl* and *olive oil* are one ingredient, *Virtualisierung*
        and *virtualization* one concept.

        The nearest declaration wins, so a subtype may differ from its
        parent in either direction: a ``dish`` is a ``work`` whose name
        translates, a ``standard`` is a ``concept`` whose name does not.
        A type that says nothing and inherits nothing is proper, because
        translating a name that should not be translated is the worse
        mistake of the two.
        """
        for n in self.ancestors(name):
            declared = self.types[n].naming
            if declared:
                return declared
        return "proper"

    @property
    def common_types(self) -> frozenset[str]:
        """The types whose names are a kind of thing, not a particular
        one: what a vocabulary may be standardized across languages."""
        return frozenset(n for n in self.types if self.naming(n) == "common")

    def canonical_type(self, name: str) -> str:
        """A type a model named, as this ontology spells it (aliases)."""
        return self.type_aliases.get(name, name)

    def canonical_relation(self, name: str) -> str:
        return self.relation_aliases.get(name, name)

    def is_reversed(self, name: str) -> bool:
        """Whether a relation a model named runs the other way round from
        the canonical one (``mentioned_in`` for ``mentions``): the caller
        swaps the ends when it takes the canonical name."""
        return name in self.reversed_aliases

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

    def domains_of(self, type_name: str) -> frozenset[str] | None:
        """The domains a thing of this type can be found in: the module
        that declares the type and every module built on it (`material`
        is craft's, so craft, kitchen and workshop). None for a core type,
        or one no module declares, which is everywhere.

        What a search uses to keep a word the graph added to a query
        where its sense lives: `apple` reached through the ingredient is
        a word for recipes, not for the Logic manuals, which name the
        organization.
        """
        home = next(
            (m.name for m in self.modules.values() if type_name in m.types), None
        )
        if home is None or home == CORE:
            return None
        return frozenset(
            m.name for m in self.modules.values() if home in _closure(m, self.modules)
        )

    def for_domains(self, domains: list[str] | set[str] | None) -> Ontology:
        """The core plus the named modules (and what they require): the
        ontology a document of those domains is extracted against. None
        or an empty set is the whole ontology."""
        if not domains:
            return self
        key = frozenset(d for d in domains if d in self.modules)
        if key not in self._subsets:
            wanted: set[str] = {CORE}
            stack = list(key)
            while stack:
                m = stack.pop()
                if m in wanted:
                    continue
                wanted.add(m)
                stack.extend(self.modules[m].requires)
            self._subsets[key] = compose(
                [self.modules[m] for m in self.modules if m in wanted]
            )
        return self._subsets[key]


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


def _naming(type_name: str, value: Any) -> str:
    """A type's declared ``naming``, checked. Absent inherits."""
    if value is None:
        return ""
    got = str(value).strip().lower()
    if got not in NAMINGS:
        raise ValueError(
            f"entity type {type_name!r}: naming is {got!r}, one of {', '.join(NAMINGS)}"
        )
    return got


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
            naming=_naming(n, a.get("naming")),
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
    relation_aliases: dict[str, str] = {}
    reversed_aliases: set[str] = set()
    for k, v in (data.get("relation_aliases") or {}).items():
        # a plain name, or {to: name, reversed: true} for one said the
        # other way round (X mentioned_in Y is Y mentions X)
        if isinstance(v, dict):
            if "to" not in v or set(v) - {"to", "reversed"}:
                raise ValueError(
                    f"relation alias {k!r}: expected a name or {{to, reversed}}"
                )
            relation_aliases[str(k)] = str(v["to"])
            if v.get("reversed"):
                reversed_aliases.add(str(k))
        else:
            relation_aliases[str(k)] = str(v)
    return Module(
        name=mod,
        version=str(data["version"]),
        requires=tuple(str(r) for r in (data.get("requires") or [])),
        types=types,
        relations=relations,
        type_aliases={
            str(k): str(v) for k, v in (data.get("type_aliases") or {}).items()
        },
        relation_aliases=relation_aliases,
        reversed_aliases=frozenset(reversed_aliases),
        self_types=tuple(str(t) for t in (data.get("self_types") or [])),
    )


def compose(modules: list[Module]) -> Ontology:
    """Merge modules into one ontology, checking names, parents, domains
    and requirements. A single unnamed module keeps its plain version."""
    by_name = {m.name: m for m in modules}
    types: dict[str, EntityType] = {}
    relations: dict[str, Relation] = {}
    type_aliases: dict[str, str] = {}
    relation_aliases: dict[str, str] = {}
    reversed_aliases: set[str] = set()
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
    # An alias may not shadow a name its own module or a required module
    # declares. When two independent modules meet (research and studio
    # both loaded) and one's alias names the other's relation, the
    # declared name wins and the alias is left out.
    for m in modules:
        visible = _closure(m, by_name)
        for alias, target in m.type_aliases.items():
            if target not in types or (
                alias in types and types[alias].module in visible
            ):
                raise ValueError(f"type alias {alias!r} -> {target!r} is not valid")
            if alias not in types:
                type_aliases[alias] = target
        for alias, target in m.relation_aliases.items():
            if target not in relations or (
                alias in relations and relations[alias].module in visible
            ):
                raise ValueError(f"relation alias {alias!r} -> {target!r} is not valid")
            if alias not in relations:
                relation_aliases[alias] = target
                if alias in m.reversed_aliases:
                    reversed_aliases.add(alias)
    self_types: list[str] = []
    for m in modules:
        for t in m.self_types:
            if t not in types:
                raise ValueError(f"module {m.name!r}: self type {t!r} is unknown")
            if t not in self_types:
                self_types.append(t)
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
        reversed_aliases=frozenset(reversed_aliases),
        self_types=tuple(self_types),
    )


def _closure(m: Module, by_name: dict[str, Module]) -> set[str]:
    """The module and everything it requires, transitively."""
    seen: set[str] = set()
    stack = [m.name]
    while stack:
        n = stack.pop()
        if n in seen or n not in by_name:
            continue
        seen.add(n)
        stack.extend(by_name[n].requires)
    return seen


def parse(text: str) -> Ontology:
    """A single file as a whole ontology (the legacy form)."""
    return compose([parse_module(text)])


LEXICON = "lexicon"  # a file beside the modules that is not one of them


def load_dir(directory: Path) -> Ontology:
    # the lexicon is words *about* the types, not types: composed as a
    # module it would join the version string, and every document would
    # look unread against the new version
    files = sorted(f for f in directory.glob("*.yaml") if f.stem != LEXICON)
    if not files:
        raise ValueError(f"no ontology modules in {directory}")
    return compose(
        [parse_module(f.read_text(encoding="utf-8"), name=f.stem) for f in files]
    )


@dataclass(frozen=True)
class Lexicon:
    """The words that say what a name is, and that a name is not one.

    Data beside the types they are about (``ontology/lexicon.yaml``)
    rather than regular expressions in the module that reads them, so a
    cue can be added, versioned and measured like anything else the
    ontology holds. It changes what a typing rule guesses about a misfit,
    never what the ontology accepts, so it bumps no module version.
    """

    version: str = "0"
    # (stems, whole words): a stem matches from the start of a word on, a
    # word must be the whole one — "mit" is MIT and not Smith
    organization: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
    top_organization: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
    by_type: tuple[tuple[str, tuple[str, ...]], ...] = ()  # ordered: first wins
    exact: frozenset[str] = frozenset()
    vague_start: tuple[str, ...] = ()
    never_start: tuple[str, ...] = ()

    def type_of(self, name: str) -> str | None:
        """The type a name's own words say it is, or None."""
        low = name.lower()
        for etype, cues in self.by_type:
            if any(cue in low for cue in cues):
                return etype
        return None


def parse_lexicon(text: str) -> Lexicon:
    data = yaml.safe_load(text) or {}
    words = data.get("not_a_name") or {}

    def seq(section: Any) -> tuple[str, ...]:
        return tuple(str(x) for x in (section or []))

    def cues(section: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
        section = section or {}
        return seq(section.get("stems")), seq(section.get("words"))

    return Lexicon(
        version=str(data.get("version") or "0"),
        organization=cues(data.get("organization")),
        top_organization=cues(data.get("top_organization")),
        by_type=tuple((str(k), seq(v)) for k, v in (data.get("by_type") or {}).items()),
        exact=frozenset(str(x).lower() for x in (words.get("exact") or [])),
        vague_start=seq(words.get("vague_start")),
        never_start=seq(words.get("never_start")),
    )


def path() -> Path:
    return Path(config.setting("ontology.dir", "PRAX_ONTOLOGY", config.ONTOLOGY_PATH))


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


@functools.lru_cache(maxsize=4)
def _load_lexicon(p: Path, stamp: tuple[Any, ...]) -> Lexicon:
    f = (p / f"{LEXICON}.yaml") if p.is_dir() else p.with_name(f"{LEXICON}.yaml")
    if not f.exists():
        return Lexicon()  # a host without one: the callers fall back to nothing
    return parse_lexicon(f.read_text(encoding="utf-8"))


def lexicon() -> Lexicon:
    """The words beside the types; re-parsed only when the file changes."""
    p = path()
    return _load_lexicon(p, _stamp(p))
