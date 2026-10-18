"""An arXiv id, however a text writes it: the one grammar every reader of
ids uses (a reference list, a captured URL, a PDF's margin stamp, a
published date, the ``latex-source`` reading), so that one paper has one
key wherever it is named.

An id is new-style (``2501.13484``, from April 2007) or old-style
(``hep-th/9901001``, ``math.GT/0309136``: the archive, its subject class
and the year, month and number), with an optional version (``v2``).
``canonical`` is the id without its version, the archive in lower case
and the subject class in upper case, so a citation of ``2501.13484v2``
and the copy captured from ``arxiv.org/abs/2501.13484`` are one key. Five
grammars disagreed on this until the quality review of 2026-10-18."""

from __future__ import annotations

import re

# the id without its version, as a pattern other patterns build on
ID = r"\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[a-z]{2})?/\d{7}"
VERSION = r"(?:v\d+)?"

_WHOLE = re.compile(rf"({ID}){VERSION}", re.IGNORECASE)
# named in a text: "arXiv:2501.13484v2", "arXiv 2501.13484", "arXiv:hep-th/9901001",
# and in emphasis, "_arXiv:1410.5401_": only a further digit ends the match
_NAMED = re.compile(rf"arXiv[:\s]*({ID}){VERSION}(?!\d)", re.IGNORECASE)
_URL = re.compile(
    rf"^https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/({ID}){VERSION}"
    r"(?:\.pdf)?/?$",
    re.IGNORECASE,
)
_PREFIX = re.compile(r"^arxiv:\s*", re.IGNORECASE)


def _normal(bare: str) -> str:
    """The case an id is keyed by: ``MATH.gt/0309136`` is ``math.GT/0309136``."""
    if "/" not in bare:
        return bare
    archive, number = bare.split("/", 1)
    name, _, subject = archive.partition(".")
    if subject:
        return f"{name.lower()}.{subject.upper()}/{number}"
    return f"{name.lower()}/{number}"


def canonical(value: object) -> str | None:
    """The id ``value`` is, without its version, or None when it is not one
    (an ``arXiv:`` before it is allowed)."""
    s = _PREFIX.sub("", str(value or "").strip())
    m = _WHOLE.fullmatch(s)
    return _normal(m.group(1)) if m else None


def named(text: str) -> str | None:
    """The first id a text names as ``arXiv:…``, canonical, or None."""
    m = _NAMED.search(text)
    return _normal(m.group(1)) if m else None


def of_url(url: str) -> str | None:
    """The id of an arXiv abstract or PDF URL, canonical, or None."""
    m = _URL.match(url.strip())
    return _normal(m.group(1)) if m else None


def yymm(value: object) -> tuple[int, int] | None:
    """The two-digit year and the month an id was first submitted in:
    ``2310.08560`` is (23, 10), ``hep-th/9901001`` (99, 1)."""
    bare = canonical(value)
    if bare is None:
        return None
    digits = bare.rsplit("/", 1)[-1]
    return int(digits[:2]), int(digits[2:4])
