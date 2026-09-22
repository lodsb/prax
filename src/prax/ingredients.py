"""The ingredient list of a recipe, read as one thing.

A captured recipe writes its ingredients as a heading ("Zutaten",
"Ingredients"), a line saying for how many people, and one numbered
list per part of the dish, each under its own small heading. Chunked as
prose that is three or four fragments, none of which says what it is;
what a reader (or a model) wants is the box: for how many, and every
ingredient with its amount.

So the region from the heading to the last list is one ``ingredients``
chunk, and ``parse`` puts what it says in ``data``: the servings, the
groups, and every line as written with the amount, the unit and the
note beside it where they can be read off. The line is always kept as
it stands — the parse is an offer, not a replacement — which is what a
later pass needs to answer "the same for six people".

Unlike a comment or an advertisement this is not set aside: an
ingredient list is exactly what a search for an ingredient should find.
"""

from __future__ import annotations

import re
from typing import Any

_HEADING = re.compile(
    r"^\s*(zutaten|ingredients|einkaufsliste|shopping list|"
    r"was du brauchst|was ihr braucht|you(?:'ll)? need|you will need|"
    r"ingr[eé]dients)\b",
    re.IGNORECASE,
)
# "Für 4 Personen", "Serves 4", "Makes 12", "4 Portionen", "for 2 people"
_SERVINGS = re.compile(
    r"^\s*(?:f[üu]r\s+|for\s+|serves\s+|makes\s+|ergibt\s+|yields?\s+)?"
    r"(?P<n>\d{1,3})(?:\s*[-–]\s*\d{1,3})?\s*"
    r"(?P<what>person(?:en)?|people|portion(?:en|s)?|servings?|st[üu]ck|"
    r"gl[äa]ser|pieces?|people)\b",
    re.IGNORECASE,
)
_FRACTIONS = {
    "½": 0.5,
    "⅓": 1 / 3,
    "⅔": 2 / 3,
    "¼": 0.25,
    "¾": 0.75,
    "⅛": 0.125,
    "⅜": 0.375,
    "⅝": 0.625,
    "⅞": 0.875,
}
UNITS = (
    "g",
    "kg",
    "mg",
    "ml",
    "l",
    "cl",
    "dl",
    "TL",
    "EL",
    "Msp",
    "Prise",
    "Prisen",
    "Bund",
    "Zehe",
    "Zehen",
    "Dose",
    "Dosen",
    "Packung",
    "Packungen",
    "Scheibe",
    "Scheiben",
    "Stück",
    "Stk",
    "Becher",
    "Tasse",
    "Tassen",
    "Glas",
    "Kugel",
    "Blatt",
    "Blätter",
    "Zweig",
    "Zweige",
    "Handvoll",
    "Spritzer",
    "Tropfen",
    "tsp",
    "tbsp",
    "teaspoon",
    "teaspoons",
    "tablespoon",
    "tablespoons",
    "cup",
    "cups",
    "oz",
    "lb",
    "lbs",
    "pinch",
    "pinches",
    "clove",
    "cloves",
    "can",
    "cans",
    "pack",
    "packs",
    "sprig",
    "sprigs",
    "slice",
    "slices",
    "stick",
    "sticks",
    "bunch",
    "handful",
    "dash",
    "quart",
    "pint",
    "gallon",
)
_UNIT = re.compile(
    rf"^(?:{'|'.join(sorted(UNITS, key=len, reverse=True))})\.?$", re.IGNORECASE
)
# "1. ", "2) ", "- ", "* "
_MARKER = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])\s+")
_AMOUNT = re.compile(
    r"^(?P<num>(?:\d+[.,]?\d*|[½⅓⅔¼¾⅛⅜⅝⅞])(?:\s*[-–]\s*\d+[.,]?\d*)?"
    r"(?:\s*[½⅓⅔¼¾⅛⅜⅝⅞])?)\s*(?P<rest>.*)$"
)


def is_heading(title: str) -> bool:
    """Whether a heading opens an ingredient list."""
    return bool(_HEADING.match(title or ""))


def is_list(text: str) -> bool:
    """Whether a piece of text is part of the list: numbered or bulleted
    lines, or the line that says for how many people it is."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return False
    marked = sum(1 for ln in lines if _MARKER.match(ln))
    if marked and marked >= len(lines) - 1:  # a servings line may lead
        return True
    return len(lines) <= 2 and all(_SERVINGS.match(ln) for ln in lines)


def _number(raw: str) -> float | None:
    whole = 0.0
    seen = False
    for part in re.split(r"\s*[-–]\s*", raw)[:1]:  # a range: its lower end
        for token in re.findall(r"\d+[.,]?\d*|[½⅓⅔¼¾⅛⅜⅝⅞]", part):
            if token in _FRACTIONS:
                whole += _FRACTIONS[token]
            else:
                whole += float(token.replace(",", "."))
            seen = True
    return round(whole, 3) if seen else None


def parse_item(line: str) -> dict[str, Any]:
    """One line of the list: what it says, and the amount, unit, name and
    note where they can be read off. ``text`` is always the line as
    written, without its list marker."""
    text = _MARKER.sub("", line.strip()).strip()
    out: dict[str, Any] = {"text": text}
    rest = text
    m = _AMOUNT.match(rest)
    if m:
        amount = _number(m.group("num"))
        if amount is not None:
            out["amount"] = amount
            rest = m.group("rest").strip()
    words = rest.split()
    if words and _UNIT.match(words[0]):
        out["unit"] = words[0].rstrip(".")
        rest = " ".join(words[1:])
    note = re.search(r"\(([^)]*)\)\s*$", rest)
    if note:
        out["note"] = note.group(1).strip()
        rest = rest[: note.start()].strip()
    if rest:
        out["item"] = rest.strip(" ,;")
    return out


def parse(text: str) -> dict[str, Any]:
    """The region as a box: ``{"servings": …, "groups": [{"name", "items"}]}``.

    The first group carries no name; a small heading inside the region
    opens the next one ("Für die Soße"). ``servings`` is the line that
    says for how many, with the number where it can be read off.
    """
    servings: dict[str, Any] | None = None
    groups: list[dict[str, Any]] = [{"name": None, "items": []}]
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        head = re.match(r"^#{1,6}\s+(?P<title>.*?)\s*$", line)
        if head:
            title = head.group("title")
            if is_heading(title):
                continue  # the region's own heading
            groups.append({"name": title, "items": []})
            continue
        if servings is None and not _MARKER.match(line):
            m = _SERVINGS.match(line)
            if m:
                servings = {"text": line, "n": int(m.group("n"))}
                continue
        if _MARKER.match(line):
            groups[-1]["items"].append(parse_item(line))
    groups = [g for g in groups if g["items"]]
    out: dict[str, Any] = {"groups": groups}
    if servings:
        out["servings"] = servings
    out["count"] = sum(len(g["items"]) for g in groups)
    return out
