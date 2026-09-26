"""A name is not an identity: a walk from a name that reaches several
things walks one and names the others (``store.senses``).

A traverse of `apple` returned the recipe's ingredient and Apple the
company as one answer, and most names held by more than one entity are a
document beside its topic, which are two things too
(``docs/eval/fractured-names-2026-09-27.md``)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import store, surf


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _edge(con: sqlite3.Connection, e: store.Edge, doc: int | None = None) -> None:
    store.link(con, e, confidence="EXTRACTED", source_doc=doc, producer="t", run="t")


def _apples(con: sqlite3.Connection) -> None:
    """The ingredient in one recipe, the company in two documents."""
    recipe = int(store.ingest_text(con, "Apple cake. " * 20, title="Cake")["doc_id"])
    manual = int(store.ingest_text(con, "Apple Loops. " * 20, title="Logic")["doc_id"])
    store.set_domains(con, recipe, ["kitchen"])
    _edge(
        con,
        store.Edge("apple cake", "dish", "calls_for", "apple", "ingredient"),
        recipe,
    )
    _edge(
        con,
        store.Edge("Logic", "tool", "developed_by", "apple", "organization"),
        manual,
    )
    _edge(
        con,
        store.Edge("Xgrid", "tool", "developed_by", "apple", "organization"),
        manual,
    )


def _others(edges: list[dict]) -> set[str]:
    return {e["src"] if e["dst"] == "apple" else e["dst"] for e in edges}


def test_a_name_of_two_things_walks_one_and_names_both(con: sqlite3.Connection) -> None:
    _apples(con)
    got = store.traverse_map(con, "apple", 1)
    # the most connected, and nothing of the other
    assert _others(got["edges"]) == {"Logic", "Xgrid"}
    senses = {(s["type"], s["walked"]) for s in got["senses"]}
    assert senses == {("organization", True), ("ingredient", False)}
    kitchen = next(s for s in got["senses"] if s["type"] == "ingredient")
    assert kitchen["documents"] == 1 and kitchen["domains"] == ["kitchen"]


def test_the_type_says_which(con: sqlite3.Connection) -> None:
    _apples(con)
    got = store.traverse_map(con, "apple", 1, type="ingredient")
    assert _others(got["edges"]) == {"apple cake"}
    assert next(s for s in got["senses"] if s["walked"])["type"] == "ingredient"
    # the fact list follows the same choice
    assert _others(store.traverse(con, "apple", 1, type="ingredient")) == {"apple cake"}
    # a type nothing of that name has: nothing walked, the senses said
    none = store.traverse_map(con, "apple", 1, type="person")
    assert none["edges"] == [] and len(none["senses"]) == 2


def test_one_thing_under_two_types_is_walked_whole(con: sqlite3.Connection) -> None:
    """A type and its subtype (an author who is a person) is one thing
    the extractor typed twice: a walk takes both, and names no senses."""
    _edge(
        con,
        store.Edge("Onset Detection", "paper", "authored_by", "Simon Dixon", "author"),
    )
    _edge(
        con,
        store.Edge("Simon Dixon", "person", "affiliated_with", "QMUL", "organization"),
    )
    got = store.traverse_map(con, "Simon Dixon", 1)
    assert {e["rel"] for e in got["edges"]} == {"authored_by", "affiliated_with"}
    assert "senses" not in got


def test_a_name_of_one_thing_carries_no_senses(con: sqlite3.Connection) -> None:
    _edge(con, store.Edge("Onset Detection", "paper", "about", "onsets", "concept"))
    assert "senses" not in store.traverse_map(con, "onsets", 1)
    assert store.traverse_map(con, "nothing at all", 1)["edges"] == []


def test_the_door_passes_the_type(client: TestClient) -> None:
    con = client.app.state.con
    _apples(con)
    got = client.get(
        "/traverse", params={"entity": "apple", "type": "ingredient"}
    ).json()
    assert _others(got["edges"]) == {"apple cake"}
    assert len(got["senses"]) == 2


def test_the_surfer_is_told_the_other_things_and_can_walk_one(
    con: sqlite3.Connection,
) -> None:
    _apples(con)
    s = surf.Surf("q", "q", [], None, 6, 3, 4000)
    first = surf.do_walk(con, s, "apple")
    assert "developed_by" in first and "walk: apple (ingredient)" in first
    other = surf.do_walk(con, s, "apple (ingredient)")
    assert "calls_for" in other and "developed_by" not in other
    assert "walk: apple (organization)" in other
