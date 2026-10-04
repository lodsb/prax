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
    assert page.recipes[0].ingredients == [
        "Pflaumen",
        "Mehl",
        "Salz",
        "Pfeffer",
        "flaky salt",
    ]
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
