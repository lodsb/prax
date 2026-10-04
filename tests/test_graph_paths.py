"""How is A connected to B (stage AM): the path index's search and the
store's answer."""

from __future__ import annotations

import sqlite3

import pytest

from prax import store
from prax.graph import paths

E = store.Edge


def _index(*facts: tuple[int, str, int, str, int | None]) -> paths.PathIndex:
    """Facts as (src, rel, dst, confidence, document), edge ids in order."""
    return paths.build(
        (i, s, r, d, c, doc) for i, (s, r, d, c, doc) in enumerate(facts, 1)
    )


def _nodes(ix: paths.PathIndex, p: paths.Path) -> list[int]:
    out = [int(ix.ids[ix.src[p.facts[0]]])]
    for f in p.facts:
        a, b = int(ix.ids[ix.src[f]]), int(ix.ids[ix.dst[f]])
        out.append(b if a == out[-1] else a)
    return out


def test_a_path_avoids_a_hub_when_it_can() -> None:
    """1 and 2 meet through 9, which joins forty things, and through 5,
    which joins nothing else: the quiet way is the better one."""
    hub = [(9, "about", 100 + i, "EXTRACTED", None) for i in range(40)]
    ix = _index(
        (1, "about", 9, "EXTRACTED", None),
        (2, "about", 9, "EXTRACTED", None),
        (1, "cites", 5, "EXTRACTED", None),
        (5, "cites", 2, "EXTRACTED", None),
        *hub,
    )
    found = paths.connect(ix, [1], [2], k=3)
    assert _nodes(ix, found[0]) == [1, 5, 2]
    assert found[0].cost < found[1].cost and _nodes(ix, found[1]) == [1, 9, 2]


def test_relations_hidden_documents_and_bans_close_a_way() -> None:
    ix = _index(
        (1, "cites", 2, "EXTRACTED", 10),
        (1, "mentions", 3, "EXTRACTED", 11),
        (3, "mentions", 2, "EXTRACTED", 11),
    )
    assert [len(p.facts) for p in paths.connect(ix, [1], [2])] == [1, 2]
    assert [
        len(p.facts) for p in paths.connect(ix, [1], [2], relations=["mentions"])
    ] == [2]
    # a fact whose only document is hidden is not crossed
    assert [
        len(p.facts) for p in paths.connect(ix, [1], [2], hidden=frozenset({10}))
    ] == [2]
    assert paths.connect(ix, [1], [2], hidden=frozenset({10, 11})) == []
    assert [
        len(p.facts) for p in paths.connect(ix, [1], [2], banned=frozenset({0}))
    ] == [2]


def test_evidence_costs_by_relation_confidence_and_documents() -> None:
    assert paths.hop_cost("cites", "EXTRACTED", 1) < paths.hop_cost(
        "about", "EXTRACTED", 1
    )
    assert paths.hop_cost("about", "EXTRACTED", 1) < paths.hop_cost(
        "mentions", "EXTRACTED", 1
    )
    assert paths.hop_cost("cites", "EXTRACTED", 1) < paths.hop_cost(
        "cites", "INFERRED", 1
    )
    assert paths.hop_cost("cites", "EXTRACTED", 4) < paths.hop_cost(
        "cites", "EXTRACTED", 1
    )


def test_hops_are_limited() -> None:
    chain = [(i, "cites", i + 1, "EXTRACTED", None) for i in range(1, 7)]
    ix = _index(*chain)
    assert paths.connect(ix, [1], [7], max_hops=4) == []
    assert len(paths.connect(ix, [1], [5], max_hops=4)[0].facts) == 4


@pytest.fixture
def fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every call sees the edges as they are."""
    monkeypatch.setattr(store.graph.paths, "PATH_REFRESH", 0)


def test_the_store_answers_with_hops_and_their_documents(
    con: sqlite3.Connection, fresh: None
) -> None:
    doc = store.ingest_text(con, "wavelets and their uses in audio " * 20)["doc_id"]
    for e in (
        E("Paper A", "paper", "uses", "wavelet transform", "method"),
        E("Paper B", "paper", "uses", "wavelet transform", "method"),
    ):
        store.link(con, e, source_doc=doc, evidence="we use the wavelet transform")
    got = store.connect_entities(con, "Paper A", "Paper B")
    [path] = got["paths"]
    assert [(h["src"], h["rel"], h["dst"]) for h in path["hops"]] == [
        ("Paper A", "uses", "wavelet transform"),
        ("Paper B", "uses", "wavelet transform"),
    ]
    assert path["hops"][0]["source_doc"] == doc
    assert path["hops"][0]["evidence"] == "we use the wavelet transform"
    assert path["cost"] <= got["sound_at"]
    assert store.connect_entities(con, "Paper A", "Nobody")["unknown"] == ["Nobody"]


def test_no_sound_path_is_an_answer(con: sqlite3.Connection, fresh: None) -> None:
    """Two weak hops through a busy entity: past the line, so left out and
    counted; asked for, shown and marked."""
    for i in range(60):
        store.link(con, E(f"Note {i}", "document", "mentions", "audio", "concept"))
    got = store.connect_entities(con, "Note 1", "Note 2")
    assert got["paths"] == [] and got["weak_left_out"] >= 1 and got["best_cost"] > 6
    shown = store.connect_entities(con, "Note 1", "Note 2", weak=True)
    assert shown["paths"][0]["weak"] is True


def test_a_new_edge_is_seen_once_the_index_is_refreshed(
    con: sqlite3.Connection, fresh: None
) -> None:
    store.link(con, E("Paper A", "paper", "cites", "Paper C", "paper"))
    assert not store.connect_entities(con, "Paper A", "Paper B").get("paths")
    store.link(con, E("Paper C", "paper", "cites", "Paper B", "paper"))
    assert store.connect_entities(con, "Paper A", "Paper B")["paths"]
