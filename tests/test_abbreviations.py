"""Resolution's abbreviation tier and its spelling variants (AV, the BWS
survey of 2026-11-17; ``prax.graph.resolution``): an acronym folds into
the phrase the library defines it as when that is its meaning here, a
British and an American spelling of a common name are one name, and so
are "Carbon 2" and "Carbon2"."""

from __future__ import annotations

import sqlite3

from prax import store
from prax.graph import ontology, resolution
from prax.text import names

E = store.Edge


def test_a_variant_key_respells_and_glues_numbers() -> None:
    sp = ontology.spellings()
    for a, b in (
        ("Equalisation", "equalization"),
        ("analogue synthesiser", "analog synthesizer"),
        ("polyphonic music modelling", "polyphonic music modeling"),
        ("colour space", "color space"),
    ):
        assert names.variant(names.normalize(a), sp) == names.variant(
            names.normalize(b), sp
        ), a
    assert names.variant(names.normalize("Carbon 2")) == "carbon2"
    assert names.variant(names.normalize("TR-808")) == "tr808"
    # without the spellings, nothing is respelled: a proper name's own
    assert names.variant(names.normalize("Colour Lab")) == "colour lab"


def test_two_spellings_of_one_name_merge_but_a_proper_name_keeps_its_own(
    con: sqlite3.Connection,
) -> None:
    store.link(con, E("P", "paper", "uses", "equalisation", "method"))
    store.link(con, E("Q", "paper", "uses", "Equalization", "method"))
    store.link(con, E("Q", "paper", "uses", "Carbon 2", "device"))
    store.link(con, E("R", "paper", "uses", "Carbon2", "device"))
    store.link(
        con, E("Jane Doe", "author", "affiliated_with", "Colour Lab", "organization")
    )
    store.link(
        con, E("John Roe", "author", "affiliated_with", "Color Lab", "organization")
    )
    plan = resolution.plan(con, likely=False)
    pairs = {frozenset((c.drop_name, c.keep_name)) for c in plan.sure}
    assert frozenset(("equalisation", "Equalization")) in pairs
    assert frozenset(("Carbon 2", "Carbon2")) in pairs
    assert frozenset(("Colour Lab", "Color Lab")) not in pairs
    assert {c.tier for c in plan.sure} == {"variant"}


def test_meaning_is_the_one_sense_or_a_dominant_one() -> None:
    assert resolution.meaning([("low-frequency oscillator", 3)]) == (
        "low frequency oscillator"
    )
    # one phrase written two ways is one meaning
    assert resolution.meaning(
        [("feedback delay network", 4), ("feedback delay networks", 2)]
    ) == ("feedback delay network")
    assert resolution.meaning(
        [("pulse code modulation", 34), ("phase change memory", 4)]
    )
    # five to four: two meanings, neither the acronym's
    assert (
        resolution.meaning(
            [("geographic information system", 5), ("generalized interval system", 4)]
        )
        is None
    )
    assert resolution.meaning([]) is None
    for name in ("LFO", "FDN", "fMRI", "VST3"):
        assert resolution.is_acronym(name), name
    for name in ("Lfo", "chorus", "low frequency oscillator", "A"):
        assert not resolution.is_acronym(name), name


def test_an_acronym_folds_into_its_phrase_and_stays_a_label(
    con: sqlite3.Connection,
) -> None:
    store.replace_acronyms(
        con,
        [
            ("LFO", "low-frequency oscillator", 3),
            ("GIS", "geographic information system", 5),
            ("GIS", "generalized interval system", 4),
            ("FM", "frequency modulation", 9),
        ],
    )
    store.link(con, E("P", "paper", "uses", "LFO", "concept"))
    store.link(con, E("P", "paper", "uses", "LFO", "concept"))
    store.link(con, E("Q", "paper", "uses", "low frequency oscillator", "concept"))
    store.link(con, E("Q", "paper", "uses", "LFO", "concept"))
    store.link(con, E("R", "paper", "about", "GIS", "concept"))
    store.link(con, E("S", "paper", "about", "generalized interval system", "concept"))
    # an acronym and its phrase under two types are a kind and a thing
    store.link(con, E("T", "paper", "uses", "FM", "device"))
    store.link(con, E("T", "paper", "uses", "frequency modulation", "method"))
    plan = resolution.plan(con, likely=False)
    assert [(c.drop_name, c.keep_name, c.tier) for c in plan.abbreviations] == [
        ("LFO", "low frequency oscillator", "abbreviation")
    ]
    assert resolution.apply(con, plan).merged_abbreviations == 0  # only when asked
    report = resolution.apply(con, plan, abbreviations=True, run="resolve-abbr")
    assert report.merged_abbreviations == 1
    # the phrase survives, even with fewer edges, and the acronym reaches it
    edges = store.traverse(con, "LFO", hops=1)
    assert {e["dst"] for e in edges} == {"low frequency oscillator"}
    assert {e["src"] for e in edges} == {"P", "Q"}
    keep = plan.abbreviations[0].keep
    assert "LFO" in {x["label"] for x in store.entity_labels(con, keep)}
    assert resolution.plan(con, likely=False).abbreviations == []
    # and the round is taken back whole
    assert store.unmerge_run(con, "resolve-abbr") == 1
    assert len(resolution.plan(con, likely=False).abbreviations) == 1
