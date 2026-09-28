"""The regions of the library: topical entities partitioned at two levels,
kept across rebuilds, named by traverse (``prax.graph.communities``,
docs/communities.md)."""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import models, store, worker
from prax.graph import communities

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


def test_a_search_says_where_its_hits_live(client: TestClient) -> None:
    """ "A way in": with ``regions`` a search opens with the region most of
    its first hits' entities live in, when it holds half their weight
    (docs/eval/regions-2026-09-28.md). The part is given only when it holds
    nine tenths of the region's parts; a search without the flag is the
    list of hits it always was."""
    con = client.app.state.con
    _library(con)
    store.maintain(con, only=["communities"])
    kitchen = next(c for c in store.list_communities(con) if "garlic" in c["members"])
    store.set_community_summary(
        con,
        kitchen["id"],
        label="Everyday cooking",
        summary="Onions. Garlic.",
        source="t",
    )
    plain = client.get("/search", params={"q": "Dish"}).json()
    assert plain and all("doc_id" in h for h in plain)
    got = client.get("/search", params={"q": "Dish", "regions": True}).json()
    where = got[0]
    assert where["kind"] == "region" and where["region"]["id"] == kitchen["id"]
    assert (
        where["region"]["label"] == "Everyday cooking"
        and where["region"]["share"] >= 0.5
    )
    assert where["region"]["summary"] == "Onions"
    assert [h["doc_id"] for h in got[1:]] == [h["doc_id"] for h in plain]
    # a region without a name is not shown; nor is one the hits are spread over
    dishes = [h["doc_id"] for h in plain if h["title"].startswith("Dish")]
    papers = [
        int(r[0])
        for r in con.execute("SELECT id FROM documents WHERE title LIKE 'Paper%'")
    ]
    assert store.regions_of(con, papers) is None  # its region has no name yet
    assert store.regions_of(con, dishes + papers, share=0.99) is None


def test_ask_can_bring_the_region_its_passages_come_from(
    con: sqlite3.Connection,
) -> None:
    """With ``regions`` the bundle carries the region most of its passages
    live in, its summary and its parts, marked as the library's map for
    the model to describe and not to cite; without, nothing changes."""
    from prax.answering import ask

    _library(con)
    store.maintain(con, only=["communities"])
    kitchen = next(c for c in store.list_communities(con) if "garlic" in c["members"])
    store.set_community_summary(
        con,
        kitchen["id"],
        label="Everyday cooking",
        summary="Onions and garlic in soups.",
        source="t",
    )
    plain = ask.gather(con, "Dish")
    assert (
        plain.region is None and "The region of the library" not in plain.as_message()
    )
    bundle = ask.gather(con, "Dish", regions=True)
    assert bundle.region is not None and bundle.region["label"] == "Everyday cooking"
    said = bundle.as_message()
    assert "The region of the library these passages come from" in said
    assert "Everyday cooking: Onions and garlic in soups." in said
    assert bundle.to_dict()["region"]["id"] == kitchen["id"]


# ---------------------------------------------------------------- the words


class Runtime:
    """A model that answers the same, whatever it is asked."""

    name = "stub"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, Any]]:
        self.asked.append(user)
        return self.answer, {}


def test_a_name_and_what_it_covers_out_of_the_answer() -> None:
    good = "Everyday cooking\nThis region covers onions, garlic and olive oil in soups."
    assert communities.parse(good) == (
        "Everyday cooking",
        "Covers onions, garlic and olive oil in soups.",
    )
    assert (
        communities.parse("**Kitchen**\n\nSalt, butter and garlic in stews and bakes.")[
            0
        ]
        == "Kitchen"
    )
    assert communities.parse("Only a name") is None  # no summary
    assert (
        communities.parse("x" * 80 + "\nA long enough summary of the region.") is None
    )
    item = {
        "region": "Signal analysis",
        "members": [{"name": "wavelet", "type": "method"}],
        "documents": [{"title": "Paper 1"}],
    }
    asked = communities.user_message(item)
    assert "part of the region “Signal analysis”" in asked
    assert "- wavelet (method)" in asked and "- Paper 1" in asked


def test_the_regions_are_named_through_the_door(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    _library(con)
    store.maintain(con, only=["communities"])
    monkeypatch.setenv("PRAX_COMMUNITIES", "stub")
    rt = Runtime(
        "Everyday cooking\nOnions, garlic and olive oil, the base of most dishes."
    )
    monkeypatch.setattr(models, "runtime", lambda spec: rt)
    door = worker.Door("http://testserver", client=client, name="test-worker")
    first = worker.run_once(door, steps=("communities",), log_=lambda t: None)
    assert first["communities"] == "2 named"  # the regions: parts wait for them
    assert not any("part of the region" in q for q in rt.asked)
    second = worker.run_once(door, steps=("communities",), log_=lambda t: None)
    assert second["communities"] == "2 named"
    assert all("part of the region “Everyday cooking”" in q for q in rt.asked[2:])
    assert store.communities_to_summarize(con) == []
    listed = client.get("/communities").json()["communities"]
    assert {c["label"] for c in listed} == {"Everyday cooking"}
    assert all(c["summary_state"] == "fresh" for c in listed)
