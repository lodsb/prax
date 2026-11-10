"""What a web page says about itself in schema.org words (stage AN): the
reader, the dates it now finds, and the ``markup`` pass's facts."""

from __future__ import annotations

import json
import sqlite3

from prax import store
from prax.graph import ontology
from prax.text import dates, schemaorg


def _page(*objects: object, quoted: bool = False) -> str:
    attr = '"application/ld+json"' if quoted else "application/ld+json"
    scripts = "".join(f"<script type={attr}>{json.dumps(o)}</script>" for o in objects)
    return f"<html><head><title>t</title>{scripts}</head><body>x</body></html>"


SITE = {"@type": "Organization", "@id": "#publisher", "name": "Wochenmarkt Magazin"}
ARTICLE = {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "Pflaumen im Herbst",
    "datePublished": "2025-09-12T08:00:00+02:00",
    "author": [{"@type": "Person", "name": "Ada Beispiel"}],
    "publisher": {"@id": "#publisher"},
}
RECIPE = {
    "@type": "Recipe",
    "name": "Pflaumenkuchen",
    "recipeIngredient": [
        "500 g Pflaumen, entsteint",
        "200 g Mehl",
        "Salz und Pfeffer",
        "a generous pinch of flaky salt",
        "1 tbsp",
    ],
    "recipeCuisine": "German",
}


def test_an_unquoted_script_tag_is_read_for_its_date_too() -> None:
    """``type=application/ld+json`` is valid HTML and common on news sites;
    the date reader missed every such page until 2026-10-04."""
    assert dates.from_html(_page(ARTICLE))["jsonld"] == ("2025-09-12", "day")
    assert dates.from_html(_page(ARTICLE, quoted=True))["jsonld"] == (
        "2025-09-12",
        "day",
    )


def test_the_pages_own_work_and_its_names() -> None:
    page = schemaorg.of_page(_page(SITE, {"@graph": [ARTICLE]}))
    assert page is not None and page.own is not None
    assert page.own.kind == "NewsArticle" and page.own.name == "Pflaumen im Herbst"
    assert page.own.authors == ["Ada Beispiel"]
    assert page.own.publisher == "Wochenmarkt Magazin"  # through its @id
    assert page.recipes == []
    assert schemaorg.of_page(_page(SITE)) is None  # only the site


def test_a_recipe_in_an_item_list_and_its_ingredients() -> None:
    teaser = {"@type": "Recipe", "name": "Another page's recipe"}  # no ingredients
    items = {
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "item": RECIPE},
            {"@type": "ListItem", "item": teaser},
        ],
    }
    page = schemaorg.of_page(_page(ARTICLE, items))
    assert page is not None and [r.name for r in page.recipes] == ["Pflaumenkuchen"]
    section = ontology.lexicon().section("ingredients")
    lines = page.recipes[0].ingredient_lines
    de = schemaorg.ingredient_words(section["de"])
    en = schemaorg.ingredient_words(section["en"])
    assert schemaorg.ingredient_names(lines[:3], de) == [
        "Pflaumen",
        "Mehl",
        "Salz",
        "Pfeffer",
    ]
    assert schemaorg.ingredient_names(lines[3:], en) == ["flaky salt"]
    assert page.recipes[0].cuisines == ["German"]


def test_the_markup_pass_files_the_facts_once(con: sqlite3.Connection) -> None:
    html = _page(SITE, ARTICLE, {"@type": "ItemList", "itemListElement": [RECIPE]})
    doc = store.register(
        con, html.encode(), mime="text/html", title="Pflaumen im Herbst"
    )["doc_id"]
    out = store.maintain(con, only=["markup"])["markup"]
    assert out["pages"] == 1
    rows = con.execute(
        "SELECT s.name, s.type, e.rel, t.name, e.world_from FROM edges e"
        " JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
        " WHERE e.producer = 'jsonld' AND e.valid_to IS NULL"
    ).fetchall()
    facts = {(r[0], r[1], r[2], r[3]) for r in rows}
    me = ("Pflaumen im Herbst", "recipe")  # one full recipe: the page is it
    assert (*me, "authored_by", "Ada Beispiel") in facts
    assert (*me, "published_by", "Wochenmarkt Magazin") in facts
    assert (*me, "calls_for", "Pflaumen") in facts
    assert (*me, "belongs_to", "German") in facts
    assert any(r[2] == "published_by" and r[4] == "2025-09-12" for r in rows)
    stamp = store.get_meta(con, doc)["markup"]
    assert (stamp["type"], stamp["genre"]) == ("NewsArticle", "recipe")
    # read once: the stamp holds the original's hash
    assert store.maintain(con, only=["markup"])["markup"]["read"] == 0


def test_several_recipes_are_each_part_of_the_page(con: sqlite3.Connection) -> None:
    second = {**RECIPE, "name": "Zwetschgenröster", "recipeCuisine": None}
    items = {"@type": "ItemList", "itemListElement": [RECIPE, second]}
    store.register(
        con, _page(ARTICLE, items).encode(), mime="text/html", title="Herbst"
    )
    store.maintain(con, only=["markup"])
    facts = {
        (r[0], r[1], r[2])
        for r in con.execute(
            "SELECT s.name, e.rel, t.name FROM edges e JOIN entities s ON s.id = e.src"
            " JOIN entities t ON t.id = e.dst WHERE e.producer = 'jsonld'"
        )
    }
    assert ("Pflaumenkuchen", "part_of", "Herbst") in facts
    assert ("Zwetschgenröster", "calls_for", "Mehl") in facts
    assert ("Herbst", "authored_by", "Ada Beispiel") in facts


def test_a_genre_in_schema_org_words() -> None:
    g = ontology.genres()
    assert g.standard("paper") == "schema:ScholarlyArticle"
    assert g.label_for("NewsArticle") == "news" and g.label_for("BlogPosting") is None
    assert g.standard("essay") is None  # nothing near is said instead
    shown = {x["name"]: x for lv in g.as_dict()["levels"] for x in lv["genres"]}
    assert shown["recipe"]["same_as"] == "schema:Recipe"
    assert "same_as" not in shown["essay"]


def test_the_markup_pass_leaves_the_review_queue_alone(con: sqlite3.Connection) -> None:
    """The pass queues no review items, so it must not drop the
    extraction's (found by the review of 2026-10-04: 2,091 dropped on a
    copy of the library)."""
    doc = store.register(con, _page(SITE).encode(), mime="text/html", title="A page")[
        "doc_id"
    ]
    store.queue_review(
        con,
        src="A",
        src_type="paper",
        rel="about",
        dst="B",
        dst_type="nothing",
        reason="unknown type",
        source_doc=doc,
    )
    store.maintain(con, only=["markup"])
    assert store.count_review(con) == 1


def test_a_page_read_again_keeps_its_facts(con: sqlite3.Connection) -> None:
    # a re-read of the same markup ends nothing and states nothing again
    # (structure review finding 16)
    doc = store.register(
        con, _page(SITE, ARTICLE).encode(), mime="text/html", title="Pflaumen"
    )["doc_id"]
    store.maintain(con, only=["markup"])

    def live() -> set[int]:
        return {
            r[0]
            for r in con.execute(
                "SELECT id FROM edges WHERE source_doc = ? AND valid_to IS NULL",
                (doc,),
            )
        }

    first = live()
    assert len(first) == 2  # authored_by, published_by
    con.execute("UPDATE documents SET meta = json_remove(meta, '$.markup')")
    again = store.maintain(con, only=["markup"])["markup"]
    assert (again.get("ended", 0), again["kept"]) == (0, 2)
    assert live() == first


def test_facts_are_kept_ended_and_added_by_what_is_stated(
    con: sqlite3.Connection,
) -> None:
    doc = store.ingest_text(con, "A text. " * 20, title="T")["doc_id"]
    other = store.ingest_text(con, "B text. " * 20, title="U")["doc_id"]
    a = store.Edge("T", "document", "authored_by", "Ada", "person")
    b = store.Edge("T", "document", "authored_by", "Bea", "person")
    c = store.Edge("T", "document", "authored_by", "Cy", "person")
    # another reading's fact, and one of another document
    elsewhere = store.link(con, c, source_doc=other, producer="hand")
    first = store.sync_facts(
        con,
        [store.Stated(a, doc, "by Ada"), store.Stated(b, doc, "by Bea")],
        producer="p",
        run="r1",
        doc=doc,
    )
    assert (first.states, first.ended) == (["added", "added"], 0)
    ids = {
        r[0]: r[1]
        for r in con.execute(
            "SELECT evidence, id FROM edges WHERE producer = 'p' AND valid_to IS NULL"
        )
    }
    got = store.sync_facts(
        con,
        [
            store.Stated(a, doc, "by Ada"),  # the same
            store.Stated(b, doc, "Bea wrote it"),  # another quote
            store.Stated(c, doc, "by Cy"),  # live from another reading
            store.Stated(a, doc, "by Ada"),  # twice in one reading
        ],
        producer="p",
        run="r2",
        doc=doc,
        defer=True,
    )
    assert got.states == ["kept", "added", "existing", "existing"]
    assert got.ended == 1  # Bea's old quote
    live = {
        r[0]: r[1]
        for r in con.execute(
            "SELECT evidence, id FROM edges WHERE producer = 'p' AND valid_to IS NULL"
        )
    }
    assert live["by Ada"] == ids["by Ada"] and set(live) == {"by Ada", "Bea wrote it"}
    # nothing stated: the reading's facts end, nobody else's
    assert store.sync_facts(con, [], producer="p", run="r3", doc=doc).ended == 2
    assert (
        con.execute("SELECT valid_to FROM edges WHERE id = ?", (elsewhere,)).fetchone()[
            0
        ]
        is None
    )
