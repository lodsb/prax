"""The modular ontology: composition, subtypes, aliases, domains, the repo's
own modules, and the legacy single file."""

from __future__ import annotations

from pathlib import Path

import pytest

from prax import config
from prax.graph import ontology

EVERY_MODULE = "core3+craft1+electronics1+kitchen2+research9+studio5+workshop2"

CORE = """
module: core
version: 2
entity_types:
  person: {description: a human}
  organization:
  concept:
relation_types:
  affiliated_with: {domain: [person], range: [organization]}
type_aliases: {institution: organization}
relation_aliases: {affiliation: affiliated_with}
"""

RESEARCH = """
module: research
version: 7
requires: [core]
entity_types:
  author: {parent: person, description: wrote something}
  paper:
relation_types:
  authored_by: {domain: [paper], range: [author]}
  about: {domain: [paper], range: [concept]}
type_aliases: {technique: concept}
"""

FAMILY = """
module: family
version: 1
requires: [core]
entity_types:
  relative: {parent: person}
relation_types:
  parent_of: {domain: [person], range: [person]}
"""


def _compose(*texts: str) -> ontology.Ontology:
    return ontology.compose([ontology.parse_module(t) for t in texts])


def test_compose_versions_types_and_subtypes() -> None:
    o = _compose(CORE, RESEARCH, FAMILY)
    assert o.version == "core2+family1+research7"
    assert o.entity_types == {
        "person",
        "organization",
        "concept",
        "author",
        "paper",
        "relative",
    }
    assert o.parent("author") == "person" and o.parent("person") is None
    assert o.ancestors("author") == ["author", "person"]
    assert o.is_a("relative", "person") and not o.is_a("paper", "person")
    assert o.types["author"].module == "research"
    assert o.relations["parent_of"].module == "family"
    assert o.describe("author") == "wrote something" and o.describe("x") == ""


def test_subtypes_pass_where_the_parent_is_allowed() -> None:
    o = _compose(CORE, RESEARCH, FAMILY)
    o.check_edge("author", "affiliated_with", "organization")  # author is a person
    o.check_edge("relative", "parent_of", "author")  # cross-module through person
    with pytest.raises(ValueError, match="does not accept src"):
        o.check_edge("paper", "affiliated_with", "organization")
    with pytest.raises(ValueError, match="unknown entity type"):
        o.check_edge("planet", "authored_by", "author")


def test_aliases() -> None:
    o = _compose(CORE, RESEARCH)
    assert o.canonical_type("institution") == "organization"
    assert o.canonical_type("technique") == "concept"
    assert o.canonical_type("paper") == "paper"
    assert o.canonical_relation("affiliation") == "affiliated_with"
    assert o.canonical_relation("about") == "about"


def test_for_domains_keeps_core_and_requirements() -> None:
    o = _compose(CORE, RESEARCH, FAMILY)
    fam = o.for_domains(["family"])
    assert set(fam.modules) == {"core", "family"}
    assert "paper" not in fam.entity_types and "relative" in fam.entity_types
    assert fam.version == "core2+family1"
    assert o.for_domains(None) is o and o.for_domains(["nonesuch"]).version == "core2"


def test_composition_errors() -> None:
    with pytest.raises(ValueError, match="requires 'core'"):
        _compose(RESEARCH)
    dup = "module: other\nversion: 1\nentity_types: [person]\n"
    with pytest.raises(ValueError, match="declared by both"):
        _compose(CORE, dup)
    orphan = (
        "module: o\nversion: 1\nrequires: [core]\n"
        "entity_types:\n  x: {parent: nothing}\n"
    )
    with pytest.raises(ValueError, match="unknown parent"):
        _compose(CORE, orphan)
    bad_alias = (
        "module: o\nversion: 1\nrequires: [core]\ntype_aliases: {person: concept}\n"
    )
    with pytest.raises(ValueError, match="type alias"):
        _compose(CORE, bad_alias)


def test_legacy_single_file_keeps_a_plain_version() -> None:
    o = ontology.parse("version: '9'\nentity_types: [a, b]\nrelation_types: [r]\n")
    assert o.version == "9" and list(o.modules) == ["main"]
    o.check_edge("a", "r", "b")


def test_repo_modules_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    o = ontology.current()
    assert set(o.modules) == {
        "core",
        "craft",
        "electronics",
        "kitchen",
        "research",
        "studio",
        "workshop",
    }
    assert o.version == EVERY_MODULE
    assert set(o.self_types) == {
        "paper",
        "manual",
        "datasheet",
        "schematic",
        "article",
        "recipe",
        "build",
    }
    assert o.is_a("author", "person") and o.is_a("paper", "document")
    assert o.is_a("venue", "organization")
    o.check_edge("author", "affiliated_with", "organization")
    o.check_edge("tool", "developed_by", "author")  # author is a person
    assert o.canonical_relation("supervised_by") == "advised_by"
    # a directory elsewhere works the same way, and a change is picked up
    d = tmp_path / "onto"
    d.mkdir()
    for f in Path(config.ONTOLOGY_PATH).glob("*.yaml"):
        (d / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("PRAX_ONTOLOGY", str(d))
    assert ontology.current().version == EVERY_MODULE
    text = (d / "research.yaml").read_text(encoding="utf-8")
    (d / "research.yaml").write_text(
        text.replace("version: 9", "version: 99", 1), encoding="utf-8"
    )
    assert (
        ontology.current().version
        == "core3+craft1+electronics1+kitchen2+research99+studio5+workshop2"
    )


def test_craft_kitchen_and_workshop_modules() -> None:
    o = ontology.current()
    kitchen = o.for_domains(["kitchen"])
    assert set(kitchen.modules) == {"core", "craft", "kitchen"}
    assert kitchen.version == "core3+craft1+kitchen2"
    assert kitchen.self_types == ("recipe",)
    assert "paper" not in kitchen.types and "device" not in kitchen.types
    kitchen.check_edge("recipe", "makes", "dish")
    kitchen.check_edge("recipe", "calls_for", "ingredient")
    kitchen.check_edge("recipe", "needs", "tool")  # craft's, on any document
    kitchen.check_edge("recipe", "applies", "technique")
    kitchen.check_edge("dish", "belongs_to", "cuisine")
    kitchen.check_edge("dish", "variant_of", "recipe")
    assert kitchen.canonical_type("spice") == "ingredient"
    assert kitchen.canonical_relation("adapted_from") == "variant_of"
    with pytest.raises(ValueError):  # an ingredient is not a dish
        kitchen.check_edge("recipe", "makes", "ingredient")

    workshop = o.for_domains(["workshop"])
    assert set(workshop.modules) == {"core", "craft", "studio", "workshop"}
    assert workshop.version == "core3+craft1+studio5+workshop2"
    workshop.check_edge("build", "made_with", "component")  # studio's component
    workshop.check_edge("build", "made_with", "material")  # craft's material
    workshop.check_edge("build", "follows", "design")
    workshop.check_edge("build", "follows", "technique")
    workshop.check_edge("build", "derived_from", "build")
    workshop.check_edge("build", "needs", "tool")
    assert workshop.canonical_type("instructable") == "build"
    assert workshop.canonical_relation("based_on") == "derived_from"
    with pytest.raises(ValueError):
        workshop.check_edge("build", "made_with", "cuisine")

    # a technique is craft's when craft is loaded; research alone still
    # reads the word as its own method
    assert o.canonical_type("technique") == "technique"
    assert o.for_domains(["research"]).canonical_type("technique") == "method"
    assert o.for_domains(["research"]).version == "core3+research9"


def test_studio_module() -> None:
    o = ontology.current()
    s = o.for_domains(["studio"])
    assert set(s.modules) == {"core", "studio"} and s.version == "core3+studio5"
    assert s.self_types == ("manual", "datasheet", "schematic", "article")
    assert "paper" not in s.types and "cites" not in s.relations
    assert s.is_a("device", "tool") and s.is_a("manufacturer", "organization")
    s.check_edge("manual", "describes", "device")
    s.check_edge("device", "developed_by", "manufacturer")  # core, via subtypes
    s.check_edge("device", "conforms_to", "standard")
    s.check_edge("article", "appeared_in", "publication")
    assert s.canonical_type("synthesizer") == "device"
    assert s.canonical_relation("mentions") == "names"
    # v2: any document may describe a device — a build log as much as a
    # manual — so what is refused is the range, not the kind of document
    s.check_edge("manual", "describes", "device")
    with pytest.raises(ValueError):
        s.check_edge("manual", "describes", "concept")
    both = o.for_domains(["research", "studio"])
    assert both.self_types[0] == "paper" and "manual" in both.self_types
    assert (
        both.canonical_relation("mentions") == "mentions"
    )  # research wins nothing: names


def test_self_types_must_exist() -> None:
    with pytest.raises(ValueError, match="self type"):
        ontology.parse("version: '1'\nentity_types: [a]\nself_types: [b]\n")


def test_a_reversed_alias_names_the_relation_the_other_way_round() -> None:
    """``mentioned_in: {to: mentions, reversed: true}`` maps to the
    relation and says the ends swap; a plain alias does not; a wrong
    shape is refused."""
    text = """
module: main
version: 1
entity_types:
  paper:
  tool:
relation_types:
  mentions: {domain: [paper], range: [tool]}
relation_aliases:
  discusses: mentions
  mentioned_in: {to: mentions, reversed: true}
"""
    o = ontology.compose([ontology.parse_module(text)])
    assert o.canonical_relation("mentioned_in") == "mentions"
    assert o.canonical_relation("discusses") == "mentions"
    assert o.is_reversed("mentioned_in") and not o.is_reversed("discusses")
    assert not o.is_reversed("mentions")
    with pytest.raises(ValueError, match="expected a name or"):
        ontology.parse_module(
            text.replace("{to: mentions, reversed: true}", "{rev: 1}")
        )


def test_v8_admits_affiliation_beyond_persons() -> None:
    o = ontology.current()
    assert o.version.startswith("core3+")
    o.check_edge("organization", "affiliated_with", "organization")
    o.check_edge("person", "affiliated_with", "concept")
    o.check_edge("person", "located_in", "place")
    with pytest.raises(ValueError):
        o.check_edge("paper", "affiliated_with", "organization")  # written_at
    r = o.for_domains(["research"])
    assert r.canonical_relation("is_about") == "about"
    assert r.canonical_relation("used_in") == "uses" and r.is_reversed("used_in")
    assert o.canonical_relation("publisher_of") == "published_by"
    assert o.is_reversed("publisher_of") and o.is_reversed("contains")


def test_v9_a_document_where_a_paper_was_named() -> None:
    """A relation a document takes accepts every kind of document
    (docs/ontology-v9.md): the review queue held an article citing, a
    manual citing, a build using, where only a paper fitted."""
    o = ontology.current()
    o.check_edge("article", "cites", "manual")
    o.check_edge("document", "cites", "document")
    o.check_edge("build", "uses", "tool")
    o.check_edge("document", "proposes", "method")
    o.check_edge("person", "proposes", "concept")
    o.check_edge("person", "advised_by", "author")
    o.check_edge("page", "annotates", "recipe")
    o.check_edge("page", "synthesizes", "article")
    o.check_edge("article", "funded_by", "organization")
    # what a claim or a tool takes stays as it was
    with pytest.raises(ValueError):
        o.check_edge("claim", "cites", "paper")


def test_electronics_module() -> None:
    """A circuit's parts, what each is and its package, on top of studio
    (docs/ontology-electronics.md)."""
    o = ontology.current()
    e = o.for_domains(["electronics"])
    assert set(e.modules) == {"core", "studio", "electronics"}
    assert e.version == "core3+electronics1+studio5"
    e.check_edge("schematic", "shows_part", "component")
    e.check_edge("datasheet", "shows_part", "device")
    e.check_edge("component", "serves_as", "part_kind")
    e.check_edge("component", "in_package", "package")
    e.check_edge("component", "has_part", "component")  # studio 5
    assert e.canonical_relation("functions_as") == "serves_as"
    with pytest.raises(ValueError):
        e.check_edge("build", "shows_part", "component")  # workshop's made_with
    # studio holds the module built on it, and a studio document is still
    # read against studio alone
    assert "electronics" in o.within("studio")
    assert "part_kind" not in o.for_domains(["studio"]).types
