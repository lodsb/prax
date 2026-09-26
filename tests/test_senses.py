"""A word the graph added to a query is searched where its sense lives.

`Apfelkuchen` reaches `apple` through the ingredient's German label, and
matched everywhere that filled the first page with Logic manuals, which
name the organization (``docs/eval/apfelkuchen-2026-09-26.md``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from prax import ontology, store
from prax.store import retrieval

RECIPE = (
    "Brown butter apple bars. Peel the apple, slice the apple thinly and"
    " bake it on a buttered tray until golden. "
) * 3
MANUAL = (
    "Apple Loops ship with the application. Apple Loops are tagged audio"
    " files; the Apple Loops Utility writes the tags. "
) * 6


Library = tuple[sqlite3.Connection, int, int]


@pytest.fixture()
def library(con: sqlite3.Connection) -> Iterator[Library]:
    """A recipe in the kitchen with an apple in it, a manual in the studio
    that says Apple more often, and `Apfel` a label of the ingredient."""
    recipe = int(store.ingest_text(con, RECIPE, title="Apple bars")["doc_id"])
    manual = int(store.ingest_text(con, MANUAL, title="Logic manual")["doc_id"])
    store.set_domains(con, recipe, ["kitchen"])
    store.set_domains(con, manual, ["studio"])
    store.link(
        con,
        store.Edge("apple bars", "dish", "calls_for", "apple", "ingredient"),
        confidence="EXTRACTED",
        source_doc=recipe,
        producer="test",
        run="test",
    )
    apple = con.execute(
        "SELECT id FROM entities WHERE name = 'apple' AND type = 'ingredient'"
    ).fetchone()[0]
    store.label_in_language(con, apple, "Apfel", lang="de", run="labels")
    yield con, recipe, manual
    retrieval.SENSES = True


def _found(con: sqlite3.Connection, query: str) -> list[int]:
    return [int(h["doc_id"]) for h in store.search(con, query, mode="fts", limit=10)]


def test_a_type_lives_in_its_module_and_what_builds_on_it() -> None:
    onto = ontology.current()
    assert onto.domains_of("ingredient") == {"kitchen"}
    assert onto.domains_of("material") == {"craft", "kitchen", "workshop"}
    # a core type is everywhere, so a word for it is never scoped
    assert onto.domains_of("concept") is None
    assert onto.domains_of("organization") is None


def test_a_word_only_the_graph_added_is_a_sense(library: Library) -> None:
    con, *_ = library
    terms, senses = retrieval.expand_query_senses(con, "Apfel")
    assert terms == [["apfel", "apple"]]
    assert senses == {"apple": frozenset({"kitchen"})}
    # typed, it is the user's word and searched everywhere
    assert retrieval.expand_query_senses(con, "apple")[1] == {}


def test_the_sense_is_searched_where_it_lives(library: Library) -> None:
    con, recipe, manual = library
    assert _found(con, "Apfel") == [recipe]
    # everywhere, the manual says it more often and comes first
    retrieval.SENSES = False
    assert _found(con, "Apfel")[0] == manual
    # and a word the user typed is theirs, in every domain
    retrieval.SENSES = True
    assert set(_found(con, "apple")) == {recipe, manual}


def test_a_thing_is_also_where_the_library_found_it(library: Library) -> None:
    """`synthesis` is a research method, and the paper a question about
    "Signalsynthese" wanted could as well be in the studio: a document
    that says something about the thing widens where it lives."""
    con, _, manual = library
    store.link(
        con,
        store.Edge("studio snack", "dish", "calls_for", "apple", "ingredient"),
        confidence="EXTRACTED",
        source_doc=manual,
        producer="test",
        run="test",
    )
    assert retrieval.expand_query_senses(con, "Apfel")[1] == {
        "apple": frozenset({"kitchen", "studio"})
    }


def test_a_sense_adds_no_vote_of_its_own(library: Library) -> None:
    """Merged with the plain words by score, not fused as a list of its
    own: in its domains a chunk scores as it did before, outside as if
    the graph had not added the word."""
    con, recipe, _ = library
    retrieval.SENSES = False
    before = store.search(con, "Apfel", mode="fts", limit=10)
    retrieval.SENSES = True
    after = store.search(con, "Apfel", mode="fts", limit=10)
    score = {h["doc_id"]: h["score"] for h in before}
    assert [h["doc_id"] for h in after] == [recipe]
    assert after[0]["score"] == pytest.approx(score[recipe])


# ------------------------ layer 2: a question for one of the small domains

CAKE = "Apple cake for a crowd. " + "Stir, rest and serve warm. " * 12
LOOPS = "Apple Loops, Apple Loops Utility, Apple Loops library. " * 8
PAPER = "A study of distributed systems and their failure modes. " * 8


@pytest.fixture()
def shelves(con: sqlite3.Connection) -> Iterator[tuple[sqlite3.Connection, list[int]]]:
    """Twenty research documents, four of them manuals full of Apple, and
    three recipes in the kitchen that say apple cake once each."""
    for i in range(16):
        d = int(store.ingest_text(con, PAPER + f" part {i}.", title=f"P{i}")["doc_id"])
        store.set_domains(con, d, ["research"])
    for i in range(4):
        d = int(store.ingest_text(con, LOOPS + f" v{i}.", title=f"Logic {i}")["doc_id"])
        store.set_domains(con, d, ["research"])
    recipes = []
    for i in range(3):
        d = int(store.ingest_text(con, CAKE + f" {i}.", title=f"Cake {i}")["doc_id"])
        store.set_domains(con, d, ["kitchen"])
        recipes.append(d)
    yield con, recipes
    retrieval.DOMAIN_PRIOR = True


def _top(con: sqlite3.Connection, query: str) -> list[int]:
    return [int(h["doc_id"]) for h in store.search(con, query, limit=10)]


def test_a_small_domain_the_candidates_gather_in_gets_a_vote(
    shelves: tuple[sqlite3.Connection, list[int]],
) -> None:
    con, recipes = shelves
    retrieval.DOMAIN_PRIOR = False
    before = _top(con, "apple cake")
    retrieval.DOMAIN_PRIOR = True
    after = _top(con, "apple cake")
    assert set(after[:3]) == set(recipes)
    # a preference, not a filter: what was found is still found
    assert set(before) == set(after)


def test_the_brand_s_own_question_keeps_its_manuals(
    shelves: tuple[sqlite3.Connection, list[int]],
) -> None:
    """Three recipes out of thirty candidates would do; "Apple Loops"
    gathers none of them, so nothing moves."""
    con, recipes = shelves
    retrieval.DOMAIN_PRIOR = False
    before = _top(con, "Apple Loops")
    retrieval.DOMAIN_PRIOR = True
    assert _top(con, "Apple Loops") == before
    assert not set(before[:4]) & set(recipes)


def test_the_whole_library_is_no_small_domain(
    shelves: tuple[sqlite3.Connection, list[int]],
) -> None:
    """Research is nearly every document, so however many of the
    candidates are research, it earns no vote."""
    con, _ = shelves
    hits = store.search(con, "distributed systems failure", limit=10)
    assert all("domain_rank" not in h for h in hits)
