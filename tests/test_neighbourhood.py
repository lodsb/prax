"""The second hop of ``traverse``: a map of the neighbourhood rather than
every edge in it (``docs/eval/traverse-neighbourhood-2026-09-25.md``)."""

from __future__ import annotations

import sqlite3

import pytest

from prax import store

E = store.Edge


def _paper(con: sqlite3.Connection, n: int) -> int:
    doc = store.ingest_text(con, f"paper {n} " * 40, title=f"Paper {n}")
    return int(doc["doc_id"])


def _link(
    con: sqlite3.Connection, src, src_type, rel, dst, dst_type, *, doc: int
) -> None:
    store.link(
        con,
        E(src, src_type, rel, dst, dst_type),
        source_doc=doc,
        producer="test",
        run="test",
    )


def _near(con: sqlite3.Connection, name: str) -> list[dict]:
    return store.traverse_map(con, name, hops=2)["edges"]


def _map(con: sqlite3.Connection, name: str) -> list[dict]:
    return store.traverse_map(con, name, hops=2)["neighbours"]


def test_the_first_hop_is_still_a_plain_edge_list(con: sqlite3.Connection) -> None:
    """The UI and the surfer ask for one hop and get the facts, evidence
    and all."""
    doc = _paper(con, 1)
    store.link(
        con,
        E("Paper 1", "paper", "about", "convolution", "method"),
        source_doc=doc,
        producer="test",
        evidence="said so",
    )
    edges = store.traverse(con, "convolution", hops=1)
    assert [e["src"] for e in edges] == ["Paper 1"]
    assert edges[0]["evidence"] == "said so"
    assert store.traverse(con, "convolution", hops=2) == edges  # hop 1 either way


def test_the_map_does_not_land_on_a_document(con: sqlite3.Connection) -> None:
    """A paper's bibliography is a record, not a neighbourhood: 22% of the
    payload measured was `paper -cites-> paper`."""
    a, b = _paper(con, 1), _paper(con, 2)
    _link(con, "Paper 1", "paper", "about", "convolution", "method", doc=a)
    _link(con, "Paper 1", "paper", "cites", "Paper 2", "paper", doc=a)
    _link(con, "Paper 1", "paper", "about", "aliasing", "concept", doc=b)

    names = {n["name"] for n in _map(con, "convolution")}
    assert "aliasing" in names  # an idea, reached through the paper
    assert "Paper 2" not in names  # a bibliography entry, dropped


def test_the_map_ranks_by_independent_documents(con: sqlite3.Connection) -> None:
    """Two documents saying so beats one saying it four times."""
    docs = [_paper(con, n) for n in range(1, 5)]
    for n, doc in enumerate(docs, 1):
        _link(con, f"Paper {n}", "paper", "about", "convolution", "method", doc=doc)
    for n, doc in zip((1, 2, 3), docs[:3]):
        _link(con, f"Paper {n}", "paper", "about", "well attested", "concept", doc=doc)
    for rel in ("about", "uses", "proposes", "mentions"):
        _link(con, "Paper 4", "paper", rel, "one source", "concept", doc=docs[3])

    by_name = {n["name"]: n for n in _map(con, "convolution")}
    assert by_name["well attested"]["documents"] == 3
    assert by_name["one source"]["documents"] == 1
    # the better attested one comes first, whatever the edge count
    order = [n["name"] for n in _map(con, "convolution")]
    assert order.index("well attested") < order.index("one source")
    # and the relations that reached it are named rather than enumerated
    assert by_name["one source"]["via"] == ["about", "mentions", "proposes", "uses"]


def test_the_map_keeps_a_quota_per_type(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ranking alone gave 44 papers in 50, because a paper accumulates
    edges mechanically while a concept does not."""
    monkeypatch.setenv("PRAX_GRAPH_PER_TYPE", "2")
    monkeypatch.setenv("PRAX_GRAPH_NEIGHBOURS", "50")
    doc = _paper(con, 1)
    _link(con, "Paper 1", "paper", "about", "convolution", "method", doc=doc)
    for n in range(5):
        _link(con, "Paper 1", "paper", "about", f"concept {n}", "concept", doc=doc)
        _link(con, "Paper 1", "paper", "uses", f"tool {n}", "tool", doc=doc)

    kinds: dict[str, int] = {}
    for n in _map(con, "convolution"):
        kinds[n["type"]] = kinds.get(n["type"], 0) + 1
    assert kinds == {"concept": 2, "tool": 2}


def test_the_map_can_require_corroboration(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """94% of a real neighbourhood is reached through exactly one
    document; a host may ask for two."""
    monkeypatch.setenv("PRAX_GRAPH_MIN_DOCUMENTS", "2")
    a, b = _paper(con, 1), _paper(con, 2)
    for n, doc in ((1, a), (2, b)):
        _link(con, f"Paper {n}", "paper", "about", "convolution", "method", doc=doc)
        _link(con, f"Paper {n}", "paper", "about", "corroborated", "concept", doc=doc)
    _link(con, "Paper 1", "paper", "about", "lonely", "concept", doc=a)

    names = {n["name"] for n in _map(con, "convolution")}
    assert "corroborated" in names
    assert "lonely" not in names


def test_the_map_says_what_it_left_out(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A map that silently drops the rest is worse than a large one."""
    monkeypatch.setenv("PRAX_GRAPH_NEIGHBOURS", "2")
    doc = _paper(con, 1)
    _link(con, "Paper 1", "paper", "about", "convolution", "method", doc=doc)
    for n in range(6):
        _link(con, "Paper 1", "paper", "about", f"concept {n}", "concept", doc=doc)

    answer = store.traverse_map(con, "convolution", hops=2)
    assert (answer["entity"], answer["hops"]) == ("convolution", 2)
    assert len(answer["neighbours"]) == 2
    assert answer["left_out"]["neighbours"] == 4
    assert answer["left_out"]["edges"] == 0

    one = store.traverse_map(con, "convolution", hops=1)
    assert one["neighbours"] == [] and one["left_out"]["neighbours"] == 0


def test_a_neighbour_carries_the_way_back(con: sqlite3.Connection) -> None:
    """No evidence at the second hop — you are orienting, not citing — but
    the documents that say so are named, so the route back exists."""
    a, b = _paper(con, 1), _paper(con, 2)
    for n, doc in ((1, a), (2, b)):
        _link(con, f"Paper {n}", "paper", "about", "convolution", "method", doc=doc)
        _link(con, f"Paper {n}", "paper", "about", "aliasing", "concept", doc=doc)

    n = _map(con, "convolution")[0]
    assert n["name"] == "aliasing" and n["type"] == "concept"
    assert n["via"] == ["about"] and n["documents"] == 2
    assert n["source_docs"] == sorted([a, b])
    assert "evidence" not in n


def test_an_entity_already_met_is_not_a_neighbour(con: sqlite3.Connection) -> None:
    """What the first hop returned does not come back as a discovery."""
    doc = _paper(con, 1)
    _link(con, "Paper 1", "paper", "about", "convolution", "method", doc=doc)
    _link(con, "Paper 1", "paper", "about", "aliasing", "concept", doc=doc)
    _link(con, "aliasing", "concept", "extends", "convolution", "method", doc=doc)

    assert [n["name"] for n in _map(con, "convolution")] == []


def test_the_first_hop_is_capped_with_every_relation_represented(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`nonnegative matrix factorization` answered its own first hop with
    319 KB of 642 edges. Taking the first N would take them in id order,
    which is extraction order: 229 `about` rows before the first
    `implements`."""
    monkeypatch.setenv("PRAX_GRAPH_EDGES", "6")
    doc = _paper(con, 1)
    for n in range(20):
        _link(con, "Paper 1", "paper", "about", f"about {n}", "concept", doc=doc)
    for n in range(4):
        _link(con, "Paper 1", "paper", "uses", f"tool {n}", "tool", doc=doc)
    _link(con, "Paper 1", "paper", "proposes", "the one method", "method", doc=doc)

    answer = store.traverse_map(con, "Paper 1", hops=1)
    assert len(answer["edges"]) == 6
    assert answer["left_out"]["edges"] == 19
    rels = {e["rel"] for e in answer["edges"]}
    assert rels == {"about", "uses", "proposes"}  # the rare one survived
    assert "the one method" in {e["dst"] for e in answer["edges"]}


def test_a_caller_can_ask_for_the_whole_list(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI's canvas draws the neighbourhood and asks for all of it."""
    monkeypatch.setenv("PRAX_GRAPH_EDGES", "3")
    doc = _paper(con, 1)
    for n in range(10):
        _link(con, "Paper 1", "paper", "about", f"about {n}", "concept", doc=doc)

    assert len(store.traverse(con, "Paper 1", hops=1)) == 3
    whole = store.traverse_map(con, "Paper 1", hops=1, limit=0)
    assert len(whole["edges"]) == 10 and whole["left_out"]["edges"] == 0


def test_a_small_entity_is_not_capped(con: sqlite3.Connection) -> None:
    doc = _paper(con, 1)
    _link(con, "Paper 1", "paper", "about", "convolution", "method", doc=doc)
    answer = store.traverse_map(con, "convolution", hops=1)
    assert len(answer["edges"]) == 1 and answer["left_out"]["edges"] == 0
