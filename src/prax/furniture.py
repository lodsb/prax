"""What a captured page carries that is not the document.

A snapshot of a blog post or a video keeps more than the piece itself.
The comment section under it, which the HTML parser already files under
its own ``## Comments`` heading. And the advertising inside it: the
sponsor read in a transcript, the offer block in a video's description,
a paragraph of links with a discount code in it.

Both are regions of the artifact like any other, and both are set
aside: their own chunk (``comment``, ``ad``), never embedded, out of a
search unless asked for by kind, folded in the document view. Nothing
is removed — the artifact is what the parser wrote, and a chunk is a
region of it. Setting a region aside changes what retrieval and
extraction read, never what is kept.

The rules are deliberately narrow, because the cost of a false positive
is a passage that no longer answers a search. An advertisement has to
carry both a sponsor's mark and an offer (a link, a code, a discount),
which is what tells "this video is sponsored by Brilliant … 20 % off
with my link" from "Research sponsored by the U.S. Department of
Energy". A run extends from the offer over the pieces beside it that
name the same brand, which is where a sponsor read begins and ends.
"""

from __future__ import annotations

import re
from typing import Any

# the comment section, as the HTML parser writes it and as other
# producers title it. Nothing else: a paper's "Discussion" is the paper,
# and the caller asks for this heading only at the end of a document,
# where a page keeps what its readers wrote
_COMMENT_HEADING = re.compile(
    r"^\s*(\d+\s+)?(comments|kommentare|leserkommentare|responses|"
    r"replies|antworten)\s*(\(\d+\))?\s*$",
    re.IGNORECASE,
)
# a section that says what it is
_AD_HEADING = re.compile(
    r"^\s*(advertisement|advertising|sponsored( content| by)?|promotion|"
    r"werbung|anzeige|werbeanzeige)\b",
    re.IGNORECASE,
)
_URL = re.compile(
    r"https?://\S+|\b(?:[a-z0-9-]+\.)+"
    r"(?:com|net|org|io|co|shop|store|de|tv|me|app|ai|gg|link|xyz)\b(?:/\S*)?",
    re.IGNORECASE,
)
# somebody paid for this, in so many words
_PAID = re.compile(
    r"\b(this (?:video|episode|post|article) is sponsored|"
    r"today's sponsor|our sponsor for (?:this|today)|"
    r"for sponsoring (?:this|the|our|today's|my) (?:video|episode|podcast|post|"
    r"article|channel|stream|show)|"
    r"paid (?:promotion|partnership)|werbepartner|"
    r"mit freundlicher unterst[üu]tzung)\b",
    re.IGNORECASE,
)
# the label a German page puts over a block it was paid for: the word
# alone, never inside a sentence ("die Anzeige zeigt", "Werbung machen")
_LABEL = re.compile(
    r"^\s*\**(anzeige|werbung|sponsored|advertisement)\**\s*:?\s*$",
    re.IGNORECASE,
)
# somebody may have paid for this: enough with an offer beside it, not
# on its own — a paper is "sponsored by" a research council too
_SPONSOR = re.compile(
    r"\b(sponsored by|sponsor(?:ed)? this (?:video|episode|post)|"
    r"sponsor of (?:this|today's|the) (?:video|episode|post|article)|"
    r"brought to you by|in partnership with|"
    r"in kooperation mit|unterst[üu]tzt von)\b",
    re.IGNORECASE,
)
# and here is what you should do about it: a code, a discount, a trial
_OFFER = re.compile(
    r"\b(use (?:my|our) (?:link|code)|"
    r"(?:use|using|with) code\s+(?-i:[A-Z0-9][A-Z0-9-]{2,})\b|"  # a real code
    r"(?:promo|discount|coupon|voucher|rabatt|gutschein) ?code|"
    r"\d{1,2}\s?% (?:off|discount)|save \d{1,2}\s?%|spare \d{1,2}\s?%|"
    r"\d{1,2}\s?% (?:on|auf) (?:an?|the|their|your|die|den)\b|"
    r"free trial|kostenlos testen|"
    r"first \d+ (?:days?|people|subscribers)[^.!?]{0,20}free|"
    r"free for a full \d+ days|affiliate links?)\b",
    re.IGNORECASE,
)
# weaker: a place to go. It says nothing by itself — every article links
# somewhere — and counts only under a sponsor's mark
_CALL = re.compile(
    r"\b(check (?:it |them )?out|head (?:to|over)|go to|visit|try it|"
    r"try them|sign up (?:at|for)|get started|link in the (?:description|bio)|"
    r"schau vorbei|download it at)\b",
    re.IGNORECASE,
)
_BRAND_AFTER = re.compile(
    r"(?:sponsored by|brought to you by|in partnership with|"
    r"in kooperation mit|thanks to|work(?:ing)? (?:together )?with)\s+"
    r"(?:the\s+)?(?P<name>[A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*){0,2})",
)
_DOMAIN_LABEL = re.compile(
    r"(?:https?://)?(?:www\.)?(?P<label>[a-z0-9-]{3,})\.(?:com|net|org|io|co|"
    r"shop|store|de|tv|me|app|ai|gg|link|xyz)\b",
    re.IGNORECASE,
)
# a brand name that says nothing about who paid
_NOT_A_BRAND = frozenset(
    {"youtube", "google", "patreon", "twitter", "github", "wikipedia", "www"}
)
MAX_RUN = 8  # pieces a sponsor read may take, either side of the offer
MAX_RUN_CHARS = 4000
NEAR = 200  # characters between the offer and the link that takes it up


def is_comment_heading(title: str) -> bool:
    """Whether a heading opens the comment section."""
    return bool(_COMMENT_HEADING.match(title or ""))


def is_ad_heading(title: str) -> bool:
    """Whether a heading says the section under it was paid for."""
    return bool(_AD_HEADING.match(title or ""))


def _brand(text: str) -> str | None:
    """Whose advertisement it is: the name after "sponsored by", else the
    label of the domain it points at. Lowercase, for matching."""
    m = _BRAND_AFTER.search(text)
    if m:
        name = m.group("name").split()[0].strip(".,:;!?").lower()
        if len(name) > 2 and name not in _NOT_A_BRAND:
            return name
    for m in _DOMAIN_LABEL.finditer(text):
        label = m.group("label").lower()
        if label not in _NOT_A_BRAND:
            return label
    return None


def _near(one: re.Match[str] | None, pattern: re.Pattern[str], text: str) -> bool:
    """Whether the pattern matches close to this match. An advertisement
    is compact: the offer and the link it is taken up with sit in the
    same breath, where a paper's "free trial" and a domain name a
    thousand characters apart are two unrelated sentences."""
    if one is None:
        return False
    return any(
        min(abs(one.start() - m.end()), abs(m.start() - one.end())) <= NEAR
        for m in pattern.finditer(text)
    )


def looks_paid(text: str) -> tuple[bool, list[str]]:
    """Whether one piece of text is advertising, and what says so.

    Three ways in. It says outright that it was paid for ("today's
    sponsor", "paid promotion", a German page's "Anzeige" on a line of
    its own). Or it names a sponsor and makes an offer — a code, a
    discount, a trial — which is what tells "sponsored by Brilliant …
    20 % off with my link" from "Research sponsored by the U.S.
    Department of Energy". Or it makes that offer with a link to take
    it up, which is an advertisement whoever wrote it.
    """
    why: list[str] = []
    paid = _PAID.search(text) or (_LABEL.match(text.strip()) if text.strip() else None)
    if paid:
        return True, [f"“{paid.group(0).strip().lower()}”"]
    sponsor, offer = _SPONSOR.search(text), _OFFER.search(text)
    if sponsor:
        why.append(f"“{sponsor.group(0).lower()}”")
    if offer:
        why.append(f"“{offer.group(0).lower()}”")
    if sponsor and offer and _near(sponsor, _OFFER, text):
        return True, why
    if sponsor and _near(sponsor, _CALL, text) and _near(sponsor, _URL, text):
        return True, [*why, "a link to follow"]
    if offer and _near(offer, _URL, text):
        return True, [*why, "a link to follow"]
    return False, []


def ad_runs(pieces: list[str]) -> list[tuple[int, int, dict[str, Any]]]:
    """The runs of consecutive pieces that are advertising, as
    ``(first, last, data)`` over ``pieces`` — a document's paragraphs,
    captions and headings in order.

    A run opens where a piece carries both marks (``looks_paid``) and
    reaches over its neighbours while they name the same brand: a
    sponsor read is the stretch about the sponsor around the offer, and
    it ends at the first piece that has stopped talking about them.
    """
    runs: list[tuple[int, int, dict[str, Any]]] = []
    taken: set[int] = set()
    for i, text in enumerate(pieces):
        if i in taken:
            continue
        paid, why = looks_paid(text)
        if not paid:
            continue
        first = last = i
        size = len(text)
        if _LABEL.match(text.strip()) and i + 1 < len(pieces):
            # a label marks the block under it: "Anzeige", then the advert
            last, size = i + 1, size + len(pieces[i + 1])
        brand = _brand(text) or (_brand(pieces[last]) if last != i else None)
        if brand:
            needle = brand.lower()
            k = i - 1
            while (
                k >= 0
                and k not in taken
                and i - k <= MAX_RUN
                and size + len(pieces[k]) <= MAX_RUN_CHARS
                and needle in pieces[k].lower()
            ):
                first, size = k, size + len(pieces[k])
                k -= 1
            k = i + 1
            while (
                k < len(pieces)
                and k - i <= MAX_RUN
                and size + len(pieces[k]) <= MAX_RUN_CHARS
                and needle in pieces[k].lower()
            ):
                last, size = k, size + len(pieces[k])
                k += 1
        data: dict[str, Any] = {"why": why}
        if brand:
            data["brand"] = brand
        runs.append((first, last, data))
        taken.update(range(first, last + 1))
    return runs
