"""Entity resolution and edge invalidation: normalization, the two tiers,
merges as canonical pointers, traversal over canonical ids."""

from __future__ import annotations

import sqlite3

import pytest

from prax import embeddings, resolution, store

E = store.Edge


@pytest.fixture(autouse=True)
def hash_embedder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRAX_EMBED", "hash")
    embeddings._build.cache_clear()
    yield
    embeddings._build.cache_clear()


def test_normalize_and_initials() -> None:
    n = resolution.normalize
    assert n("Julius O. Smith III") == n("julius o smith") == "julius o smith"
    assert n("Fettweis, Alfred", plural=False) == "fettweis alfred"
    assert n("Wave Digital Filters") == n("wave digital filter")
    assert n("Févotte") == "fevotte"
    assert resolution.initials_form("J. O. Smith") == ("j", "o", "smith")
    assert resolution.initials_form("Julius O. Smith") == ("j", "o", "smith")
    assert resolution.initials_form("Smith") is None
    assert resolution._is_initials_only("J. O. Smith")
    assert not resolution._is_initials_only("Julius O. Smith")


def _graph(con: sqlite3.Connection) -> None:
    store.link(con, E("Paper A", "paper", "authored_by", "Julius O. Smith", "author"))
    store.link(con, E("Paper B", "paper", "authored_by", "J. O. Smith", "author"))
    store.link(
        con, E("Paper C", "paper", "authored_by", "julius o smith iii", "author")
    )
    store.link(con, E("Paper D", "paper", "authored_by", "Jane Doe", "author"))
    store.link(con, E("Paper A", "paper", "about", "wave digital filters", "concept"))
    store.link(con, E("Paper B", "paper", "about", "Wave Digital Filter", "concept"))
    store.link(con, E("Paper D", "paper", "about", "reverberation", "concept"))


def test_sure_tier_merges_and_traverse_follows(con: sqlite3.Connection) -> None:
    _graph(con)
    plan = resolution.plan(con, embed=False)
    pairs = {(c.drop_name, c.keep_name) for c in plan.sure}
    # normalized equality: the two wave-digital-filter spellings, the iii author
    assert ("Wave Digital Filter", "wave digital filters") in pairs or (
        "wave digital filters",
        "Wave Digital Filter",
    ) in pairs
    assert ("julius o smith iii", "Julius O. Smith") in pairs
    # initials form: J. O. Smith is an abbreviation of Julius O. Smith
    assert ("J. O. Smith", "Julius O. Smith") in pairs
    assert not any("Jane" in a or "Jane" in b for a, b in pairs)
    assert plan.likely == []
    report = resolution.apply(con, plan)
    assert report.merged_sure == 3
    # traversal from any alias sees the merged node's edges under one name
    for name in ("Julius O. Smith", "J. O. Smith", "julius o smith iii"):
        edges = store.traverse(con, name, hops=1)
        papers = {e["src"] for e in edges if e["rel"] == "authored_by"}
        assert papers == {"Paper A", "Paper B", "Paper C"}
        assert {e["dst"] for e in edges if e["rel"] == "authored_by"} == {
            "Julius O. Smith"
        }
    # the concept merge: both papers reach the survivor
    wdf = store.traverse(con, "Wave Digital Filter", hops=1)
    assert {e["src"] for e in wdf} == {"Paper A", "Paper B"}
    # lookups hide merged aliases; the raw edges keep their alias ids
    assert [e["name"] for e in store.find_entities(con, "smith")] == ["Julius O. Smith"]
    assert con.execute("SELECT count(*) FROM edges").fetchone()[0] == 7
    # idempotent
    assert resolution.plan(con, embed=False).sure == []


def test_merge_rules(con: sqlite3.Connection) -> None:
    _graph(con)
    a = con.execute("SELECT id FROM entities WHERE name = 'Paper A'").fetchone()[0]
    smith = con.execute(
        "SELECT id FROM entities WHERE name = 'Julius O. Smith'"
    ).fetchone()[0]
    jos = con.execute("SELECT id FROM entities WHERE name = 'J. O. Smith'").fetchone()[
        0
    ]
    with pytest.raises(ValueError, match="different types"):
        store.merge_entities(con, a, smith)
    with pytest.raises(ValueError, match="itself"):
        store.merge_entities(con, smith, smith)
    store.merge_entities(con, jos, smith)
    with pytest.raises(ValueError, match="cycle"):
        store.merge_entities(con, smith, jos)
    # chains flatten: merging X into an alias lands on the survivor
    iii = con.execute(
        "SELECT id FROM entities WHERE name = 'julius o smith iii'"
    ).fetchone()[0]
    store.merge_entities(con, iii, jos)
    assert store.canonical_entity(con, iii) == smith
    with pytest.raises(KeyError):
        store.merge_entities(con, 999, smith)


def test_concept_method_twins_merge_into_the_method(con: sqlite3.Connection) -> None:
    store.link(con, E("P", "paper", "about", "empirical mode decomposition", "concept"))
    store.link(con, E("Q", "paper", "uses", "Empirical Mode Decomposition", "method"))
    store.link(con, E("Q", "paper", "about", "timbre", "concept"))
    plan = resolution.plan(con, embed=False)
    assert plan.sure == []
    assert [(c.drop_name, c.keep_name) for c in plan.twins] == [
        ("empirical mode decomposition", "Empirical Mode Decomposition")
    ]
    with pytest.raises(ValueError, match="different types"):
        store.merge_entities(con, plan.twins[0].drop, plan.twins[0].keep)
    assert resolution.apply(con, plan).merged_twins == 0  # only when asked
    report = resolution.apply(con, plan, twins=True)
    assert report.merged_twins == 1
    edges = store.traverse(con, "Empirical Mode Decomposition", hops=1)
    assert {(e["src"], e["rel"]) for e in edges} == {("P", "about"), ("Q", "uses")}
    assert [e["name"] for e in store.find_entities(con, "mode decomposition")] == [
        "Empirical Mode Decomposition"
    ]
    assert resolution.plan(con, embed=False).twins == []


def test_likely_tier_needs_an_adjudicator(con: sqlite3.Connection) -> None:
    store.link(con, E("P", "paper", "about", "granular synthesis", "concept"))
    store.link(con, E("Q", "paper", "about", "granular synthesis method", "concept"))
    store.link(con, E("R", "paper", "about", "room acoustics", "concept"))
    # the hash embedder scores shared words; 0.8 stands in for bge's 0.92
    plan = resolution.plan(con, embed=True, likely_threshold=0.8)
    assert plan.sure == []
    assert [c.drop_name for c in plan.likely] and all(
        c.type == "concept" for c in plan.likely
    )
    assert resolution.apply(con, plan).declined == len(plan.likely)  # default: no
    accept_all = resolution.StubAdjudicator(threshold=0.0)
    report = resolution.apply(con, plan, adjudicator=accept_all)
    assert report.merged_likely >= 1
    assert resolution.plan(con, embed=True, likely_threshold=0.8).likely == []


def test_invalidate_edge_with_successor(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "evidence text", title="d")["doc_id"]
    old = store.link(
        con, E("claim one", "claim", "supports", "claim two", "claim"), source_doc=doc
    )
    new = store.invalidate_edge(
        con,
        old,
        successor=E("claim one", "claim", "contradicts", "claim two", "claim"),
        source_doc=doc,
        evidence="later result",
    )
    rows = store.traverse(con, "claim one", hops=1)
    assert [r["edge_id"] for r in rows] == [new] and rows[0][
        "evidence"
    ] == "later result"
    history = con.execute(
        "SELECT id, valid_to IS NOT NULL AS ended FROM edges ORDER BY id"
    ).fetchall()
    assert [(r[0], r[1]) for r in history] == [(old, 1), (new, 0)]
    with pytest.raises(KeyError):
        store.invalidate_edge(con, old)  # already ended
    assert store.invalidate_edge(con, new) is None
    assert store.traverse(con, "claim one", hops=1) == []


def test_shared_initials_between_two_full_names_is_not_merged(
    con: sqlite3.Connection,
) -> None:
    store.link(con, E("A", "paper", "authored_by", "Julius O. Smith", "author"))
    store.link(con, E("B", "paper", "authored_by", "Jane O. Smith", "author"))
    store.link(con, E("C", "paper", "authored_by", "J. O. Smith", "author"))
    plan = resolution.plan(con, embed=False)
    assert plan.sure == []  # J. O. Smith could be either: left for a person


def test_papers_and_claims_are_never_likely_candidates(
    con: sqlite3.Connection,
) -> None:
    store.link(
        con, E("Lecture 5 Neural Networks Part 4", "paper", "about", "nn", "concept")
    )
    store.link(
        con, E("Lecture 5 Neural Networks Part 2", "paper", "about", "nn", "concept")
    )
    plan = resolution.plan(con, embed=True, likely_threshold=0.5)
    assert plan.sure == [] and plan.likely == []
