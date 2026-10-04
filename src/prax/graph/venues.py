"""Which venue names are one series, and which name an edition of it.

The extraction writes a venue as the document prints it: "ISMIR",
"ISMIR 2008 – Session 3a", "Proceedings of the 11th International
Society for Music Information Retrieval Conference", "26th Int. Society
for Music Information Retrieval Conf.". To the functional relation
``published_in`` each is another venue, so a paper "published in" two of
them was a conflict (``prax heal --check functional-conflicts``) where
there is none.

``read`` reduces a name to its series (the words that stay from year to
year), its edition (a year, or an ordinal) and its acronym. Two names of
one series and one edition are one venue (a merge, in resolution's
``venue`` tier); an edition is ``part_of`` its series' bare name. An
acronym meets its expansion through the library's own acronyms table
("phrase (ACRONYM)" in the texts, ``store.acronyms``) or a name that
carries both ("Web Audio Conference WAC-2018").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from prax.graph import ontology

# what a printed venue name carries that is not its series
_LEADING = re.compile(
    r"^(?:(?:in|in:)\s+)?(?:(?:the\s+)?(?:proceedings|proc\.?|extended\s+abstracts|"
    r"companion|adjunct\s+proceedings|abstracts|late[- ]breaking\s+(?:demo\s+)?"
    r"(?:session|papers?)|papers|tagungsband)\s+(?:of|to|for|from|zur|der)?\s*(?:the\s+)?)+",
    re.IGNORECASE,
)
_ABBREVIATIONS = {
    "int": "international",
    "intl": "international",
    "conf": "conference",
    "symp": "symposium",
    "proc": "proceedings",
    "assoc": "association",
    "soc": "society",
    "trans": "transactions",
    "j": "journal",
}
_STOP = {"the", "of", "on", "for", "and", "in", "a", "an", "&", "annual"}
# a name of only these says when, not where: "March 2009", "SS 2010"
_WHEN = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "januar", "februar", "marz",
    "mai", "juni", "juli", "oktober", "dezember", "ss", "ws", "semester",
    "sommersemester", "wintersemester", "summer", "winter", "spring", "fall",
}  # fmt: skip
# what looks like an acronym and is a legal form or a generic word
_NOT_ACRONYM = {
    "GMBH", "KG", "OHG", "AG", "INC", "LTD", "LLC", "CO", "SE", "BV", "IEEE",
    "ACM", "AES", "THE", "SS", "WS", "PHD", "MSC", "BSC", "USA", "UK", "EU",
}  # fmt: skip
# a word that says a name is a venue: what an acronym's expansion or a
# name beside a bare acronym must carry before the two are one series
VENUE_WORDS = {
    "conference", "symposium", "workshop", "journal", "transactions",
    "congress", "convention", "meeting", "society", "proceedings", "forum",
    "colloquium", "review", "letters", "magazine", "summit", "expo",
}  # fmt: skip
# what a meeting is called, rather than a journal or a body
_EVENT_WORDS = {
    "conference", "symposium", "workshop", "congress", "convention",
    "meeting", "colloquium", "summit", "forum", "session", "school",
}  # fmt: skip
_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")
# CHI '04, DAFx25, DAFx-17, CHI 98
_SHORT_YEAR = re.compile(
    r"(?:['’‘`]|(?<=[A-Za-z])|(?<=[A-Za-z]-)|(?<=[A-Z] ))(\d{2})(?!\d)"
)
_ORDINAL = re.compile(r"\b(\d{1,3})\s*(?:st|nd|rd|th|d|\.)(?=\s|$)", re.IGNORECASE)
_UNITS = [
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
    "fourteenth",
    "fifteenth",
    "sixteenth",
    "seventeenth",
    "eighteenth",
    "nineteenth",
]
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
_TENTHS = {"twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50}
# "the Thirty-Sixth AAAI Conference", "Seventeenth International Conference"
_WORD_ORDINAL = re.compile(
    r"\b(?:(twenty|thirty|forty|fifty)[- ]?)?("
    + "|".join([*_UNITS, *_TENTHS])
    + r")\b",
    re.IGNORECASE,
)
# an acronym may run into its year: "DAFx23", "NIME05"
_ACRONYM = re.compile(r"\b([A-Z][A-Za-z]*[A-Z][A-Za-z]*|[A-Z]{2,})(?=\d*\b)")
# a session, a track or a page range after a dash belongs to the paper
_TAIL = re.compile(
    r"\s+[–—-]+\s+(?:session|track|workshop|demo|poster|pp?\.)\b.*$", re.IGNORECASE
)


# a volume or an issue: "(Volume 6, 2018)", "Vol. 21 No. 3", "1(2)"
_VOLUME = re.compile(
    r"\b(?:vol(?:ume)?|no|nr|issue|heft|band)\.?\s*\d+\w*|\b\d+\s*\(\d+\)",
    re.IGNORECASE,
)
# a publisher's word before a series it publishes: "IEEE ICASSP"
_PUBLISHERS = {"ieee", "acm", "aes", "springer", "elsevier", "wiley"}
# words that name a kind of venue and nothing of which one
_KIND_ONLY = {"proceedings", "transactions", "journal", "conference", "magazine"}


@dataclass(frozen=True)
class Venue:
    series: str  # folded words that stay from year to year
    edition: str | None  # a year or an ordinal ("2008", "11th")
    acronym: str | None  # the name's own acronym, upper case
    stated: bool = False  # the name states its acronym: "(DAFx-06)"
    ordinal: int | None = None  # "the 123rd", beside a year: two in one year


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def _short_year(two: str) -> str:
    n = int(two)
    return f"20{two}" if n <= 49 else f"19{two}"


def read(name: str) -> Venue:
    """A venue name as its series, its edition and its acronym."""
    text = " ".join(name.split())
    text = _TAIL.sub("", text)
    # an acronym in brackets is the name's own: "… Digital Audio Effects
    # (DAFx-06)", although DAFx is not the words' initials
    bracketed = [
        a
        for inner in re.findall(r"\(([^)]*)\)", text)
        for a in _ACRONYM.findall(inner)
        if a.upper() not in _NOT_ACRONYM
    ]
    text = _VOLUME.sub(" ", text)
    text = re.sub(r"\(([^)]*)\)", r" \1 ", text)  # "(ICMC)" keeps its word
    edition = None
    number: int | None = None
    year = _YEAR.search(text)
    if year:
        edition = year.group(1)
        text = text[: year.start()] + " " + text[year.end() :]
    acronyms = [a for a in _ACRONYM.findall(text) if a.upper() not in _NOT_ACRONYM]
    if edition is None:
        short = _SHORT_YEAR.search(text)
        if short and (acronyms or "'" in text or "’" in text):
            edition = _short_year(short.group(1))
            text = text[: short.start()] + " " + text[short.end() :]
    # "DAFX 12, International Conference on …, York, UK, September 17-21":
    # past the first comma, a name that already says its venue goes on
    # with where and when
    head, _, tail = text.partition(",")
    # (only past the venue's own acronym: "IEEE Transactions on Systems,
    # Man, and Cybernetics" is one title with commas in it)
    if tail and any(a.upper() not in _NOT_ACRONYM for a in _ACRONYM.findall(head)):
        text = head
    text = text.replace("_th", "th")
    ordinal = _ORDINAL.search(text)
    if ordinal:
        number = int(ordinal.group(1))
        edition = edition or f"#{number}"
        text = text[: ordinal.start()] + " " + text[ordinal.end() :]
    else:
        spelled = _WORD_ORDINAL.search(text)
        if spelled:
            tens = _TENS.get((spelled.group(1) or "").lower(), 0)
            unit = spelled.group(2).lower()
            n = tens + (_TENTHS[unit] if unit in _TENTHS else _UNITS.index(unit) + 1)
            number = n
            edition = edition or f"#{n}"
            text = text[: spelled.start()] + " " + text[spelled.end() :]
    # "Proceedings of the 5th Conference on X" is the conference; but
    # "Proceedings of the IEEE" and "Proceedings of the Musical
    # Association" are journals, not the body that publishes them
    stripped = _LEADING.sub("", text.strip())
    if stripped != text.strip():
        rest = set(re.split(r"[^\w]+", _fold(stripped))) - {""}
        own = [a for a in _ACRONYM.findall(stripped) if a.upper() not in _NOT_ACRONYM]
        if rest & _EVENT_WORDS or own:
            text = stripped
    words = []
    # a plain number stays: "Lecture 6" and "Lecture 10" are two things
    for raw in re.split(r"[^\w]+", _fold(text)):
        if not raw:
            continue
        w = _ABBREVIATIONS.get(raw, raw)
        if w not in _STOP:
            words.append(w)
    acronym = None
    for a in acronyms:
        a = re.sub(r"\d+$", "", a)
        if 3 <= len(a) <= 10 and a.upper() not in _NOT_ACRONYM:
            acronym = a.upper()
            break
    if words and all(w in _WHEN for w in words):
        words = []  # a date, not a venue
    if "joint" in words and edition is None:
        edition = "joint"  # two series met once: not either of them
    stated = acronym is not None and any(
        re.sub(r"\d+$", "", b).upper() == acronym for b in bracketed
    )
    return Venue(" ".join(words), edition, acronym, stated, number)


def is_bare(v: Venue) -> bool:
    """A name that is its acronym alone ("DAFx", "CHI")."""
    return v.acronym is not None and v.series == v.acronym.lower()


def _initials_of(acronym: str, series: str) -> bool:
    """Whether an acronym's letters are, in order, the initials of the
    name's other words (ICASSP and the International Conference on
    Acoustics, Speech and Signal Processing; not SIAM and "SIAM Review",
    nor MULTIMEDIA and "IEEE Transactions on Multimedia")."""
    initials = "".join(w[0] for w in series.split() if w != acronym)
    it = iter(initials)
    return len(acronym) >= 3 and all(ch in it for ch in acronym)


def _venue_like(series: str) -> bool:
    return any(w in VENUE_WORDS for w in series.split())


def series_names(v: Venue, expansions: dict[str, set[str]]) -> set[str]:
    """The forms a name's series is known by, for grouping: its words; for
    a bare acronym, the acronym and the expansions the library defines
    that are venue-like ("ICMC" is the International Computer Music
    Conference; "DSP" is a field, no venue); for a venue-like name that
    carries an acronym, the acronym too ("Web Audio Conference WAC")."""
    if not v.series:
        return set()
    out = {v.series}
    # "IEEE ICASSP" is ICASSP: the form without a publisher's word, when
    # a series is left ("Proceedings of the IEEE" stays a journal)
    plain = [w for w in v.series.split() if w not in _PUBLISHERS]
    if plain and len(plain) < len(v.series.split()) and not set(plain) <= _KIND_ONLY:
        out.add(" ".join(plain))
    if v.acronym:
        a = v.acronym.lower()
        if is_bare(v):
            for phrase in expansions.get(v.acronym, ()):
                full = read(phrase).series
                # the table holds what the texts wrote, now and then wrong
                if _venue_like(full) and _initials_of(a, full):
                    out.add(full)
        elif _venue_like(v.series) and (v.stated or _initials_of(a, v.series)):
            out.add(a)
            rest = " ".join(w for w in v.series.split() if w != a)
            if len(rest.split()) >= 2:
                out.add(rest)
    return out


@dataclass
class Plan:
    """What the venue tier would do: ``merges`` (keep, drop) of two names
    of one series and one edition, and ``editions`` (edition, series) to
    link ``part_of``."""

    merges: list[tuple[int, int]]
    editions: list[tuple[int, int]]


def plan(entities: list[tuple[int, str, int]], expansions: dict[str, set[str]]) -> Plan:
    """The venue tier over ``(entity id, name, live edges)``: names are
    grouped by the forms their series is known by; in a group, names of
    one edition (or of none) merge into the most connected, and every
    edition is ``part_of`` the group's bare series, the most connected name
    without an edition, when there is one."""
    parent: dict[object, object] = {}

    def find(x: object) -> object:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    reads = {i: (read(n), d) for i, n, d in entities}
    for i, (v, _) in reads.items():
        for form in series_names(v, expansions):
            parent[find(("e", i))] = find(form)
    groups: dict[object, list[int]] = {}
    for i in reads:
        if ("e", i) in parent:
            groups.setdefault(find(("e", i)), []).append(i)
    out = Plan([], [])
    for members in groups.values():
        if len(members) < 2:
            continue
        # one edition is one year, unless two ordinals say otherwise (the
        # 122nd and 123rd AES Conventions were both in 2007); a name that
        # gives no ordinal joins the one ordinal its year has, if one
        by_edition: dict[tuple[str | None, int | None], list[int]] = {}
        for i in members:
            v = reads[i][0]
            by_edition.setdefault((v.edition, v.ordinal), []).append(i)
        for edition, ordinal in list(by_edition):
            if ordinal is None:
                others = [k for k in by_edition if k[0] == edition and k[1] is not None]
                if len(others) == 1:
                    by_edition[others[0]] += by_edition.pop((edition, None))
        survivors: dict[tuple[str | None, int | None], int] = {}
        for key, same in by_edition.items():
            same.sort(key=lambda i: (-reads[i][1], i))
            survivors[key] = same[0]
            out.merges += [(same[0], i) for i in same[1:]]
        series = survivors.get((None, None))
        if series is not None:
            out.editions += [
                (i, series) for k, i in survivors.items() if k[0] is not None
            ]
    return out


NOT_A_VENUE_ORDER = ("none", "publisher", "company", "institution")


def not_a_venue(name: str) -> str | None:
    """What a name typed as a venue says it is instead (the lexicon's
    ``not_a_venue``): ``none`` (a date, a semester, an exercise sheet, a
    licence), ``publisher``, ``company`` or ``institution``; None for a
    venue. A name with a venue word is a venue whatever else it says
    ("Journal of the Audio Engineering Society", "Acta Universitatis
    Upsaliensis" too, which no cue names)."""
    v = read(name)
    if not v.series:
        return "none"  # a date: "March 2009", "Sommersemester 2005"
    if set(_fold(name).replace("-", " ").split()) & VENUE_WORDS:
        return None
    cues = dict(ontology.lexicon().not_a_venue)
    for kind in NOT_A_VENUE_ORDER:
        if kind in cues and ontology.cue_pattern(cues[kind]).search(name):
            return kind
    return None
