"""A compound is split where both halves are words the library uses
(``docs/eval/apfelkuchen-2026-09-26.md``)."""

from __future__ import annotations

import sqlite3

from prax import compounds, store


def _stock(con: sqlite3.Connection, word: str, n: int = 4) -> None:
    """Enough documents to make a word one the library uses."""
    for i in range(n):
        store.ingest_text(con, f"{word} " * 30 + f"filler {i} " * 20)


def test_a_compound_splits_where_both_halves_are_words(
    con: sqlite3.Connection,
) -> None:
    _stock(con, "Apfel")
    _stock(con, "Kuchen")
    assert compounds.split(con, "Apfelkuchen") == ["apfel", "kuchen"]


def test_a_word_the_library_holds_is_not_split(con: sqlite3.Connection) -> None:
    """FTS matches it directly, so splitting could only add noise. This is
    why `Betriebssystem` and `wavetable` come back untouched from the real
    library: they are terms of their own."""
    _stock(con, "Apfel")
    _stock(con, "Kuchen")
    _stock(con, "Apfelkuchen")
    assert compounds.split(con, "Apfelkuchen") == []


def test_a_half_the_library_does_not_use_is_not_a_split(
    con: sqlite3.Connection,
) -> None:
    """A library contains typos, and a typo is not a word."""
    _stock(con, "Apfel")
    store.ingest_text(con, "kuchen " * 30)  # one document only
    assert compounds.split(con, "Apfelkuchen") == []


def test_german_glues_a_letter_between_the_halves(con: sqlite3.Connection) -> None:
    """`Schokoladenkuchen` is `Schokolade` + n + `Kuchen`, which the real
    library splits correctly."""
    _stock(con, "Schokolade")
    _stock(con, "Kuchen")
    assert compounds.split(con, "Schokoladenkuchen") == ["schokolade", "kuchen"]


def test_a_short_word_is_left_alone(con: sqlite3.Connection) -> None:
    _stock(con, "ap")
    _stock(con, "ple")
    assert compounds.split(con, "apple") == []


def test_the_longest_left_half_wins(con: sqlite3.Connection) -> None:
    """`Apfelkuchen` must be `Apfel` + `Kuchen` and never `Ap` + `felkuchen`
    or some shorter accident, so the cut is tried from the right."""
    _stock(con, "Apfel")
    _stock(con, "Kuchen")
    _stock(con, "Apf")
    _stock(con, "elkuchen")
    assert compounds.split(con, "Apfelkuchen") == ["apfel", "kuchen"]


def test_expand_names_only_the_tokens_that_split(con: sqlite3.Connection) -> None:
    _stock(con, "Apfel")
    _stock(con, "Kuchen")
    got = compounds.expand(con, ["Rezept", "Apfelkuchen", "Apfelkuchen"])
    assert got == {"Apfelkuchen": ["apfel", "kuchen"]}


def test_the_halves_reach_the_query_and_the_graph(con: sqlite3.Connection) -> None:
    """The chain the whole thing is for: a German compound reaches an
    English document through its half's label. Measured on the real
    library as `Olivenoel` -> `oliven` -> `olive`."""
    _stock(con, "Oliven")
    _stock(con, "Kuchen")
    store.link(
        con,
        store.Edge("a recipe", "recipe", "calls_for", "olive oil", "ingredient"),
        producer="test",
    )
    eid = con.execute("SELECT id FROM entities WHERE name = 'olive oil'").fetchone()[0]
    store.add_label(con, eid, "Oliven", lang="de", producer="test")

    terms = store.expand_query(con, "Olivenkuchen")
    assert "oliven" in terms[0]  # the half
    assert "olive oil" in terms[0]  # and what the graph calls it
