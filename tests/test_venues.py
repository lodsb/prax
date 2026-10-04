"""Which venue names are one series, and which an edition of it
(``prax.graph.venues``, resolution's venue tier)."""

from __future__ import annotations

import sqlite3

import pytest

from prax import store
from prax.graph import resolution, venues
from prax.store import repair


@pytest.mark.parametrize(
    ("name", "series", "edition"),
    [
        ("DAFx", "dafx", None),
        ("DAFx-17", "dafx", "2017"),
        ("DAFx23", "dafx", "2023"),
        ("CHI '04", "chi", "2004"),
        ("CHI 99", "chi", "1999"),
        ("ISMIR 2008 – Session 3a – Content-Based Retrieval", "ismir", "2008"),
        (
            "26th Int. Society for Music Information Retrieval Conf.",
            "international society music information retrieval conference",
            "#26",
        ),
        (
            "The Thirty-Sixth AAAI Conference on Artificial Intelligence",
            "aaai conference artificial intelligence",
            "#36",
        ),
        (
            "DAFX 12, International Conference on Digital Audio Effects, York, UK",
            "dafx",
            "2012",
        ),
        ("IEEE ICASSP", "ieee icassp", None),
        ("IEEE Access (Volume 6, 2018)", "ieee access", "2018"),
        # a journal keeps its "Proceedings"; a meeting does not need it
        ("Proceedings of the IEEE", "proceedings ieee", None),
        ("Proceedings of the 2nd ISMIR Conference", "ismir conference", "#2"),
        # a plain number is part of the name; a date is no venue
        (
            "Lecture Distributed Problem Solving 10",
            "lecture distributed problem solving 10",
            None,
        ),
        ("March 2009", "", "2009"),
    ],
)
def test_a_name_reads_as_series_and_edition(
    name: str, series: str, edition: str | None
) -> None:
    v = venues.read(name)
    assert (v.series, v.edition) == (series, edition)


def _plan(
    names: list[str], expansions: dict[str, set[str]] | None = None
) -> tuple[set, set]:
    ids = dict(enumerate(names, 1))
    p = venues.plan([(i, n, 10 - i) for i, n in ids.items()], expansions or {})
    return (
        {(ids[k], ids[d]) for k, d in p.merges},
        {(ids[e], ids[s]) for e, s in p.editions},
    )


def test_an_acronym_meets_its_expansion_and_its_editions() -> None:
    merges, editions = _plan(
        [
            "International Computer Music Conference",
            "ICMC",
            "ICMC 2015",
            "Proceedings of the International Computer Music Conference 2015",
            "ICMC-87",
        ],
        {"ICMC": {"International Computer Music Conference"}},
    )
    assert ("International Computer Music Conference", "ICMC") in merges
    assert (
        "ICMC 2015",
        "Proceedings of the International Computer Music Conference 2015",
    ) in merges
    assert editions == {
        ("ICMC 2015", "International Computer Music Conference"),
        ("ICMC-87", "International Computer Music Conference"),
    }


def test_a_bracketed_acronym_is_the_names_own() -> None:
    """DAFx is not the initials of "Digital Audio Effects", but a name that
    writes it in brackets says it is its own."""
    merges, editions = _plan(
        [
            "DAFx",
            "Proc. of the 9th Int. Conference on Digital Audio Effects (DAFx-06)",
            "International Conference on Digital Audio Effects",
        ]
    )
    assert ("DAFx", "International Conference on Digital Audio Effects") in merges
    assert (
        "Proc. of the 9th Int. Conference on Digital Audio Effects (DAFx-06)",
        "DAFx",
    ) in editions


def test_what_must_stay_apart() -> None:
    merges, editions = _plan(
        [
            # one year, two ordinals: two conventions
            "AES 122nd Convention, Vienna, Austria, 2007 May 5-8",
            "123rd AES Convention 2007",
            # one acronym, two journals: SIAM is not their initials
            "SIAM Review",
            "SIAM Journal on Computing",
            # an expansion the texts got wrong (initials do not match)
            "OOPSLA'06",
            "European Conference on Object-Oriented Programming",
            # two-letter acronyms and legal forms are no acronyms here
            "AI Magazine",
            "AI & Society",
            "Bitwig GmbH",
            "Native Instruments GmbH",
        ],
        {"OOPSLA": {"European Conference on Object-Oriented Programming"}},
    )
    assert merges == set() and editions == set()


def test_the_tier_merges_links_and_quiets_the_finding(con: sqlite3.Connection) -> None:
    E = store.Edge
    for paper, venue in (
        ("Paper A", "NIME"),
        ("Paper A", "NIME 2010"),
        ("Paper B", "New Interfaces for Musical Expression (NIME)"),
        ("Paper C", "NIME2010"),
    ):
        store.link(con, E(paper, "paper", "published_in", venue, "venue"), producer="t")
    assert [f["subject"] for f in repair._functional_conflicts(con)] == ["Paper A"]
    plan = resolution.plan(con, etype="venue", likely=False)
    report = resolution.apply(con, plan, run="venues-t")
    assert report.merged_venues >= 1 and report.editions == 1
    # "NIME 2010" is part of NIME: one venue, said finer and coarser
    assert repair._functional_conflicts(con) == []
    again = resolution.plan(con, etype="venue", likely=False)
    assert (again.venues, again.editions) == ([], [])
    # a round is one run: taken back whole
    store.unmerge_run(con, "venues-t")
    assert resolution.plan(con, etype="venue", likely=False).venues


def test_a_publishers_word_does_not_keep_a_series_apart() -> None:
    merges, _ = _plan(["ICASSP", "IEEE ICASSP"])
    assert merges == {("ICASSP", "IEEE ICASSP")}


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Oxford University Press", "publisher"),
        ("Springer-Verlag London Limited", "publisher"),
        ("Native Instruments GmbH", "company"),
        ("Stanford University", "institution"),
        ("Fakultät für Informatik", "institution"),
        ("Sommersemester 2009", "none"),
        ("Übungsblatt 06", "none"),
        ("University Exercise Sheet", "none"),
        ("proprietary software license", "none"),
        # a venue word keeps a venue, whatever else the name says
        ("Journal of the Audio Engineering Society", None),
        ("Proceedings of the IEEE", None),
        ("Acta Universitatis Upsaliensis", None),
        ("Psychological Research", None),
        ("AES E-Library", None),
    ],
)
def test_what_a_venue_name_says_it_is_instead(name: str, kind: str | None) -> None:
    assert venues.not_a_venue(name) == kind


def test_the_repair_writes_what_the_fact_meant(con: sqlite3.Connection) -> None:
    E = store.Edge
    doc = store.ingest_text(con, "a monograph on counterpoint " * 20)["doc_id"]
    for venue in (
        "Oxford University Press",
        "Stanford University",
        "Sommersemester 2009",
    ):
        store.link(
            con,
            E("A monograph", "paper", "published_in", venue, "venue"),
            source_doc=doc,
            evidence="printed on the title page",
            producer="t",
        )
    store.link(
        con, E("A paper", "paper", "published_in", "DAFx", "venue"), producer="t"
    )
    found = repair._not_venues(con)
    assert {(f["name"], f["is"]) for f in found} == {
        ("Oxford University Press", "publisher"),
        ("Stanford University", "institution"),
        ("Sommersemester 2009", "none"),
    }
    assert repair.heal(con, only=["not-venues"])["not-venues"]["repaired"] == 3
    live = {
        (r[0], r[1], r[2], r[3])
        for r in con.execute(
            "SELECT e.rel, t.name, t.type, e.producer FROM edges e"
            " JOIN entities t ON t.id = e.dst WHERE e.valid_to IS NULL"
        )
    }
    assert (
        "published_by",
        "Oxford University Press",
        "organization",
        repair.NOT_VENUE_PRODUCER,
    ) in live
    assert (
        "written_at",
        "Stanford University",
        "organization",
        repair.NOT_VENUE_PRODUCER,
    ) in live
    assert ("published_in", "DAFx", "venue", "t") in live
    assert not any(rel == "published_in" and name != "DAFx" for rel, name, _, _ in live)
    assert repair._not_venues(con) == []
