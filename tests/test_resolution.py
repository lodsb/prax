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
    plan = resolution.plan(con, likely=False)
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
    assert resolution.plan(con, likely=False).sure == []


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
    plan = resolution.plan(con, likely=False)
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
    assert resolution.plan(con, likely=False).twins == []


def _likely_computed(con: sqlite3.Connection, etype: str, threshold: float) -> int:
    """What a worker's resolve step does for one type, without the door:
    embed the names, find the close pairs, leave them in the store."""
    emb = embeddings.current()
    assert emb is not None
    pairs = resolution.likely_pairs(
        store.entity_names(con, etype), emb, threshold=threshold
    )
    return store.replace_entity_candidates(con, etype, pairs, producer="test")


def test_likely_tier_needs_an_adjudicator(con: sqlite3.Connection) -> None:
    store.link(con, E("P", "paper", "about", "granular synthesis", "concept"))
    store.link(con, E("Q", "paper", "about", "granular synthesis method", "concept"))
    store.link(con, E("R", "paper", "about", "room acoustics", "concept"))
    # nothing likely until a worker has computed the pairs
    assert resolution.plan(con).likely == []
    # the hash embedder scores shared words; 0.8 stands in for bge's 0.92
    assert _likely_computed(con, "concept", 0.8) >= 1
    plan = resolution.plan(con)
    assert plan.sure == []
    assert [c.drop_name for c in plan.likely] and all(
        c.type == "concept" for c in plan.likely
    )
    assert resolution.apply(con, plan).declined == len(plan.likely)  # default: no
    accept_all = resolution.StubAdjudicator(threshold=0.0)
    report = resolution.apply(con, plan, adjudicator=accept_all)
    assert report.merged_likely >= 1
    # merged: the pair is moot and leaves the plan without being recomputed
    assert resolution.plan(con).likely == []


def test_a_decline_is_recorded_and_survives_the_next_computation(
    con: sqlite3.Connection,
) -> None:
    """The adjudicator's no cost money: the pair is marked decided, leaves
    the plan, and the next week's computation of its type keeps the
    mark instead of asking again. The default adjudicator's no is only
    'nobody asked' and marks nothing."""
    store.link(con, E("P", "paper", "about", "spatial audio", "concept"))
    store.link(con, E("Q", "paper", "about", "spatial audio coding", "concept"))
    store.link(con, E("R", "paper", "about", "musical sound synthesis", "concept"))
    store.link(con, E("S", "paper", "about", "musical sound", "concept"))
    assert _likely_computed(con, "concept", 0.7) >= 2
    plan = resolution.plan(con)
    assert len(plan.likely) >= 2
    resolution.apply(con, plan)  # nobody asked: nothing recorded
    assert len(resolution.plan(con).likely) == len(plan.likely)

    class Judge:  # says yes to the plural, no to the coding
        name = "judge"

        def decide(self, cands):
            return ["musical" in c.keep_name for c in cands]

    report = resolution.apply(con, plan, adjudicator=Judge())
    assert report.merged_likely == 1 and report.declined >= 1
    assert resolution.plan(con).likely == []  # decided either way
    decided = con.execute(
        "SELECT decided, decided_by FROM entity_candidates WHERE decided IS NOT NULL"
    ).fetchall()
    assert decided and all(r["decided_by"] == "judge" for r in decided)
    # the type computed again: the decided pair keeps its mark
    assert _likely_computed(con, "concept", 0.7) >= 1
    assert resolution.plan(con).likely == []
    assert con.execute(
        "SELECT count(*) FROM entity_candidates WHERE decided = 'different'"
    ).fetchone()[0] == len(decided)


def test_likely_pairs_are_the_close_names_a_block_at_a_time() -> None:
    """The worker's computation: every pair at or above the threshold,
    the smaller id first, highest first, the same whatever the block —
    thirty thousand names must not want the square."""
    emb = embeddings.current()
    assert emb is not None
    names = [
        (10, "granular synthesis"),
        (7, "granular synthesis method"),
        (3, "room acoustics"),
        (12, "room acoustic"),
        (5, "wave digital filters"),
    ]
    whole = resolution.likely_pairs(names, emb, threshold=0.8)
    blocked = resolution.likely_pairs(names, emb, threshold=0.8, block=2)
    assert whole == blocked and whole
    assert all(a < b for a, b, _ in whole)
    assert [s for _, _, s in whole] == sorted((s for _, _, s in whole), reverse=True)
    assert (7, 10) in {(a, b) for a, b, _ in whole}
    assert (5, 10) not in {(a, b) for a, b, _ in whole}
    assert resolution.likely_pairs(names[:1], emb) == []


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
    plan = resolution.plan(con, likely=False)
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
    # even computed, a paper is never a likely candidate: the type is refused
    emb = embeddings.current()
    pairs = resolution.likely_pairs(
        store.entity_names(con, "paper"), emb, threshold=0.5
    )
    assert pairs  # the names are close
    store.replace_entity_candidates(con, "paper", pairs, producer="test")
    plan = resolution.plan(con)
    assert plan.sure == [] and plan.likely == []


def test_a_person_who_is_an_author_is_one_entity(con: sqlite3.Connection) -> None:
    """One name under a type and its subtype: the ontology says an author
    is a person and a paper is a document, so the general side is the
    extractor reaching for the safe word. The specific side survives."""
    doc = store.ingest_text(con, "a paper about reverb " * 30, title="P")["doc_id"]
    for src, st, rel, dst, dt in (
        ("A Study of Reverb", "paper", "authored_by", "Ann Author", "author"),
        ("A Study of Reverb", "document", "mentions", "Ann Author", "person"),
    ):
        store.link(
            con,
            store.Edge(src, st, rel, dst, dt),
            source_doc=doc,
            producer="test",
            run="r1",
        )
    plan = resolution.plan(con, likely=False)
    folds = {(c.drop_name, c.keep_name, c.type) for c in plan.subtypes}
    assert ("Ann Author", "Ann Author", "person→author") in folds
    assert ("A Study of Reverb", "A Study of Reverb", "document→paper") in folds
    assert not plan.sure  # the names are equal, the types are not
    report = resolution.apply(con, plan, subtypes=True)
    assert report.merged_subtypes == 2
    # and the graph now has one of each, under the type that says more
    names = {
        (r["name"], r["type"])
        for r in con.execute(
            "SELECT name, type FROM entities WHERE canonical_id IS NULL"
        )
    }
    assert ("Ann Author", "author") in names and ("Ann Author", "person") not in names
    assert ("A Study of Reverb", "paper") in names
    assert resolution.plan(con, likely=False).subtypes == []  # idempotent


def test_a_page_of_my_own_is_never_folded_into_a_paper(
    con: sqlite3.Connection,
) -> None:
    """`page` and `project` are the store's kinds, not the extractor's
    guess, so a note that shares a title with a paper stays itself."""
    doc = store.ingest_text(con, "reverb notes " * 40, title="Reverb notes")["doc_id"]
    store.link(
        con,
        store.Edge("Reverb notes", "page", "annotates", "A Study", "paper"),
        source_doc=doc,
        producer="test",
        run="r1",
    )
    store.link(
        con,
        store.Edge("Reverb notes", "document", "mentions", "reverb", "concept"),
        source_doc=doc,
        producer="test",
        run="r1",
    )
    assert resolution.plan(con, likely=False).subtypes == []


def test_a_merge_leaves_a_label_and_can_be_undone(con: sqlite3.Connection) -> None:
    """A merge is a claim like an edge: it says who decided and in which
    pass, the folded name stays as a label with the language it is in,
    and a round of merging can be taken back whole (docs/normalization.md
    — until migration 20 neither was true)."""
    doc = store.ingest_text(con, "a paper about reverb " * 30, title="P")["doc_id"]
    for name, etype in (("Olivenöl", "ingredient"), ("olive oil", "ingredient")):
        store.link(
            con,
            store.Edge("A Recipe", "recipe", "calls_for", name, etype),
            source_doc=doc,
            producer="test",
            run="r1",
        )
    ids = {
        r["name"]: r["id"]
        for r in con.execute("SELECT id, name FROM entities WHERE type = 'ingredient'")
    }
    store.merge_entities(
        con,
        ids["Olivenöl"],
        ids["olive oil"],
        producer="resolution",
        run="resolve-test",
        confidence="INFERRED",
    )
    labels = store.entity_labels(con, ids["olive oil"])
    assert [x["label"] for x in labels] == ["Olivenöl"]
    got = labels[0]
    assert got["lang"] is None or got["lang"] == "de"  # a word alone may not say
    assert got["producer"] == "resolution" and got["run"] == "resolve-test"
    assert got["from_entity"] == ids["Olivenöl"]
    # the German name now reaches the English entity
    assert store.entities_by_label(con, "Olivenöl") == [ids["olive oil"]]

    # and the round can be taken back
    assert store.unmerge_run(con, "resolve-test") == 1
    back = con.execute(
        "SELECT canonical_id FROM entities WHERE id = ?", (ids["Olivenöl"],)
    ).fetchone()
    assert back[0] is None
    assert store.entity_labels(con, ids["olive oil"]) == []
    assert store.unmerge_run(con, "resolve-test") == 0  # idempotent


def test_a_label_can_be_added_by_hand(con: sqlite3.Connection) -> None:
    """What a dictionary import or a person writes: a name in a language,
    with who said so."""
    doc = store.ingest_text(con, "a paper about reverb " * 30, title="P")["doc_id"]
    store.link(
        con,
        store.Edge("A Recipe", "recipe", "calls_for", "celeriac", "ingredient"),
        source_doc=doc,
        producer="test",
        run="r1",
    )
    entity = con.execute("SELECT id FROM entities WHERE name = 'celeriac'").fetchone()[
        0
    ]
    assert (
        store.add_label(
            con,
            entity,
            "Knollensellerie",
            lang="de",
            kind="pref",
            producer="dictionary:kitchen",
            confidence="EXTRACTED",
        )
        == 1
    )
    assert store.add_label(con, entity, "Knollensellerie", lang="de") == 0  # once
    german = store.entity_labels(con, entity, lang="de")
    assert [x["label"] for x in german] == ["Knollensellerie"]
    assert german[0]["kind"] == "pref"
    assert store.entities_by_label(con, "knollensellerie") == [entity]
