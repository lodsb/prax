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


# ------------------------------------------- step 3: words in data, generality


def test_a_pack_carries_lexicon_sections_and_none_is_declared_twice() -> None:
    """Findings 6, 8, 9, 15: the venue and ingredient words are data in
    their packs; a section two files hold is refused."""
    import pytest
    import yaml

    from prax import packs

    lex = ontology.lexicon()
    assert "conference" in lex.section("venues")["venue_words"]
    assert "Zwetschgen" not in lex.section("ingredients")["de"]["trailing"]
    with pytest.raises(ValueError, match="declared twice"):
        ontology.parse_lexicon("venues: {stop: [a]}\n", ["venues: {stop: [b]}\n"])

    # a bare YAML word read as a boolean ("on", "no") is no word at all
    def leaves(node: object) -> list[object]:
        if isinstance(node, dict):
            return [x for v in node.values() for x in leaves(v)]
        if isinstance(node, list):
            return [x for v in node for x in leaves(v)]
        return [node]

    for f in [*packs.lexicon_files(), ontology.path() / "lexicon.yaml"]:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert not [x for x in leaves(data) if isinstance(x, bool)], f


def test_venue_names_in_other_languages_read_their_editions() -> None:
    """Finding 6: a French or Spanish edition stayed in the series."""
    from prax.graph import venues

    fr = venues.read("12e Congrès Français d'Acoustique 2014")
    es = venues.read("Actas del 3er Congreso Iberoamericano de Acústica")
    assert (fr.series, fr.ordinal) == ("congres francais acoustique", 12)
    assert (es.series, es.edition) == ("congreso iberoamericano acustica", "#3")
    # the core lexicon's company forms are no acronyms
    assert "GMBH" in venues.words().not_acronym
    assert venues.not_a_venue("Tagungsband zur Jahrestagung für Akustik") is None


def test_a_sentence_is_no_status_and_german_states_are_read() -> None:
    """Finding 7: a note opening with 'Archived copies…' was retired."""
    from prax.text import status

    for prose in (
        "# Notes\n\nArchived copies of the datasets live on the NAS.",
        "Wrong turns we took are listed below.",
        "Replaced the ADC in rev B.",
    ):
        assert status.read(prose, "notes.md") is None, prose
    for text, state in (
        ("Veraltet: siehe plan-v2.md", "superseded"),
        ("---\nstatus: veraltet\n---\n# Plan", "superseded"),
        ("**Deprecated** since 2026-09", "deprecated"),
        ("Superseded by [the plan](plan-v2.md)", "superseded"),
        ("> RETIRED 2026-10-02", "retired"),
        ("Retired.", "retired"),
    ):
        got = status.read(text, "notes.md")
        assert got is not None and got.state == state, text


def test_a_recipe_in_a_language_without_words_calls_for_nothing(
    con: sqlite3.Connection,
) -> None:
    """Finding 8: '200 g de farine, tamisée' became the ingredient 'de
    farine'."""
    import json

    from prax.text import schemaorg

    section = ontology.lexicon().section("ingredients")
    assert schemaorg.ingredient_words(section.get("fr")) is None
    assert schemaorg.ingredient_names(["200 g de farine, tamisée"], None) == []
    recipe = {
        "@type": "Recipe",
        "name": "Gâteau",
        "recipeIngredient": ["200 g de farine, tamisée", "3 œufs battus"],
    }
    html = f"<script type=application/ld+json>{json.dumps(recipe)}</script>"
    doc = int(
        store.register(con, html.encode(), mime="text/html", title="Gâteau")["doc_id"]
    )
    con.execute(
        "UPDATE documents SET meta = json_set(COALESCE(meta, '{}'), '$.lang', 'fr')"
        " WHERE id = ?",
        (doc,),
    )
    con.commit()
    store.maintain(con, only=["markup"])
    rels = {
        r[0]
        for r in con.execute(
            "SELECT rel FROM edges WHERE producer = 'jsonld' AND source_doc = ?", (doc,)
        )
    }
    assert "calls_for" not in rels


def test_a_page_links_a_document_as_the_node_it_is(con: sqlite3.Connection) -> None:
    """Finding 11: a page annotating a recipe made a second, 'paper'
    entity of it."""
    doc = int(
        store.ingest_text(con, "flour and plums " * 20, title="Plum cake")["doc_id"]
    )
    store.link(con, E("Plum cake", "recipe", "calls_for", "plum", "ingredient"))
    store.write_page(con, "note-cake", "# Cake\n\nA note.", annotates=[doc])
    types = {
        r[0] for r in con.execute("SELECT type FROM entities WHERE name = 'Plum cake'")
    }
    assert types == {"recipe"}
    # a research document with no entity yet is the paper its extraction
    # will name it
    paper = int(store.ingest_text(con, "a study " * 30, title="A study")["doc_id"])
    store.set_domains(con, paper, ["research"])
    assert store.document_node(con, paper) == ("A study", "paper")


def test_a_relations_strength_is_its_modules(con: sqlite3.Connection) -> None:
    """Findings 19, 21: hop strength was a list of names in paths.py."""
    from prax.graph import paths

    onto = ontology.current()
    assert onto.relations["cites"].strength == "strong"
    assert onto.relations["mentions"].strength == "weak"
    assert "evaluates" not in paths.relation_strengths()
    bad = ontology.Relation("x", strength="loud")
    assert any("strength" in m for m in ontology.lint({}, {"x": bad}))
    assert (
        paths.hop_cost("cites", "EXTRACTED", 1)
        < paths.hop_cost("about", "EXTRACTED", 1)
        < paths.hop_cost("mentions", "EXTRACTED", 1)
    )


def test_a_genres_schema_type_is_a_pages_own_work() -> None:
    """Finding 22: a Q&A page, a thesis or source code gave no author."""
    import json

    from prax.text import schemaorg

    html = (
        "<script type=application/ld+json>"
        + json.dumps(
            {"@type": "QAPage", "author": {"@type": "Person", "name": "Ann Bee"}}
        )
        + "</script>"
    )
    assert schemaorg.of_page(html) is None
    page = schemaorg.of_page(html, ontology.genres().standards())
    assert page is not None and page.own is not None
    assert page.own.authors == ["Ann Bee"]
    assert {"Thesis", "SoftwareSourceCode", "QAPage"} <= set(
        ontology.genres().standards()
    )
