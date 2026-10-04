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
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from prax import config

CORE = "core"
# The store's own kinds of document. A page is written here and a project
# is declared here; neither is ever the extractor reaching for a general
# type, so neither is proposed as one nor folded into its parent.
SELF_KINDS = frozenset({"page", "project"})


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
    # the same type in a standard vocabulary ("schema:Person",
    # "skos:Concept"): what the type is called where other systems and
    # models know it; it changes what validates in no way (stage AN)
    same_as: tuple[str, ...] = ()


NAMINGS = ("proper", "common")


@dataclass(frozen=True)
class Relation:
    name: str
    domain: frozenset[str] = frozenset()  # empty = unconstrained
    range: frozenset[str] = frozenset()
    description: str = ""
    module: str = CORE
    # what follows from the relation (stage AN): the rule pass derives the
    # closure of a transitive one and the converse of a symmetric one;
    # a functional one (one value a subject) is a constraint whose breach
    # is a finding, never an edge; ``inverse_of`` names the relation that
    # says the same the other way round. None of it changes what
    # validates, so none of it bumps a version
    transitive: bool = False
    symmetric: bool = False
    functional: bool = False
    inverse_of: str = ""
    # how a fact of it lasts: "state" (holds for a while: an affiliation),
    # "event" (happens once: a publication), "eternal" (holds as long as
    # the things do: authorship); empty says nothing
    kind: str = ""
    same_as: tuple[str, ...] = ()  # in schema.org, SKOS, PROV-O, Dublin Core


RELATION_KINDS = ("state", "event", "eternal")


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
        language: *Clara Schumann* is not translated, and neither is
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

    def check_names(
        self, src: str, src_type: str, rel: str, dst: str, dst_type: str
    ) -> None:
        """Raise ``ValueError`` when the names say the edge is wrong though
        its types fit: a ``part_of`` the wrong way round, or one that holds
        neither way (``part_of_suspect``). The writers call it beside
        ``check_edge``, so a suspect takes the misfit's way to a person."""
        if rel != "part_of":
            return
        said = part_of_suspect(self, src, src_type, dst, dst_type)
        if said:
            raise ValueError(f"part_of {said[0]}: {said[1]}")

    def within(self, domain: str) -> frozenset[str]:
        """The modules whose documents are also documents of ``domain``:
        the module and every module built on it. A kitchen document is
        read against craft as well, so it is a craft document too; a
        filter by craft that took only craft's own listed 27 of the 130."""
        if domain not in self.modules:
            return frozenset({domain})
        return frozenset(
            m.name for m in self.modules.values() if domain in _closure(m, self.modules)
        )

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


def _same_as(name: str, value: Any) -> tuple[str, ...]:
    """A ``same_as`` entry: one ``prefix:Term`` or a list of them."""
    if value is None:
        return ()
    items = [value] if isinstance(value, str) else list(value)
    for item in items:
        if not isinstance(item, str) or ":" not in item:
            raise ValueError(f"{name!r}: same_as is prefix:Term, not {item!r}")
    return tuple(items)


def lint(types: dict[str, EntityType], relations: dict[str, Relation]) -> list[str]:
    """What contradicts itself in the composed ontology (stage AN): a
    relation transitive and functional at once (its closure would give a
    subject several values), symmetric between types it cannot hold the
    other way round, an ``inverse_of`` that names nothing or is not
    mutual, an unknown ``kind``, a subtype cycle."""
    out: list[str] = []
    for r in relations.values():
        if r.transitive and r.functional:
            out.append(f"relation {r.name!r} is transitive and functional")
        if r.symmetric and r.domain and r.range and r.domain != r.range:
            out.append(
                f"relation {r.name!r} is symmetric but its domain is not its range"
            )
        if r.inverse_of:
            other = relations.get(r.inverse_of)
            if other is None:
                out.append(
                    f"relation {r.name!r}: inverse_of {r.inverse_of!r} is unknown"
                )
            elif other.inverse_of and other.inverse_of != r.name:
                out.append(
                    f"relation {r.name!r}: inverse_of {r.inverse_of!r}, which says"
                    f" {other.inverse_of!r}"
                )
            elif r.symmetric:
                out.append(f"relation {r.name!r} is symmetric and has an inverse")
        if r.kind and r.kind not in RELATION_KINDS:
            out.append(f"relation {r.name!r}: kind {r.kind!r}, one of {RELATION_KINDS}")
    for t in types.values():
        seen = {t.name}
        parent = t.parent
        while parent:
            if parent in seen:
                out.append(f"entity type {t.name!r} is in a subtype cycle")
                break
            seen.add(parent)
            parent = types[parent].parent if parent in types else None
    return out


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
            same_as=_same_as(n, a.get("same_as")),
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
            transitive=bool(a.get("transitive", False)),
            symmetric=bool(a.get("symmetric", False)),
            functional=bool(a.get("functional", False)),
            inverse_of=str(a.get("inverse_of") or ""),
            kind=str(a.get("kind") or ""),
            same_as=_same_as(n, a.get("same_as")),
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


def _declare(
    into: dict[str, Any], what: str, module: str, found: dict[str, Any]
) -> None:
    """One module's types or relations into the composed set; a name two
    modules declare is refused."""
    for n, x in found.items():
        if n in into:
            raise ValueError(
                f"{what} {n!r} is declared by both {into[n].module!r} and {module!r}"
            )
        into[n] = x


def _check_references(
    types: dict[str, EntityType], relations: dict[str, Relation]
) -> None:
    """Every parent and every relation's domain and range name a type."""
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


def _aliases(
    what: str,
    aliases: dict[str, str],
    declared: dict[str, Any],
    visible: set[str],
    into: dict[str, str],
) -> list[str]:
    """One module's aliases of types or relations into the composed set.

    An alias may not shadow a name its own module or a required module
    declares. When two independent modules meet (research and studio both
    loaded) and one's alias names the other's declared name, the declared
    name wins and the alias is left out. Returns the aliases taken."""
    taken = []
    for alias, target in aliases.items():
        if target not in declared or (
            alias in declared and declared[alias].module in visible
        ):
            raise ValueError(f"{what} alias {alias!r} -> {target!r} is not valid")
        if alias not in declared:
            into[alias] = target
            taken.append(alias)
    return taken


def _version(modules: list[Module]) -> str:
    """The modules' versions joined, core first (``core4+research9``); a
    single unnamed module keeps its plain version."""
    if len(modules) == 1 and modules[0].name == "main":
        return modules[0].version
    ordered = sorted(modules, key=lambda m: (m.name != CORE, m.name))
    return "+".join(f"{m.name}{m.version}" for m in ordered)


def compose(modules: list[Module]) -> Ontology:
    """Merge modules into one ontology, checking names, parents, domains
    and requirements."""
    by_name = {m.name: m for m in modules}
    types: dict[str, EntityType] = {}
    relations: dict[str, Relation] = {}
    for m in modules:
        for req in m.requires:
            if req not in by_name:
                raise ValueError(
                    f"module {m.name!r} requires {req!r}, which is not loaded"
                )
        _declare(types, "entity type", m.name, m.types)
        _declare(relations, "relation", m.name, m.relations)
    _check_references(types, relations)
    problems = lint(types, relations)
    if problems:
        raise ValueError("the ontology contradicts itself: " + "; ".join(problems))
    type_aliases: dict[str, str] = {}
    relation_aliases: dict[str, str] = {}
    reversed_aliases: set[str] = set()
    for m in modules:
        visible = _closure(m, by_name)
        _aliases("type", m.type_aliases, types, visible, type_aliases)
        taken = _aliases(
            "relation", m.relation_aliases, relations, visible, relation_aliases
        )
        reversed_aliases |= {a for a in taken if a in m.reversed_aliases}
    self_types: list[str] = []
    for m in modules:
        for own in m.self_types:
            if own not in types:
                raise ValueError(f"module {m.name!r}: self type {own!r} is unknown")
            if own not in self_types:
                self_types.append(own)
    return Ontology(
        version=_version(modules),
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
SAMENESS = "sameness"  # nor is this one: what "the same thing" means
GENRES = "genres"  # nor this: what a document is (stage Z)
SUBJECTS = "subjects"  # nor this: what a document is about, in a person's words
BESIDE = (LEXICON, SAMENESS, GENRES, SUBJECTS)


def load_dir(directory: Path, extra: Iterable[Path] = ()) -> Ontology:
    """The modules of ``directory``, and the module files ``extra`` names
    (the packs', docs/packs.md), composed in the order of their names as
    one directory's were."""
    # the lexicon is words *about* the types, not types: composed as a
    # module it would join the version string, and every document would
    # look unread against the new version
    files = sorted(
        [f for f in directory.glob("*.yaml") if f.stem not in BESIDE] + list(extra),
        key=lambda f: f.name,
    )
    texts = [(f.stem, f.read_text(encoding="utf-8")) for f in files]
    # and a file that does not say it is one is not a module either. A
    # door started before a file beside the modules was written knew no
    # name for it, read it as a module, and stamped 883 edges with
    # "+genres1" or "+subjects1" (2026-09-29); a module names itself
    texts = [(stem, text) for stem, text in texts if _is_module(text)]
    if not texts:
        raise ValueError(f"no ontology modules in {directory}")
    return compose([parse_module(text, name=stem) for stem, text in texts])


def _is_module(text: str) -> bool:
    """Whether a YAML file in the ontology directory is a module: it
    names itself (``module:``) or declares types or relations."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return True  # a broken module is an error to see, not a file to skip
    return isinstance(data, dict) and any(
        k in data for k in ("module", "entity_types", "relation_types")
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
    # which end of a part_of is the part (``part_of_suspect``)
    part: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
    numbered: tuple[str, ...] = ()
    whole: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
    generic: frozenset[str] = frozenset()
    ranks: tuple[tuple[int, tuple[tuple[str, ...], tuple[str, ...]]], ...] = ()

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
    parts = data.get("part_of") or {}

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
        part=cues(parts.get("part")),
        numbered=seq(parts.get("numbered")),
        whole=cues(parts.get("whole")),
        generic=frozenset(str(x).lower() for x in (parts.get("generic") or [])),
        ranks=tuple(
            sorted((int(k), cues(v)) for k, v in (parts.get("ranks") or {}).items())
        ),
    )


def cue_pattern(cues: tuple[tuple[str, ...], tuple[str, ...]]) -> re.Pattern[str]:
    """A lexicon section as one pattern: a stem matches from the start of
    a word on, a whole word must be the whole word. Longest first, so
    "inc." wins over "inc"."""
    stems, words = cues
    parts = [rf"\b{re.escape(w)}" for w in sorted(stems, key=len, reverse=True)]
    parts += [rf"\b{re.escape(w)}\b" for w in sorted(words, key=len, reverse=True)]
    return re.compile("|".join(parts) or r"(?!x)x", re.IGNORECASE)


@functools.lru_cache(maxsize=4)
def _direction_cues(
    lex: Lexicon,
) -> tuple[re.Pattern[str], re.Pattern[str], list[tuple[int, re.Pattern[str]]]]:
    part = cue_pattern(lex.part)
    if lex.numbered:
        words = "|".join(
            re.escape(w) for w in sorted(lex.numbered, key=len, reverse=True)
        )
        # a number or a roman numeral after the word: "Teil 1", "Vol. IV"
        numbered = re.compile(
            rf"\b(?:{words})(?:\s|\b)\s*(?:\d|[ivxlc]+\b)", re.IGNORECASE
        )
        part = re.compile(f"{part.pattern}|{numbered.pattern}", re.IGNORECASE)
    return part, cue_pattern(lex.whole), [(r, cue_pattern(c)) for r, c in lex.ranks]


_DIGIT = re.compile(r"\d")
HEAD_CHARS = 30  # where a name says what it is
_FOLD = re.compile(r"[\W_]+")


def _words(name: str) -> list[str]:
    """The content words of a name: four letters or more."""
    return [w for w in _FOLD.sub(" ", name.lower()).split() if len(w) >= 4]


def _folded(name: str) -> str:
    return " " + " ".join(_FOLD.sub(" ", name.lower()).split()) + " "


def part_of_suspect(
    onto: Ontology, src: str, src_type: str, dst: str, dst_type: str
) -> tuple[str, str] | None:
    """Whether ``src part_of dst`` looks wrong from its names: ``(verdict,
    reason)`` or None. ``reversed``: the destination is the part, by
    structure (the source is the higher organization, or the destination
    is the source's name and a number more or its exam). ``misfit``: what
    holds neither way round (a university part of a project, a paper part
    of a publisher, anything part of "course"). ``doubtful``: a cue says so
    (the destination names a part and the source does not, or the source
    names a whole), which a topic in an exercise sheet also does. The cues
    are the lexicon's ``part_of`` section. Every verdict keeps the triple
    out of the graph for a person and out of the rule pass's premises; only
    ``reversed`` and ``misfit`` are ever mended without one looking, and
    only by ``prax heal --check backwards-part-of --apply``."""
    lex = lexicon()
    part, whole, ranks = _direction_cues(lex)
    sheet = cue_pattern(lex.part)  # the part words without the numbered ones
    plain = " ".join(dst.lower().split())
    for article in ("the ", "a ", "an "):
        plain = plain.removeprefix(article)
    if plain in lex.generic:
        return "misfit", f"{dst!r} is a kind of thing, not one"

    def rank(name: str) -> int:
        return max((r for r, pat in ranks if pat.search(name)), default=0)

    org = "organization"
    src_org = src_type in onto.types and onto.is_a(src_type, org)
    dst_org = dst_type in onto.types and onto.is_a(dst_type, org)
    rs, rd = rank(src), rank(dst)
    ps, pd = bool(part.search(src)), bool(part.search(dst))
    ws, wd = bool(whole.search(src)), bool(whole.search(dst))
    fs, fd = _folded(src), _folded(dst)
    if len(fs) > 3 and fs in fd:
        extra = fd.replace(fs, " ")
        # "Klausur zur Vorlesung X" is the exam of the course X
        if sheet.search(extra):
            return "reversed", f"{dst!r} is a part of {src!r} by its name"
        # "Chapter 32- The Laplace Transform" is the chapter itself
        if _DIGIT.search(extra) and not part.search(extra):
            return "reversed", f"{dst!r} is {src!r} and a number more"
        return None
    if rs and rd:
        if rs > rd:
            return "reversed", f"{src!r} ranks above {dst!r} as an organization"
        return None
    # the institution leads the name: a document's title that only
    # mentions one near its end ("… Referat, TU München") says nothing
    if rank(src[:HEAD_CHARS]) >= 3 and not rd and not dst_org:
        return "misfit", f"{src!r} is an institution, part of no {dst_type}"
    if src_org and not dst_org:
        return "misfit", f"an organization is part of no {dst_type}"
    if dst_org and not src_org and onto.is_a(src_type, "document"):
        return "misfit", f"a {src_type} is part of no organization"
    # what the cues say is a guess, for a person: a topic is in an exercise
    # sheet as often as a course is wrongly said to be. "Journal X, Vol. 4"
    # names a part and a whole, and an article is in it; a section shares
    # words with the chapter it is in
    shared = set(_words(src)) & set(_words(dst))
    if pd and not ps and not wd and not shared:
        return "doubtful", f"{dst!r} names a part and {src!r} does not"
    # a project may hold proceedings or a series
    if ws and not wd and not ps and dst_type != "project":
        return "doubtful", f"{src!r} names a whole and {dst!r} does not"
    return None


@dataclass(frozen=True)
class Case:
    """One kind of pair, as the rule states it, with its examples."""

    text: str
    examples: tuple[tuple[str, str], ...] = ()

    def said(self) -> str:
        if not self.examples:
            return self.text
        pairs = ", ".join(f'"{a}" and "{b}"' for a, b in self.examples)
        return f"{self.text} ({pairs})"


@dataclass(frozen=True)
class Sameness:
    """What "the same thing" means for a likely pair: the cases that are
    one thing and those that are two, for every pair, and a module's own
    cases for the pairs of its types (``ontology/sameness.yaml``). Every
    judge is given the same text: the local model, the paid one, and the
    person on the Review page."""

    version: str = "0"
    same: tuple[Case, ...] = ()
    different: tuple[Case, ...] = ()
    # module -> (its same cases, its different cases)
    modules: tuple[tuple[str, tuple[Case, ...], tuple[Case, ...]], ...] = ()

    def cases(self, modules: Iterable[str] = ()) -> tuple[list[Case], list[Case]]:
        """The same and the different cases for pairs of these modules."""
        wanted = set(modules)
        same, different = list(self.same), list(self.different)
        for name, s, d in self.modules:
            if name in wanted:
                same += s
                different += d
        return same, different

    def rule(self, modules: Iterable[str] = ()) -> str:
        """The rule as two lines of a question to a model."""
        same, different = self.cases(modules)
        return (
            "The same thing: " + "; ".join(c.said() for c in same) + ".\n"
            "Different things: " + "; ".join(c.said() for c in different) + ".\n"
        )

    def as_dict(self) -> dict[str, Any]:
        """The whole rule for a reader: the Review page shows it."""

        def cases(cs: tuple[Case, ...]) -> list[dict[str, Any]]:
            return [
                {"case": c.text, "examples": [list(e) for e in c.examples]} for c in cs
            ]

        return {
            "version": self.version,
            "same": cases(self.same),
            "different": cases(self.different),
            "modules": {
                name: {"same": cases(s), "different": cases(d)}
                for name, s, d in self.modules
            },
        }


def parse_sameness(text: str) -> Sameness:
    data = yaml.safe_load(text) or {}

    def cases(section: Any) -> tuple[Case, ...]:
        return tuple(
            Case(
                str(c["case"]),
                tuple((str(e[0]), str(e[1])) for e in c.get("examples") or []),
            )
            for c in section or []
        )

    return Sameness(
        version=str(data.get("version") or "0"),
        same=cases(data.get("same")),
        different=cases(data.get("different")),
        modules=tuple(
            (str(name), cases((m or {}).get("same")), cases((m or {}).get("different")))
            for name, m in (data.get("modules") or {}).items()
        ),
    )


@dataclass(frozen=True)
class Level:
    """A level of a facet and the labels under it, each with its line of
    description: for the genres, what a text does and the forms it takes;
    for the subjects, a field and the subjects in it."""

    name: str
    description: str
    genres: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Facet:
    """A vocabulary a document is labelled with, in two levels: what it is
    (``ontology/genres.yaml``) or what it is about
    (``ontology/subjects.yaml``). The labels a person gives on the Review
    page's "genre" tab and a model gives later. A label is one under a
    level, or a level alone, for a document that fits none under it. The
    YAML names the labels under a level ``genres`` in both files."""

    version: str = "0"
    levels: tuple[Level, ...] = ()

    def labels(self) -> list[str]:
        """Every label, level by level, each level before its genres."""
        out: list[str] = []
        for lv in self.levels:
            out.append(lv.name)
            out += [g for g, _ in lv.genres]
        return out

    def level_of(self, label: str) -> str | None:
        """The level a label belongs to (a level is its own), or None."""
        for lv in self.levels:
            if label == lv.name or any(label == g for g, _ in lv.genres):
                return lv.name
        return None

    def check(self, labels: Iterable[str]) -> list[str]:
        """The labels without repeats, in the vocabulary's order; a
        ValueError names the first one that is not in it."""
        known = self.labels()
        given = set()
        for label in labels:
            if label not in known:
                raise ValueError(f"unknown label {label!r}; the labels are {known}")
            given.add(label)
        return [x for x in known if x in given]

    def implied(self, labels: Iterable[str]) -> list[str]:
        """The labels with the level of each one added: a label under a
        level is that level too, as a person ticks it (2026-09-29: the
        user ticked "informational" beside "paper" throughout). In the
        vocabulary's order; a ValueError names an unknown label."""
        given = self.check(labels)
        levels = {lv for x in given if (lv := self.level_of(x))}
        return self.check([*given, *levels])

    def as_dict(self) -> dict[str, Any]:
        """The vocabulary for a reader: the Review page shows it."""
        return {
            "version": self.version,
            "levels": [
                {
                    "name": lv.name,
                    "description": lv.description,
                    "genres": [{"name": g, "description": d} for g, d in lv.genres],
                }
                for lv in self.levels
            ],
        }


def parse_facet(text: str, *, name: str = GENRES) -> Facet:
    data = yaml.safe_load(text) or {}
    levels: list[Level] = []
    seen: set[str] = set()
    for level, body in (data.get("levels") or {}).items():
        body = body or {}
        under = tuple(
            (str(g), str(d or "")) for g, d in (body.get("genres") or {}).items()
        )
        for label in (str(level), *(g for g, _ in under)):
            if label in seen:
                raise ValueError(f"{name}.yaml names {label!r} twice")
            seen.add(label)
        levels.append(Level(str(level), str(body.get("description") or ""), under))
    return Facet(version=str(data.get("version") or "0"), levels=tuple(levels))


def path() -> Path:
    return Path(config.setting("ontology.dir", "PRAX_ONTOLOGY", config.ONTOLOGY_PATH))


def _packed(p: Path) -> bool:
    """Whether the packs' knowledge joins ``p``: for the core directory of
    the repository. A directory a host or a test names is the whole
    ontology, as it was before packs (docs/packs.md)."""
    return p.is_dir() and p.resolve() == config.ONTOLOGY_PATH.resolve()


def _pack_modules(p: Path) -> list[Path]:
    if not _packed(p):
        return []
    from prax import packs

    return packs.ontology_files()


def _pack_sameness(p: Path) -> list[Path]:
    if not _packed(p):
        return []
    from prax import packs

    return packs.sameness_files()


def module_files() -> dict[str, Path]:
    """Each composed module's own file, by module name: the core
    directory's and the packs'."""
    p = path()
    if not p.is_dir():
        return {}
    files = [f for f in p.glob("*.yaml") if f.stem not in BESIDE]
    return {f.stem: f for f in [*files, *_pack_modules(p)]}


def _stamp(p: Path) -> tuple[Any, ...]:
    if p.is_dir():
        files = [*sorted(p.glob("*.yaml")), *_pack_modules(p), *_pack_sameness(p)]
        return tuple((str(f), f.stat().st_mtime_ns) for f in files)
    return (p.name, p.stat().st_mtime_ns)


@functools.lru_cache(maxsize=4)
def _load(p: Path, stamp: tuple[Any, ...]) -> Ontology:
    if p.is_dir():
        return load_dir(p, _pack_modules(p))
    return parse(p.read_text(encoding="utf-8"))


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


@functools.lru_cache(maxsize=4)
def _load_sameness(p: Path, stamp: tuple[Any, ...]) -> Sameness:
    f = (p / f"{SAMENESS}.yaml") if p.is_dir() else p.with_name(f"{SAMENESS}.yaml")
    rule = parse_sameness(f.read_text(encoding="utf-8")) if f.exists() else Sameness()
    # a pack's module cases follow the general ones, pack by pack
    for extra in _pack_sameness(p):
        more = parse_sameness(extra.read_text(encoding="utf-8"))
        rule = replace(rule, modules=rule.modules + more.modules)
    return rule


def sameness() -> Sameness:
    """What "the same thing" means; re-parsed only when the file changes."""
    p = path()
    return _load_sameness(p, _stamp(p))


@functools.lru_cache(maxsize=8)
def _load_facet(p: Path, stamp: tuple[Any, ...], name: str) -> Facet:
    f = (p / f"{name}.yaml") if p.is_dir() else p.with_name(f"{name}.yaml")
    if not f.exists():
        return Facet()
    return parse_facet(f.read_text(encoding="utf-8"), name=name)


def genres() -> Facet:
    """What a document may be; re-parsed only when the file changes."""
    p = path()
    return _load_facet(p, _stamp(p), GENRES)


def subjects() -> Facet:
    """What a document may be about; re-parsed only when the file changes."""
    p = path()
    return _load_facet(p, _stamp(p), SUBJECTS)


def modules_of(etype: str) -> set[str]:
    """The modules a type belongs to: its own and its ancestors'."""
    onto = current()
    names = [etype, *onto.ancestors(etype)] if etype in onto.types else []
    return {onto.types[n].module for n in names if n in onto.types}
