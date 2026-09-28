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

Not every list has a heading. The Guardian prints none, marks nothing as
a list, sets the quantities in bold and runs the group name into the
first item ("For the fruit filling**750g apples**"). So a list is also
recognised by what its lines say (``looks_like_list``): enough lines
that open with an amount, a real cooking unit among them, and the
furniture a recipe prints and a datasheet does not — prep, cook, serves,
makes. Measured over the library before it went in: 20 of the 32 kitchen
documents that had no list got one, against 2 false alarms outside the
kitchen domain in ten thousand documents
(``docs/eval/apfelkuchen-2026-09-26.md``).
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


# the headingless test: how many item lines, what share of the lines,
# and how long a paragraph may be and still be a line of a list
MIN_ITEMS = 4
MIN_SHARE = 0.4
PIECE_MAX = 300
_TIME = re.compile(
    r"^(?:min|mins|minutes?|hrs?|hours?|secs?|std|stunden?|minuten?)\b", re.IGNORECASE
)
# what a recipe prints around its list and a datasheet does not
_CUE = re.compile(
    r"\b(?:prep|cook|serves|makes|portionen|personen|zubereitung|"
    r"arbeitszeit|kochzeit|backzeit)\b",
    re.IGNORECASE,
)
_BARE_CUE = re.compile(r"^\s*(?:prep|cook|serves|makes|ergibt)\s*:?\s*$", re.IGNORECASE)
# furniture opens its line ("Prep 10 min", "Makes 12 bars"); a method
# paragraph that says "cook, swirling" is prose, and without the anchor
# it was taken for furniture and pulled into the list
_CUE_LINE = re.compile(
    r"^\s*(?:prep|cook|serves|makes|ergibt|zubereitung|arbeitszeit|"
    r"kochzeit|backzeit)\b",
    re.IGNORECASE,
)
_GROUP = re.compile(r"^\s*(?:for|f[üu]r)\s+(?:the|den|die|das)?\s*\S", re.IGNORECASE)


def lines(text: str) -> list[str]:
    """The lines of a region as a reader sees them. A bold span that opens
    on a number starts a line of its own, because a page that sets its
    quantities in bold often runs them into the words before."""
    text = re.sub(r"\*\*(?=\d|[½⅓⅔¼¾])", "\n", text)
    text = text.replace("**", "")
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def item_of(line: str) -> dict[str, Any] | None:
    """A line read as an ingredient, or None: an amount and something
    after it that is not a length of time ("10 min" is the prep, not an
    ingredient)."""
    if len(line) > 160:
        return None
    it = parse_item(line)
    if "amount" not in it or not it.get("item") or _TIME.match(it["item"]):
        return None
    return it


def looks_like_list(text: str) -> bool:
    """Whether a run of text is an ingredient list without a heading.

    Two ways in. Enough lines with an amount *and* a cooking unit say so
    on their own. Countable ingredients ("4 courgettes") have no unit, so
    a looser count is allowed as well — but only beside the furniture a
    recipe prints, because a manual's numbered specifications look just
    like a count of things: without the cue the loose test fired in seven
    more documents outside the kitchen, a DSP textbook and a loudspeaker
    manual among them.
    """
    found = lines(text)
    if not found:
        return False
    items = [it for it in (item_of(ln) for ln in found) if it]
    units = sum(1 for it in items if "unit" in it)
    share = len(items) / len(found)
    if units >= MIN_ITEMS and units / len(found) >= MIN_SHARE:
        return True
    return (
        units >= 2
        and len(items) >= MIN_ITEMS
        and share >= MIN_SHARE
        and bool(_CUE.search(text))
    )


def is_piece(text: str) -> bool:
    """Whether a short paragraph belongs at the edge of a headingless
    list: it holds an ingredient, or it is the recipe's furniture."""
    if len(text) > PIECE_MAX:
        return False
    found = lines(text)
    return any(
        _CUE_LINE.match(ln) or _SERVINGS.match(ln) or item_of(ln) for ln in found
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
    if not any(_MARKER.match(ln) for ln in text.splitlines()):
        return _parse_unmarked(text)
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


def _parse_unmarked(text: str) -> dict[str, Any]:
    """A list with no markers: every line that reads as an ingredient is
    one, "For the filling" opens a group, "Makes" and "Serves" say for how
    many. A line printed twice within a group is one item: a capture that
    kept both the bold and the plain copy repeats lines, and not always
    next to each other."""
    servings: dict[str, Any] | None = None
    groups: list[dict[str, Any]] = [{"name": None, "items": []}]
    found = lines(text)
    skip: set[int] = set()
    for i, line in enumerate(found):
        if i in skip:
            continue
        if _BARE_CUE.match(line) and i + 1 < len(found):
            skip.add(i + 1)  # its value is the next line, not an ingredient
            said = f"{line.strip()} {found[i + 1]}"
            if servings is None and re.match(
                r"\s*(serves|makes|ergibt)", line, re.IGNORECASE
            ):
                n = re.search(r"\d{1,3}", found[i + 1])
                servings = {"text": said, "n": int(n.group()) if n else None}
            continue
        if servings is None:
            m = _SERVINGS.match(line)
            if m:
                servings = {"text": line, "n": int(m.group("n"))}
                continue
        it = item_of(line)
        if it:
            here = groups[-1]["items"]
            if all(x["text"] != it["text"] for x in here):
                here.append(it)
            continue
        if _GROUP.match(line) and len(line) <= 60:
            groups.append({"name": line.rstrip(" :"), "items": []})
    groups = [g for g in groups if g["items"]]
    out: dict[str, Any] = {"groups": groups}
    if servings:
        out["servings"] = servings
    out["count"] = sum(len(g["items"]) for g in groups)
    return out
