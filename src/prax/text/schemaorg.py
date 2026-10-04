"""What a web page says about itself in schema.org words (stage AN).

Many pages carry their own description as JSON-LD: a recipe's
ingredients, cuisine and yield, an article's authors, publisher and date.
This reads it without a model. ``nodes`` finds every JSON-LD object on a
page; ``own`` picks the one that is the page's own work (the recipe, the
article, the post), not the site, its logo or a breadcrumb; ``said``
reduces it to what prax can file: names, never markup.

The script tag is matched with its attribute quoted or not
(``type=application/ld+json`` is valid HTML and common on news sites).
Microdata (``itemprop``) is not read: 24 of the library's 559 pages carry
it, 328 carry JSON-LD (2026-10-04).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from prax.text import ingredients

JSONLD = re.compile(
    r"""<script[^>]+type\s*=\s*["']?application/ld\+json["']?[^>]*>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)

# the page's own work, the most telling first: a page that is a recipe and
# an article is the recipe
OWN_TYPES = (
    "Recipe",
    "ScholarlyArticle",
    "TechArticle",
    "NewsArticle",
    "Report",
    "BlogPosting",
    "Review",
    "Article",
    "HowTo",
    "SocialMediaPosting",
    "DiscussionForumPosting",
    "VideoObject",
    "Book",
    "Product",
)
NAME_CHARS = 120  # a name past this is a sentence, not a name
# what a line says of the preparation, at its end: "garlic cloves, peeled",
# "Black pepper to taste", "Zwetschgen entsteint"
_PREP = re.compile(
    r"(?:\s+(?:finely|roughly|thinly|lightly|freshly|to|for|zum|nach|"
    r"peeled|sliced|chopped|grated|picked|topped|beaten|removed|diced|"
    r"crushed|halved|trimmed|drained|softened|melted|washed|torn|shredded|"
    r"rinsed|soaked|cubed|toasted|"
    r"deep-frying|frying|serving|serve|taste|garnish|optional|"
    r"gehackt|gewürfelt|geschält|gerieben|entsteint|halbiert|geröstet|"
    r"gehobelt|abgerieben|zerdrückt|fein|grob|"
    r"servieren|bestreuen|geschmack|belieben))+$",
    re.IGNORECASE,
)


@dataclass
class Said:
    """What a page's own schema.org object says, as names."""

    kind: str  # its schema.org type: Recipe, NewsArticle…
    name: str = ""
    authors: list[str] = field(default_factory=list)  # people
    author_organizations: list[str] = field(default_factory=list)
    publisher: str = ""
    date: str = ""  # datePublished as written
    ingredients: list[str] = field(default_factory=list)
    cuisines: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


def _types(node: dict[str, Any]) -> list[str]:
    raw = node.get("@type")
    out = raw if isinstance(raw, list) else [raw]
    # "http://schema.org/Recipe" is Recipe
    return [str(t).rsplit("/", 1)[-1] for t in out if t]


def nodes(html: str | bytes) -> list[dict[str, Any]]:
    """Every JSON-LD object on the page, in the order written, a
    ``@graph`` opened into its members. A block that does not parse is
    passed over."""
    text = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            if "@graph" in node:
                walk(node["@graph"])
            if node.get("@type"):
                out.append(node)

    for block in JSONLD.findall(text):
        body = block.strip()
        if body.startswith("<!--"):
            body = body.removeprefix("<!--").removesuffix("-->").strip()
        try:
            walk(json.loads(body))
        except ValueError:
            continue
    return out


def own(found: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The page's own work among its objects: the first of the most
    telling type (``OWN_TYPES``); a ``WebPage``'s ``mainEntity`` when it
    is one; None for a page that only describes its site."""
    candidates = list(found)
    for node in found:
        main = node.get("mainEntity")
        if isinstance(main, dict) and main.get("@type"):
            candidates.append(main)
    for wanted in OWN_TYPES:
        for node in candidates:
            if wanted in _types(node):
                return node
    return None


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(re.sub(r"<[^>]+>", " ", value).split())
    return text if 1 < len(text) <= NAME_CHARS else ""


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _words(value: Any) -> list[str]:
    """A list of names, or one comma-separated string of them."""
    out: list[str] = []
    for v in _list(value):
        if isinstance(v, str):
            out += [w for w in (_clean(p) for p in v.split(",")) if w]
        elif isinstance(v, dict):
            name = _clean(v.get("name"))
            if name:
                out.append(name)
    return list(dict.fromkeys(out))


# a measure written as words, at the start: "a generous pinch of", "1 handful"
_MEASURE = re.compile(
    r"^(?:(?:a|an|some|very|generous|large|small|good|big|eine?|etwas)\s+)*"
    r"(?:pinch|handful|splash|dash|knob|bunch|sprig|glug|squeeze|"
    r"prise|handvoll|schuss|bund|zweig|kopf|köpfe|knolle|stange|zehe|scheibe|"
    r"dose|becher|päckchen|tasse|stück)(?:e?s|e?n)?\s+(?:of\s+)?",
    re.IGNORECASE,
)


# and at the start: "finely chopped ginger", "etwas Speiseöl"; "minced pork"
# keeps its word, which names the ingredient
_LEADING = re.compile(
    r"^(?:(?:finely|roughly|thinly|freshly|coarsely|etwas|frisch|fein)\s+)+"
    r"(?:(?:chopped|grated|sliced|ground|gehackte?r?|geriebene?r?)\s+)?",
    re.IGNORECASE,
)


_FRACTION = re.compile(r"^\s*(?:\d+\s+)?\d+\s*/\s*\d+\s*")


def ingredient_name(line: str) -> list[str]:
    """The ingredients one line of ``recipeIngredient`` names, without
    amount, unit, note or preparation: "500 g Zwetschgen, entsteint" is
    Zwetschgen; "Salz und Pfeffer" is two."""
    # an amount written as an ASCII fraction ("1/2 cup", "1 1/2 tbsp") goes
    # first, so the unit after it is seen: else "/2 cup milk" was a name
    line = _FRACTION.sub("", line)
    item = (ingredients.parse_item(line).get("item") or "").split(",")[0]
    item = re.sub(r"\([^)]*\)", " ", item)
    parts = re.split(r"\s+(?:und|and|oder|or|&)\s+", item)
    out = []
    for p in parts:
        # "– that is…", "plus extra for greasing"
        name = " ".join(re.split(r"\s(?:[–—-]|plus|sowie)\s", p)[0].split())
        name = _MEASURE.sub("", name)
        name = _PREP.sub("", " " + _LEADING.sub("", name)).strip(" .;:")
        # "1 tbsp" alone, a unit left without its ingredient
        if len(name) > 1 and not name[0].isdigit():
            out.append(name)
    return out[:2]


def said(node: dict[str, Any], by_id: dict[str, dict[str, Any]] | None = None) -> Said:
    """What one object says, as names. ``by_id`` resolves a reference
    (``{"@id": "#publisher"}``) to the object it names on the same page."""
    by_id = by_id or {}

    def resolve(value: Any) -> Any:
        if isinstance(value, dict) and set(value) <= {"@id"} and value.get("@id"):
            return by_id.get(str(value["@id"]), value)
        return value

    out = Said(kind=(_types(node) or [""])[0])
    out.name = _clean(node.get("name")) or _clean(node.get("headline"))
    for a in _list(node.get("author")):
        a = resolve(a)
        if isinstance(a, str):
            name, kinds = _clean(a), ["Person"]
        elif isinstance(a, dict):
            name, kinds = _clean(a.get("name")), _types(a) or ["Person"]
        else:
            continue
        if not name:
            continue
        if "Person" in kinds:
            out.authors.append(name)
        else:
            out.author_organizations.append(name)
    publisher = resolve(node.get("publisher"))
    if isinstance(publisher, list):
        publisher = resolve(publisher[0]) if publisher else None
    if isinstance(publisher, dict):
        out.publisher = _clean(publisher.get("name"))
    elif isinstance(publisher, str):
        out.publisher = _clean(publisher)
    out.date = str(node.get("datePublished") or "")[:32]
    for line in _list(node.get("recipeIngredient") or node.get("ingredients")):
        if isinstance(line, str):
            out.ingredients += ingredient_name(line)
    out.ingredients = list(dict.fromkeys(out.ingredients))
    out.cuisines = _words(node.get("recipeCuisine"))
    out.categories = _words(node.get("recipeCategory") or node.get("articleSection"))
    out.keywords = _words(node.get("keywords"))
    out.authors = list(dict.fromkeys(out.authors))
    return out


@dataclass
class Page:
    """What a page says of itself: its own work, and the full recipes it
    holds (with their ingredients; a teaser linking another page's recipe
    has none), which a news site puts in an ``ItemList`` beside its
    ``Article``."""

    own: Said | None
    recipes: list[Said] = field(default_factory=list)


def _recipes(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        if "Recipe" in _types(node) and node.get("recipeIngredient"):
            out.append(node)
            return
        for child in node.values():
            _recipes(child, out)
    elif isinstance(node, list):
        for child in node:
            _recipes(child, out)


def of_page(html: str | bytes) -> Page | None:
    """What the page says of itself, or None when it says nothing prax
    files."""
    found = nodes(html)
    by_id = {str(n["@id"]): n for n in found if isinstance(n.get("@id"), str)}
    node = own(found)
    full: list[dict[str, Any]] = []
    _recipes(found, full)
    if node is None and not full:
        return None
    recipes = [said(r, by_id) for r in full]
    unique = list({r.name: r for r in recipes if r.name}.values())
    return Page(said(node, by_id) if node is not None else None, unique)
