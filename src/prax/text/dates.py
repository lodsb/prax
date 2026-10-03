"""When a document says it was published: a date and how precise it is.

A person judges a source by when it was written, and prax showed that
for a seventh of the library (the user, 2026-10-03). What says it, from
the most trusted down (``store.published_of`` chooses): a person; the
Zotero record; what the browser extension found on a paper's page; a
web page's citation tags; its schema.org ``datePublished``; the arXiv id
(the month of the first version); generic article and Dublin Core tags.

A date is an ISO prefix as precise as its source: ``2019``, ``2019-07``
or ``2019-07-03``, with the precision named (``year``, ``month``,
``day``). Nothing finer than a day: a publication's hour says nothing a
reader weighs. Nothing is guessed: a string that is not clearly a date
gives ``None``.

Imports nothing of prax (the ``text`` package's rule).
"""

from __future__ import annotations

import json
import re

PRECISIONS = ("year", "month", "day")
EARLIEST, LATEST = 1500, 2100  # a year outside is a number, not a date

_MONTHS = {
    name: n
    for n, names in enumerate(
        (
            ("january", "jan", "januar", "jänner", "janvier"),
            ("february", "feb", "februar", "février", "fevrier"),
            ("march", "mar", "märz", "maerz", "mars"),
            ("april", "apr", "avril"),
            ("may", "mai"),
            ("june", "jun", "juni", "juin"),
            ("july", "jul", "juli", "juillet"),
            ("august", "aug", "août", "aout"),
            ("september", "sep", "sept", "septembre"),
            ("october", "oct", "oktober", "okt", "octobre"),
            ("november", "nov", "novembre"),
            ("december", "dec", "dezember", "dez", "décembre", "decembre"),
        ),
        1,
    )
    for name in names
}

_ISO = re.compile(r"(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?(?=$|[T\s])")
_DAY_MONTH_YEAR = re.compile(r"(\d{1,2})\.?\s+([A-Za-zÀ-ÿ]+)\.?,?\s+(\d{4})$")
_MONTH_DAY_YEAR = re.compile(r"([A-Za-zÀ-ÿ]+)\.?\s+(\d{1,2}),?\s+(\d{4})$")
_MONTH_YEAR = re.compile(r"([A-Za-zÀ-ÿ]+)\.?,?\s+(\d{4})$")
_YEAR = re.compile(r"(\d{4})$")


def _make(
    year: int, month: int | None = None, day: int | None = None
) -> tuple[str, str] | None:
    if not EARLIEST <= year <= LATEST:
        return None
    if month is None:
        return f"{year:04d}", "year"
    if not 1 <= month <= 12:
        return None
    if day is None:
        return f"{year:04d}-{month:02d}", "month"
    if not 1 <= day <= 31:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}", "day"


def parse(text: str | None) -> tuple[str, str] | None:
    """A date as written, as ``(date, precision)``: ISO and its slashed or
    dotted forms (``2019-07-03``, ``2019/07``, ``2019-07-03T10:00:00Z``),
    ``3 July 2019``, ``July 3, 2019``, ``July 2019``, ``2019``; month
    names in English, German and French. ``None`` for anything else."""
    s = " ".join(str(text or "").split()).strip()
    if not s:
        return None
    if m := _ISO.match(s):
        y, mo, d = m.groups()
        return _make(int(y), int(mo) if mo else None, int(d) if d else None)
    low = s.lower()
    if m := _DAY_MONTH_YEAR.match(low):
        month = _MONTHS.get(m.group(2))
        return _make(int(m.group(3)), month, int(m.group(1))) if month else None
    if m := _MONTH_DAY_YEAR.match(low):
        month = _MONTHS.get(m.group(1))
        return _make(int(m.group(3)), month, int(m.group(2))) if month else None
    if m := _MONTH_YEAR.match(low):
        month = _MONTHS.get(m.group(1))
        return _make(int(m.group(2)), month) if month else None
    if m := _YEAR.match(s):
        return _make(int(m.group(1)))
    return None


_ARXIV_NEW = re.compile(r"^(\d{2})(\d{2})\.\d{4,5}(?:v\d+)?$")
_ARXIV_OLD = re.compile(r"^[a-z\-]+(?:\.[A-Z]{2})?/(\d{2})(\d{2})\d{3}(?:v\d+)?$")


def from_arxiv(arxiv_id: str | None) -> tuple[str, str] | None:
    """The month of an arXiv paper's first version, from its id:
    ``2310.08560`` is 2023-10, ``hep-th/9901001`` 1999-01."""
    s = str(arxiv_id or "").strip()
    m = _ARXIV_NEW.match(s)
    if m:
        return _make(2000 + int(m.group(1)), int(m.group(2)))
    m = _ARXIV_OLD.match(s)
    if m:
        yy = int(m.group(1))
        return _make((1900 if yy >= 91 else 2000) + yy, int(m.group(2)))
    return None


# a web page's tags, in the order of trust: citation tags are what
# scholarly sites write for Google Scholar; article and Dublin Core tags
# are what any site may write, often the day it was last edited
CITATION_TAGS = (
    "citation_publication_date",
    "citation_date",
    "citation_online_date",
    "citation_cover_date",
)
GENERIC_TAGS = (
    "article:published_time",
    "og:published_time",
    "dc.date.issued",
    "dcterms.issued",
    "dc.date",
    "dcterms.date",
    "date",
)
HEAD_BYTES = 400_000  # what is read of a page: its head and then some

_META = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_JSONLD = re.compile(
    r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def _meta_tags(head: str) -> dict[str, str]:
    """Each ``<meta name|property=… content=…>``, the name lowercased,
    the first of a name kept."""
    out: dict[str, str] = {}
    for tag in _META.findall(head):
        attrs = {k.lower(): (a or b) for k, a, b in _ATTR.findall(tag)}
        name = (attrs.get("name") or attrs.get("property") or "").strip().lower()
        content = attrs.get("content")
        if name and content and name not in out:
            out[name] = content
    return out


def _jsonld_dates(head: str) -> list[str]:
    """``datePublished`` of every JSON-LD object on the page, the first
    first (a page's own article before what it links)."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            value = node.get("datePublished")
            if isinstance(value, str):
                found.append(value)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    for block in _JSONLD.findall(head):
        try:
            walk(json.loads(block.strip()))
        except ValueError:
            continue
    return found


def from_html(data: bytes | str) -> dict[str, tuple[str, str]]:
    """What a page says of when it was published, by kind of tag:
    ``citation`` (the first citation tag that parses), ``jsonld``
    (schema.org ``datePublished``) and ``generic`` (article and Dublin
    Core tags). Empty when it says nothing that parses."""
    head = (
        data[:HEAD_BYTES].decode("utf-8", "replace")
        if isinstance(data, bytes)
        else data[:HEAD_BYTES]
    )
    tags = _meta_tags(head)
    out: dict[str, tuple[str, str]] = {}
    for kind, names in (("citation", CITATION_TAGS), ("generic", GENERIC_TAGS)):
        for name in names:
            got = parse(tags.get(name))
            if got:
                out[kind] = got
                break
    for value in _jsonld_dates(head):
        got = parse(value)
        if got:
            out["jsonld"] = got
            break
    return out
