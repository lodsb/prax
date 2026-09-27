"""The regions of the library: topical entities partitioned at two levels,
kept across rebuilds, named by traverse (``prax.communities``,
docs/communities.md)."""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import communities, store

SIGNAL = ["fourier transform", "wavelet", "spectrogram", "filter bank", "windowing"]
KITCHEN = ["salt", "onion", "garlic", "olive oil", "butter"]


def test_every_document_weighs_the_same_and_one_mention_is_left_out() -> None:
    docs = [{1, 2}, {1, 2, 3, 4, 5}, {1, 9}, {3, 4, 6}, {6, 7, 8}]
    pairs = communities.graph_of(docs, Counter({(1, 9): 2}), min_docs=2)
    # 5, 7, 8 and 9 are named once: out, and so is the direct edge to 9
    assert all(9 not in pair and 5 not in pair for pair in pairs)
    # {1, 2} alone: 1; with 3 and 4 beside them: 1/3 more
    assert pairs[(1, 2)] == pytest.approx(1 + 1 / 3)
    assert pairs[(3, 4)] == pytest.approx(1 / 3 + 1 / 2)
    assert pairs[(3, 6)] == pytest.approx(1 / 2)


def test_two_regions_and_a_large_one_split_in_parts() -> None:
    a, b = list(range(1, 7)), list(range(11, 17))
    pairs = {}
    for group in (a, b):
        for i in group:
            for j in group:
                if i < j:
                    pairs[(i, j)] = 1.0
    pairs[(6, 11)] = 0.1  # a thin bridge
    found = communities.partition(pairs, split=100)
    assert sorted(sorted(r) for r in found.regions) == [a, b]
    assert [len(p) for _, p in found.parts] == [6, 6]  # small regions: one part
    assert found.modularity and found.modularity > 0.3
    # a region of the splitting size is parted inside (on the library, the
    # 1,634 members of the largest became eleven parts); whatever the parts,
    # they cover their region exactly
    parted = communities.partition(pairs, split=5)
    for i, region in enumerate(parted.regions):
        mine = [part for parent, part in parted.parts if parent == i]
        assert frozenset().union(*mine) == region
        assert sum(len(part) for part in mine) == len(region)


def _library(con: sqlite3.Connection, extra: int = 0) -> None:
    """Four papers on signal analysis, four recipes, all naming their
    things; ``extra`` more recipes that name a new ingredient too."""
    for n in range(4):
        doc = int(
            store.ingest_text(con, f"Paper {n}. " * 30, title=f"Paper {n}")["doc_id"]
        )
        for thing in SIGNAL[n % 2 :]:
            store.link(
                con,
                store.Edge(f"Paper {n}", "paper", "uses", thing, "method"),
                confidence="EXTRACTED",
                source_doc=doc,
                producer="t",
                run="t",
            )
    for n in range(4 + extra):
        doc = int(
            store.ingest_text(con, f"Dish {n}. " * 30, title=f"Dish {n}")["doc_id"]
        )
        store.set_domains(con, doc, ["kitchen"])
        wanted = KITCHEN[n % 2 :] + (["paprika", "cumin"] if n >= 4 else [])
        for thing in wanted:
            store.link(
                con,
                store.Edge(f"Dish {n}", "dish", "calls_for", thing, "ingredient"),
                confidence="EXTRACTED",
                source_doc=doc,
                producer="t",
                run="t",
            )


def _names(con: sqlite3.Connection, level: int = 0) -> list[set[str]]:
    return [set(c["members"]) for c in store.list_communities(con, level=level)]


def test_the_pass_finds_the_regions_and_traverse_names_them(
    con: sqlite3.Connection,
) -> None:
    _library(con)
    done = store.maintain(con, only=["communities"])["communities"]
    assert done["regions"] == 2 and done["new"] == 4  # two regions, two parts
    regions = _names(con)
    # the dishes and papers themselves are not members: they are documents
    # (a paper) or named once each (a dish)
    assert set(SIGNAL) in regions and set(KITCHEN) in regions
    walk = store.traverse_map(con, "garlic", 1)
    region = walk["community"][0]
    assert region["level"] == 0 and region["size"] == 5
    assert [c["level"] for c in walk["community"]] == [0, 1]
    one = store.community(con, region["id"])
    assert one is not None
    assert {m["name"] for m in one["members"]} == set(KITCHEN)
    assert {d["title"] for d in one["documents"]} == {f"Dish {n}" for n in range(4)}
    assert one["summary_state"] == "none"
    assert store.communities_to_summarize(con)[0] in {
        c["id"] for c in store.list_communities(con)
    }
    # a paper is outside the partition: no community key
    assert "community" not in store.traverse_map(con, "Paper 1", 1)


def test_a_rebuild_keeps_ids_and_summaries_and_says_when_they_moved(
    con: sqlite3.Connection,
) -> None:
    _library(con)
    store.maintain(con, only=["communities"])
    kitchen = next(c for c in store.list_communities(con) if "garlic" in c["members"])
    assert store.set_community_summary(
        con,
        kitchen["id"],
        label="Everyday cooking",
        summary="Onions. Garlic.",
        source="test",
    )
    # the same graph again: the same ids, the summary kept and fresh
    store.maintain(con, only=["communities"])
    again = next(c for c in store.list_communities(con) if "garlic" in c["members"])
    assert again["id"] == kitchen["id"] and again["label"] == "Everyday cooking"
    assert again["summary_state"] == "fresh"
    # two more ingredients in two more recipes: 5 of 7 members the same,
    # so the id and the summary stay, and the summary is marked stale
    _library(con, extra=2)
    store.maintain(con, only=["communities"])
    moved = next(c for c in store.list_communities(con) if "garlic" in c["members"])
    assert "paprika" in moved["members"]
    assert moved["id"] == kitchen["id"] and moved["summary_state"] == "stale"
    assert kitchen["id"] in store.communities_to_summarize(con)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_the_door_lists_them(client: TestClient) -> None:
    con = client.app.state.con
    _library(con)
    store.maintain(con, only=["communities"])
    listed = client.get("/communities").json()["communities"]
    assert len(listed) == 2 and all(c["size"] == 5 for c in listed)
    one = client.get(f"/communities/{listed[0]['id']}").json()
    assert len(one["parts"]) == 1 and one["parts"][0]["parent"] == listed[0]["id"]
    assert client.get("/communities/99999").status_code == 404
