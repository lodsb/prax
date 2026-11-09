"""The facts a web page's own schema.org markup states about it (JSON-LD,
read without a model by ``prax.text.schemaorg``): its authors, its
publisher with the date, and of a recipe what it calls for and its
cuisine. Pure: the ``markup`` pass of ``prax maintain`` writes them.
Moved out of ``store.maintain`` on 2026-11-09 (structure review finding
40).
"""

from __future__ import annotations

from prax.text import dates, schemaorg

from .edges import Edge

MARKUP_PRODUCER = "jsonld"  # the producer and run of what a page's markup says

Fact = tuple[Edge, str, str | None]  # the edge, its evidence, its world date


def markup_facts(
    name: str,
    etype: str,
    page: schemaorg.Page,
    words: schemaorg.IngredientWords | None = None,
) -> list[Fact]:
    """The facts a page's schema.org markup states about the document
    ``(name, etype)``: who wrote it, who published it (dated by
    ``datePublished``), and of a recipe what it calls for and the cuisine
    it belongs to. A page with one full recipe is that recipe; a page with
    several holds each as a recipe ``part_of`` it. The ingredients are
    read with ``words``, the page's language's; without them a recipe
    calls for nothing here (the extraction still reads it)."""
    out: list[Fact] = []
    own = page.own
    single = len(page.recipes) == 1
    me = (name, "recipe" if single else etype)
    when = dates.parse(own.date) if own else None
    if own:
        kind = own.kind
        for a in own.authors:
            out.append(
                (
                    Edge(*me, "authored_by", a, "person"),
                    f"schema.org {kind} author",
                    None,
                )
            )
        for a in own.author_organizations:
            if a != own.publisher:
                out.append(
                    (
                        Edge(*me, "authored_by", a, "organization"),
                        f"schema.org {kind} author",
                        None,
                    )
                )
        if own.publisher:
            out.append(
                (
                    Edge(*me, "published_by", own.publisher, "organization"),
                    f"schema.org {kind} publisher",
                    when[0] if when else None,
                )
            )
    for r in page.recipes:
        it = me if single else (r.name, "recipe")
        if not single:
            out.append(
                (Edge(*it, "part_of", *me), "schema.org Recipe on the page", None)
            )
        if not single or not (own and own.authors):
            for a in r.authors:
                out.append(
                    (
                        Edge(*it, "authored_by", a, "person"),
                        "schema.org Recipe author",
                        None,
                    )
                )
        for i in schemaorg.ingredient_names(r.ingredient_lines, words):
            out.append(
                (
                    Edge(*it, "calls_for", i, "ingredient"),
                    "schema.org recipeIngredient",
                    None,
                )
            )
        for c in r.cuisines:
            out.append(
                (
                    Edge(*it, "belongs_to", c, "cuisine"),
                    "schema.org recipeCuisine",
                    None,
                )
            )
    return out
