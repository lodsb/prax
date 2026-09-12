"""The modular ontology: composition, subtypes, aliases, domains, the repo's
own modules, and the legacy single file."""

from __future__ import annotations

from pathlib import Path

import pytest

from prax import config, ontology

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
        "kitchen",
        "research",
        "studio",
        "workshop",
    }
    assert o.version == "core1+craft1+kitchen1+research5+studio1+workshop1"
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
    assert (
        ontology.current().version
        == "core1+craft1+kitchen1+research5+studio1+workshop1"
    )
    text = (d / "research.yaml").read_text(encoding="utf-8")
    (d / "research.yaml").write_text(
        text.replace("version: 5", "version: 6"), encoding="utf-8"
    )
    assert (
        ontology.current().version
        == "core1+craft1+kitchen1+research6+studio1+workshop1"
    )


def test_craft_kitchen_and_workshop_modules() -> None:
    o = ontology.current()
    kitchen = o.for_domains(["kitchen"])
    assert set(kitchen.modules) == {"core", "craft", "kitchen"}
    assert kitchen.version == "core1+craft1+kitchen1"
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
    assert workshop.version == "core1+craft1+studio1+workshop1"
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
    assert o.for_domains(["research"]).version == "core1+research5"


def test_studio_module() -> None:
    o = ontology.current()
    s = o.for_domains(["studio"])
    assert set(s.modules) == {"core", "studio"} and s.version == "core1+studio1"
    assert s.self_types == ("manual", "datasheet", "schematic", "article")
    assert "paper" not in s.types and "cites" not in s.relations
    assert s.is_a("device", "tool") and s.is_a("manufacturer", "organization")
    s.check_edge("manual", "describes", "device")
    s.check_edge("device", "developed_by", "manufacturer")  # core, via subtypes
    s.check_edge("device", "conforms_to", "standard")
    s.check_edge("article", "appeared_in", "publication")
    assert s.canonical_type("synthesizer") == "device"
    assert s.canonical_relation("mentions") == "names"
    with pytest.raises(ValueError):
        s.check_edge("paper", "describes", "device")
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
