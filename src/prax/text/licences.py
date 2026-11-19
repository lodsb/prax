"""Under what licence a document is offered: an SPDX id and where it was read.

A reader downstream of the library needs to know what it may do with a
document and with the facts read from it (AV, the BWS survey of
2026-11-17). Three places say it. A record names a licence by its URL or
its name: Crossref's ``license``, OpenAlex's, arXiv's OAI ``<license>``, a
Zotero item's ``rights``, a repository's licence on GitHub. The document
itself may print a line about itself: "This article is licensed under a
Creative Commons Attribution 4.0 International License", "Licensed under a
Creative Commons Attribution 4.0 International License (CC BY 4.0)", a
wiki's "Text is available under the Creative Commons
Attribution-ShareAlike License 4.0". A person's word
(``store.set_licence``) outranks both.

``from_url`` reads a URL, ``from_name`` a licence's name or its short form
(``CC BY-NC 4.0``), and ``printed`` finds the line a text prints about
itself: in its head or its tail only, and only in a sentence that speaks of
the document (``this article``, ``open access``, ``licensed under`` at its
start, a copyright sign), so a paper that releases its dataset under CC BY
is not CC BY itself.

The ids are SPDX's where SPDX has one (``CC-BY-4.0``, ``CC-BY-NC-SA-3.0``,
``CC0-1.0``). A Creative Commons licence printed without its version is
its family (``CC-BY``). arXiv's own is ``LicenseRef-arXiv-nonexclusive``,
and a publisher's terms named by a URL that is no licence prax knows are
``LicenseRef-publisher`` with the URL kept: a licence, but not an open one.

Imports nothing of prax (the ``text`` package's rule).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ARXIV = "LicenseRef-arXiv-nonexclusive"
PUBLISHER = "LicenseRef-publisher"
PUBLIC_DOMAIN = "LicenseRef-public-domain"

# how much of a text a licence line is looked for in: its first pages and
# its last; a line about the document stands on its first page or at its
# end (a journal's footer, a wiki's), never in the middle of the argument
HEAD_CHARS = 8_000
TAIL_CHARS = 4_000


@dataclass(frozen=True)
class Licence:
    """A licence: its id, the URL that names it when one does, and the
    words that state it when they were read from a text."""

    id: str
    url: str | None = None
    words: str | None = None

    @property
    def open(self) -> bool:
        """Whether anyone may reuse the work, commercially too, and change
        it: CC BY, CC BY-SA, CC0, the public domain."""
        return is_open(self.id)


def is_open(licence_id: str) -> bool:
    """``open`` of ``Licence``, for an id alone."""
    family = family_of(licence_id)
    return family in ("CC-BY", "CC-BY-SA", "CC0") or licence_id == PUBLIC_DOMAIN


def family_of(licence_id: str) -> str:
    """The id without its version: ``CC-BY-NC-4.0`` is ``CC-BY-NC``."""
    return re.sub(r"-\d+(?:\.\d+)*$", "", licence_id)


def matches(licence_id: str, wanted: str) -> bool:
    """Whether a licence is what a filter asks for: ``open`` (``is_open``),
    ``cc`` (any Creative Commons licence, CC0 too), ``any`` (any licence at
    all), a family (``CC-BY``: every version of CC BY, not CC BY-NC) or an
    id (``CC-BY-4.0``), in any case."""
    want = wanted.strip()
    low = want.lower()
    if low == "any":
        return True
    if low == "open":
        return is_open(licence_id)
    if low == "cc":
        return licence_id.upper().startswith(("CC-", "CC0"))
    have = licence_id.upper()
    return have == want.upper() or family_of(have) == want.upper()


_CC_URL = re.compile(
    r"creativecommons\.org/\s*(?:licen[cs]es/\s*(?P<kind>[a-z-]+)"
    r"(?:/\s*(?P<v>\d\.\d))?"
    r"|publicdomain/(?P<pd>zero|mark)(?:/(?P<pv>\d\.\d))?)",
    re.IGNORECASE,
)
_ARXIV_URL = re.compile(r"arxiv\.org/licenses/nonexclusive-distrib", re.IGNORECASE)
# the parts of a CC licence, in the order its id writes them
_PARTS = {"by": "BY", "nc": "NC", "sa": "SA", "nd": "ND"}


def _cc(parts: list[str], version: str | None) -> str | None:
    """``CC-BY-NC-SA-4.0`` from its parts in any order and its version."""
    got = {p for p in (x.lower() for x in parts) if p in _PARTS}
    if "by" not in got or ("sa" in got and "nd" in got):
        return None
    ordered = [_PARTS[p] for p in ("by", "nc", "sa", "nd") if p in got]
    return "CC-" + "-".join(ordered) + (f"-{version}" if version else "")


def deed(licence_id: str) -> str | None:
    """The Creative Commons page of a CC licence with its version
    (``CC-BY-NC-4.0`` is ``https://creativecommons.org/licenses/by-nc/4.0/``):
    the one URL for it, whatever form the record or the text wrote. None
    for any other id."""
    if m := re.fullmatch(r"CC0-(\d\.\d)", licence_id):
        return f"https://creativecommons.org/publicdomain/zero/{m.group(1)}/"
    if m := re.fullmatch(
        r"CC-((?:BY|NC|SA|ND)(?:-(?:BY|NC|SA|ND))*)-(\d\.\d)", licence_id
    ):
        return (
            f"https://creativecommons.org/licenses/{m.group(1).lower()}/{m.group(2)}/"
        )
    return None


def from_url(url: str | None) -> Licence | None:
    """The licence a URL names: a Creative Commons deed or legal code,
    the CC0 or public-domain mark, arXiv's licence; any other http URL is
    a publisher's own terms (``PUBLISHER``). None for no URL."""
    if not url or not (u := url.strip()):
        return None
    if m := _CC_URL.search(u):
        if m.group("pd"):
            if m.group("pd").lower() == "zero":
                zero = f"CC0-{m.group('pv') or '1.0'}"
                return Licence(zero, deed(zero))
            return Licence(PUBLIC_DOMAIN, u)
        kind = m.group("kind").lower().replace("bync", "by-nc")
        got = _cc(kind.split("-"), m.group("v"))
        return Licence(got, deed(got)) if got else None
    if _ARXIV_URL.search(u):
        return Licence(ARXIV, u)
    if u.lower().startswith(("http://", "https://")):
        return Licence(PUBLISHER, u)
    return None


# the words of a licence's name, squashed: "Non-Commercial" is "noncommercial"
_WORD = (
    ("attribution", "by"),
    ("noncommercial", "nc"),
    ("sharealike", "sa"),
    ("noderiv", "nd"),
)
# a licence by its name: "Creative Commons Attribution-NonCommercial 4.0",
# its parts written apart, with hyphens or glued ("AttributionNoncommercial")
_NAMED = re.compile(
    r"creative\s+commons\s+(?:\(cc\)\s+)?"
    r"(?P<words>attribution(?:[\s-]*(?:non[\s-]?commercial|share[\s-]?alike"
    r"|no[\s-]?deriv(?:ative)?s?(?:\s+works)?)){0,2})"
    r"(?:[\s-]+(?:international|unported|generic))?"
    r"(?:\s+(?:public\s+)?licen[cs]e)?"
    r"(?:[\s,(]+(?:version\s+|v)?(?P<v>\d\.\d))?",
    re.IGNORECASE,
)
# by its short form: "CC BY-NC-SA 4.0", "CC-BY 3.0"
_SHORT = re.compile(
    r"\bCC[\s-]+(?P<parts>BY(?:[\s-]+(?:NC|SA|ND)){0,2})\b(?:[\s-]+(?P<v>\d\.\d))?"
)
_ZERO = re.compile(
    r"\bCC0\b(?:[\s-]+(?P<v>1\.0))?|creative\s+commons\s+zero", re.IGNORECASE
)


def from_name(words: str | None) -> Licence | None:
    """The licence a name states: a URL in it, a Creative Commons licence's
    name or short form, CC0. None when it names none of them."""
    if not words:
        return None
    if m := _CC_URL.search(words):
        return from_url(m.group(0))
    if m := _NAMED.search(words):
        squashed = re.sub(r"[\s-]+", "", m.group("words").lower())
        parts = [part for word, part in _WORD if word in squashed]
        if got := _cc(parts, m.group("v")):
            return Licence(got, deed(got))
    if (m := _SHORT.search(words)) and (
        got := _cc(re.split(r"[\s-]+", m.group("parts")), m.group("v"))
    ):
        return Licence(got, deed(got))
    if m := _ZERO.search(words):
        return Licence(f"CC0-{m.group('v') or '1.0'}")
    return None


# a sentence that speaks of the document it stands in
_ABOUT_ITSELF = re.compile(
    r"\b(?:this|the\s+present)\s+(?:work|article|paper|document|chapter|book"
    r"|publication|thesis|dissertation|report|manual|text|issue|volume|preprint"
    r"|item|contribution|page|site|website|entry|edition|guide)\b"
    r"|\bopen[\s-]+access\b|\blicensee\b|©|\(c\)\s*\d{4}|\bcopyright\b"
    r"|\b(?:text|content)\s+is\s+(?:available|licensed)\s+under\b"
    r"|\b(?:manual|guide|documentation|handbook)\s+is\s+licensed\s+under\b"
    r"|^\W*it\s+is\s+made\s+available\s+under\b|\buse\s+permitted\s+under\b"
    r"|\bexcept\s+where\s+otherwise\s+noted\b"
    r"|^\W*(?:licen[cs]ed|published|distributed|released|available)\s+under\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z©])|\n\s*\n")
_MENTION = re.compile(
    r"creative\s*commons|creativecommons\.org|\bCC[\s-]+BY\b|\bCC0\b", re.IGNORECASE
)


def _sentence(text: str, at: int) -> str:
    """The sentence of ``text`` around position ``at``."""
    start = 0
    for m in _SENTENCE_END.finditer(text, 0, at):
        start = m.end()
    end_m = _SENTENCE_END.search(text, at)
    end = end_m.start() if end_m else len(text)
    return text[start:end].strip()


def printed(text: str) -> Licence | None:
    """The licence the text prints about itself, with its words: the first
    sentence in its head (``HEAD_CHARS``) or tail (``TAIL_CHARS``) that
    names a licence and speaks of the document. A URL in the sentence wins
    over its name, which carries less (a version left out)."""
    if not text:
        return None
    parts = (
        [text]
        if len(text) <= HEAD_CHARS + TAIL_CHARS
        else [text[:HEAD_CHARS], text[-TAIL_CHARS:]]
    )
    for part in parts:
        for m in _MENTION.finditer(part):
            said = _sentence(part, m.start())
            if not _ABOUT_ITSELF.search(said):
                continue
            got = from_name(said)
            if got is None:
                continue
            words = re.sub(r"\s+", " ", said)[:300]
            return Licence(got.id, got.url, words)
    return None
