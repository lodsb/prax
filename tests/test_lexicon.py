"""The words that say what a name is are data beside the types.

They were regular expressions in `prax.review`, invisible to the
ontology they describe (`docs/stratification.md`, stratum C).
"""

from __future__ import annotations

import pytest

from prax import ontology, review


def test_the_lexicon_loads_beside_the_modules() -> None:
    lex = ontology.lexicon()
    assert lex.version != "0"
    assert "universit" in lex.organization[0]  # a stem
    assert "mit" in lex.organization[1]  # a whole word
    assert "gmbh" in lex.top_organization[0]
    assert "unknown" in lex.exact
    assert "this" in lex.vague_start


def test_the_lexicon_is_not_a_module() -> None:
    """Composed as one it would join the version string, and every
    document would look unread against the new version."""
    onto = ontology.current()
    assert "lexicon" not in onto.modules
    assert "lexicon" not in onto.version
    assert onto.version.startswith("core3+")


def test_a_name_says_its_type_from_the_lexicon() -> None:
    lex = ontology.lexicon()
    assert lex.type_of("a spectral subtraction algorithm") == "method"
    assert lex.type_of("the MusicNet corpus") == "dataset"
    assert lex.type_of("an audio plugin") == "tool"
    assert lex.type_of("reverberation") is None


@pytest.mark.parametrize(
    "name",
    [
        "Stanford University",
        "Waves Audio",
        "Fraunhofer IIS",
        "LMA-CNRS",
        "Acme GmbH",
    ],
)
def test_an_organization_is_still_recognised(name: str) -> None:
    assert review._looks_org(name)


@pytest.mark.parametrize("name", ["Julius Smith", "Ann Author", "reverberation"])
def test_what_is_not_an_organization_still_is_not(name: str) -> None:
    assert not review._looks_org(name)


@pytest.mark.parametrize(
    "name",
    [
        "unknown",
        "N/A",
        "source",
        "this inference scheme",
        "the proposed method",
        "(unknown paper)",
        "supporting document",
        "Figure 3",
    ],
)
def test_a_placeholder_is_still_a_placeholder(name: str) -> None:
    assert review._placeholder(name)


@pytest.mark.parametrize("name", ["The Beatles", "Various Artists", "Waves Audio"])
def test_a_real_name_that_starts_like_a_placeholder_survives(name: str) -> None:
    """`vague_start` is matched case-sensitively on purpose."""
    assert not review._placeholder(name)


def test_a_lab_sits_inside_a_university_and_not_the_other_way_round() -> None:
    assert review._inside("Acoustics Laboratory", "Stanford University")
    assert not review._inside("Stanford University", "Acoustics Laboratory")
    # two universities are more likely aliases of one than nested bodies
    assert not review._inside("Stanford University", "Harvard University")


def test_a_host_without_a_lexicon_falls_back_to_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "core.yaml").write_text(
        "module: core\nversion: 1\nentity_types:\n  thing:\n    description: a thing\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRAX_ONTOLOGY", str(tmp_path))
    lex = ontology.lexicon()
    assert lex.version == "0"
    assert lex.organization == ((), ())
    assert lex.type_of("an algorithm") is None
