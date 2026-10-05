"""The fixes of the quality review of 2026-10-05
(``docs/eval/code-review-2026-10-05.md``, numbered there)."""

from __future__ import annotations

import sqlite3

from prax import store
from prax.graph import ontology, resolution
from prax.text import ingredients

E = store.Edge


def test_two_editions_are_two_things_for_every_judge(con: sqlite3.Connection) -> None:
    """Finding 1: sameness.yaml told the judge two editions were one thing,
    and the likely tier offered such pairs."""
    rule = ontology.sameness().rule()
    assert "ISMIR 2010, ISMIR 2011" not in rule.split("different")[0]
    ids = {}
    for name in ("ISMIR 2010", "ISMIR 2011", "ISMIR", "11th ISMIR Conference 2010"):
        store.link(con, E(f"paper on {name}", "paper", "published_in", name, "venue"))
        ids[name] = int(
            con.execute(
                "SELECT id FROM entities WHERE name = ? AND type = 'venue'", (name,)
            ).fetchone()[0]
        )
    store.replace_entity_candidates(
        con,
        "venue",
        [
            (ids["ISMIR 2010"], ids["ISMIR 2011"], 0.97),
            (ids["ISMIR"], ids["ISMIR 2010"], 0.95),
            (ids["ISMIR 2010"], ids["11th ISMIR Conference 2010"], 0.93),
        ],
        producer="test",
    )
    likely = resolution.plan(con, etype="venue").likely
    pairs = {frozenset((c.keep_name, c.drop_name)) for c in likely}
    assert frozenset(("ISMIR 2010", "ISMIR 2011")) not in pairs
    assert frozenset(("ISMIR", "ISMIR 2010")) not in pairs
    # two names for one edition is still a pair for the judge
    assert frozenset(("ISMIR 2010", "11th ISMIR Conference 2010")) in pairs


def test_cited_but_missing_reads_through_the_reference_index(
    con: sqlite3.Connection,
) -> None:
    """Finding 2: the kind index walked every reference chunk of the
    library; the query names migration 45's index."""
    plan = con.execute(
        "EXPLAIN QUERY PLAN SELECT doc_id, data FROM chunks"
        " INDEXED BY idx_chunks_reference WHERE kind = 'reference' AND doc_id IN (1, 2)"
    ).fetchall()
    assert "idx_chunks_reference" in " ".join(str(r[-1]) for r in plan)


def test_an_ascii_fraction_is_an_amount_everywhere() -> None:
    """Finding 10: the general parser read '1/2 cup milk' as 1 of '/2 cup'."""
    got = ingredients.parse_item("1/2 cup milk")
    assert (got["amount"], got["unit"], got["item"]) == (0.5, "cup", "milk")
    got = ingredients.parse_item("1 1/2 tbsp sugar")
    assert (got["amount"], got["unit"], got["item"]) == (1.5, "tbsp", "sugar")


def test_the_path_stamp_is_read_on_indexes(con: sqlite3.Connection) -> None:
    """Finding 12: the stamp scanned the edges table on every call."""
    from prax.store.graph import paths as part

    plan = " ".join(
        str(r[-1])
        for r in con.execute(
            "EXPLAIN QUERY PLAN SELECT (SELECT max(id) FROM edges),"
            " (SELECT max(valid_to) FROM edges WHERE valid_to IS NOT NULL)"
        ).fetchall()
    )
    assert "SCAN edges" not in plan
    assert part._stamp(con) == (None, None)


def test_a_past_moments_index_is_kept(con: sqlite3.Connection) -> None:
    """Finding 13: every as_of call built the whole index again."""
    store.link(con, E("Paper A", "paper", "cites", "Paper B", "paper"))
    first = store.path_index(con, as_of="2030-01-01")
    assert store.path_index(con, as_of="2030-01-01") is first
    store.link(con, E("Paper B", "paper", "cites", "Paper C", "paper"))
    assert store.path_index(con, as_of="2030-01-01") is not first


def test_the_hidden_set_is_read_once_a_state(con: sqlite3.Connection) -> None:
    """Finding 24: every caller in a search scanned the documents again;
    a write in between is still seen."""
    doc = int(store.ingest_text(con, "notes on a thing " * 20)["doc_id"])
    token = store.VIEWER.set(store.Viewer(name="t", personal=False))
    try:
        first = store.hidden_documents(con)
        assert store.hidden_documents(con) is first
        store.set_sensitivity(con, doc, "personal")
        assert doc in store.hidden_documents(con)
    finally:
        store.VIEWER.reset(token)


def test_the_heal_check_and_the_conflicts_pass_agree(con: sqlite3.Connection) -> None:
    """Findings 4 and 23: the heal check had its own definition of a
    conflict, which counted rule-derived edges the pass leaves out."""
    from prax.store import repair

    for venue in ("DAFx-14", "ICASSP 2014"):
        store.link(
            con, E("Paper A", "paper", "published_in", venue, "venue"), producer="t"
        )
    store.link(
        con, E("Paper B", "paper", "published_in", "NIME", "venue"), producer="t"
    )
    store.link(
        con, E("Paper B", "paper", "published_in", "SMC", "venue"), producer="rule:x"
    )
    healed = {f["subject"] for f in repair._functional_conflicts(con)}
    report = store.find_conflicts(con)["published_in"]
    assert healed == {"Paper A"} and report["open"] == 1


def test_a_pages_own_date_comes_before_what_it_links() -> None:
    """Finding 16: the dates reader walked JSON-LD on its own and took the
    first datePublished anywhere, a linked article's before the page's."""
    from prax.text import dates

    html = (
        '<script type="application/ld+json">{"@type": "ItemList", "itemListElement":'
        ' [{"@type": "ListItem", "item": {"@type": "Article",'
        ' "datePublished": "2011-01-01"}}]}</script>'
        "<script type=application/ld+json>"
        '{"@type": "NewsArticle", "headline": "x", "datePublished": "2019-07-03"}'
        "</script>"
    )
    assert dates.from_html(html)["jsonld"][0] == "2019-07-03"


def test_what_is_stale_is_one_rule() -> None:
    """Finding 18: the stale states and their relations were written in
    three places."""
    from prax.store.retrieval import fusion
    from prax.text import status

    assert set(fusion.STALE_STATES) == status.STALE
    assert fusion.STALE_RELS is status.STALE_RELS
    assert status.stale_rel("invalid") == "invalidates"
    assert status.stale_rel("retired") == "supersedes"
