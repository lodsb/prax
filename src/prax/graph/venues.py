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

import functools
import re
import unicodedata
from dataclasses import dataclass

from prax.graph import ontology
from prax.text import dates

NEVER = re.compile(r"(?!x)x")


@dataclass(frozen=True)
class Words:
    """The research pack's venue words (its ``lexicon.yaml``, section
    ``venues``), as the patterns and sets ``read`` uses. A host whose
    lexicon has no such section reads every name as its plain words."""

    leading: re.Pattern[str] = NEVER
    abbreviations: tuple[tuple[str, str], ...] = ()
    stop: frozenset[str] = frozenset()
    when: frozenset[str] = frozenset()
    not_acronym: frozenset[str] = frozenset()
    venue_words: frozenset[str] = frozenset()
    event_words: frozenset[str] = frozenset()
    publishers: frozenset[str] = frozenset()
    ordinal: re.Pattern[str] = NEVER
    short_year: re.Pattern[str] = NEVER
    spelled: re.Pattern[str] = NEVER
    units: tuple[str, ...] = ()
    tens: tuple[tuple[str, int], ...] = ()
    tenths: tuple[tuple[str, int], ...] = ()


def _alternation(phrases: list[str]) -> str:
    """Phrases longest first, a space matching a space or a hyphen."""
    return "|".join(
        re.escape(p).replace(r"\ ", r"[\s-]+")
        for p in sorted(phrases, key=len, reverse=True)
    )


@functools.lru_cache(maxsize=4)
def _words(lex: ontology.Lexicon) -> Words:
    data = lex.section("venues") or {}
    if not data:
        return Words()

    def seq(key: str) -> list[str]:
        return [str(x) for x in data.get(key) or []]

    def folded(key: str) -> frozenset[str]:
        return frozenset(_fold(x) for x in seq(key))

    # the core lexicon's company forms are no acronyms either: GmbH, Inc
    stems, forms = dict(lex.not_a_venue).get("company", ((), ()))
    legal = {
        part.upper()
        for cue in (*stems, *forms)
        for part in re.split(r"[^\w]+", cue)
        if len(part) >= 2
    }
    leading = (
        rf"^(?:(?:{_alternation(seq('citing'))})\s+)?"
        rf"(?:(?:(?:{_alternation(seq('articles'))})\s+)?"
        rf"(?:{_alternation(seq('leading'))})\s+"
        rf"(?:{_alternation(seq('joiners'))})?\s*"
        rf"(?:(?:{_alternation(seq('articles'))})\s+)?)+"
    )
    suffixes = seq("ordinal_suffixes")
    # the digits of an ordinal are never a short year: "IEEE 24th
    # Workshop" is no 2024 (a one-letter suffix only as a word's end)
    not_ordinal = "|".join(
        re.escape(s) + (r"\b" if len(s) == 1 else "")
        for s in sorted(suffixes, key=len, reverse=True)
        if s.isalpha()
    )
    units = seq("spelled_units")
    tens = {str(k): int(v) for k, v in (data.get("spelled_tens") or {}).items()}
    tenths = {str(k): int(v) for k, v in (data.get("spelled_tenths") or {}).items()}
    return Words(
        leading=re.compile(leading, re.IGNORECASE),
        abbreviations=tuple(
            (str(k), str(v)) for k, v in (data.get("abbreviations") or {}).items()
        ),
        stop=folded("stop"),
        when=folded("when") | {_fold(m) for m in dates.month_names()},
        not_acronym=frozenset(x.upper() for x in seq("not_acronym")) | legal,
        venue_words=folded("venue_words"),
        event_words=folded("event_words"),
        publishers=folded("series_publishers"),
        ordinal=re.compile(
            rf"\b(\d{{1,3}})\s*(?:{_alternation(suffixes)})(?=\s|$)", re.IGNORECASE
        )
        if suffixes
        else NEVER,
        # CHI '04, DAFx25, DAFx-17, CHI 98
        short_year=re.compile(
            r"(?:['’‘`]|(?<=[A-Za-z])|(?<=[A-Za-z]-)|(?<=[A-Z] ))(\d{2})(?!\d"
            + (f"|{not_ordinal}" if not_ordinal else "")
            + ")"
        ),
        # "the Thirty-Sixth AAAI Conference", "Seventeenth International
        # Conference"
        spelled=re.compile(
            rf"\b(?:({'|'.join(map(re.escape, tens))})[- ]?)?("
            + "|".join(map(re.escape, [*units, *tenths]))
            + r")\b",
            re.IGNORECASE,
        )
        if units or tenths
        else NEVER,
        units=tuple(units),
        tens=tuple(tens.items()),
        tenths=tuple(tenths.items()),
    )


def words() -> Words:
    """The venue words of the lexicon on disk."""
    return _words(ontology.lexicon())


_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")
_APOSTROPHE_YEAR = re.compile(r"['’‘`](\d{2})(?!\d)")
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


@dataclass(frozen=True)
class Venue:
    series: str  # folded words that stay from year to year
    edition: str | None  # a year or an ordinal ("2008", "11th")
    acronym: str | None  # the name's own acronym, upper case
    stated: bool = False  # the name states its acronym: "(DAFx-06)"
    ordinal: int | None = None  # "the 123rd", beside a year: two in one year
    # a joint meeting's other series, by the acronyms it states: "Joint
    # International Conference ICMC and SMC" is an edition of both
    others: tuple[str, ...] = ()


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def _short_year(two: str) -> str:
    n = int(two)
    return f"20{two}" if n <= 49 else f"19{two}"


def read(name: str) -> Venue:
    """A venue name as its series, its edition and its acronym."""
    w = words()
    text = " ".join(name.split())
    text = _TAIL.sub("", text)
    # an acronym in brackets is the name's own: "… Digital Audio Effects
    # (DAFx-06)", although DAFx is not the words' initials
    bracketed = [
        a
        for inner in re.findall(r"\(([^)]*)\)", text)
        for a in _ACRONYM.findall(inner)
        if a.upper() not in w.not_acronym
    ]
    text = _VOLUME.sub(" ", text)
    text = re.sub(r"\(([^)]*)\)", r" \1 ", text)  # "(ICMC)" keeps its word
    edition = None
    number: int | None = None
    year = _YEAR.search(text)
    if year:
        edition = year.group(1)
        text = text[: year.start()] + " " + text[year.end() :]
    acronyms = [a for a in _ACRONYM.findall(text) if a.upper() not in w.not_acronym]
    if edition is None:
        # an apostrophe year ("SOSP '09") is surer than digits after letters
        short = _APOSTROPHE_YEAR.search(text) or w.short_year.search(text)
        if short and (acronyms or "'" in text or "’" in text):
            edition = _short_year(short.group(1))
            text = text[: short.start()] + " " + text[short.end() :]
    # "DAFX 12, International Conference on …, York, UK, September 17-21":
    # past the first comma, a name that already says its venue goes on
    # with where and when
    head, _, tail = text.partition(",")
    # (only past the venue's own acronym: "IEEE Transactions on Systems,
    # Man, and Cybernetics" is one title with commas in it)
    if tail and any(a.upper() not in w.not_acronym for a in _ACRONYM.findall(head)):
        text = head
    text = text.replace("_th", "th")
    ordinal = w.ordinal.search(text)
    if ordinal:
        number = int(ordinal.group(1))
        edition = edition or f"#{number}"
        text = text[: ordinal.start()] + " " + text[ordinal.end() :]
    else:
        spelled = w.spelled.search(text)
        if spelled:
            tens = dict(w.tens).get((spelled.group(1) or "").lower(), 0)
            unit = spelled.group(2).lower()
            tenths = dict(w.tenths)
            n = tens + (tenths[unit] if unit in tenths else w.units.index(unit) + 1)
            number = n
            edition = edition or f"#{n}"
            text = text[: spelled.start()] + " " + text[spelled.end() :]
        else:
            roman = _roman_edition(text, w)
            if roman is not None:
                number, start, end = roman
                edition = edition or f"#{number}"
                numeral = text[start:end]
                acronyms = [a for a in acronyms if a != numeral]  # XXV is no name
                text = text[:start] + " " + text[end:]
    # "Proceedings of the 5th Conference on X" is the conference; but
    # "Proceedings of the IEEE" and "Proceedings of the Musical
    # Association" are journals, not the body that publishes them
    stripped = w.leading.sub("", text.strip())
    if stripped != text.strip():
        rest = set(re.split(r"[^\w]+", _fold(stripped))) - {""}
        own = [a for a in _ACRONYM.findall(stripped) if a.upper() not in w.not_acronym]
        if rest & w.event_words or own:
            text = stripped
    kept = []
    # a plain number stays: "Lecture 6" and "Lecture 10" are two things
    for raw in re.split(r"[^\w]+", _fold(text)):
        if not raw:
            continue
        word = dict(w.abbreviations).get(raw, raw)
        if word not in w.stop:
            kept.append(word)
    acronym = None
    for a in acronyms:
        a = re.sub(r"\d+$", "", a)
        if 3 <= len(a) <= 10 and a.upper() not in w.not_acronym:
            acronym = a.upper()
            break
    if kept and all(k in w.when for k in kept):
        kept = []  # a date, not a venue
    if "joint" in kept and edition is None:
        edition = "joint"  # two series met once: not either of them
    stated = acronym is not None and any(
        re.sub(r"\d+$", "", b).upper() == acronym for b in bracketed
    )
    others: tuple[str, ...] = ()
    if edition == "joint" and acronym is not None:
        others = tuple(
            dict.fromkeys(
                b.upper()
                for b in (re.sub(r"\d+$", "", x) for x in bracketed)
                if b.upper() != acronym and 3 <= len(b) <= 10
            )
        )
    return Venue(" ".join(kept), edition, acronym, stated, number, others)


_ROMAN = re.compile(r"\b([IVXL]{1,6})\s+(\w+)")
_ROMAN_VALUE = {"I": 1, "V": 5, "X": 10, "L": 50}


def _roman_edition(text: str, w: Words) -> tuple[int, int, int] | None:
    """An edition written as a roman numeral, only where a meeting's word
    follows it ("Atti del XX Colloquio", "IV International Conference";
    the word after "International" is looked at too): a numeral alone
    meets real acronyms (CHI, MIX, VR). ``(number, start, end)`` of the
    numeral, or None."""
    for m in _ROMAN.finditer(text):
        after = _fold(m.group(2))
        rest = text[m.end() :].split()
        if after in ("international", "internationale", "internazionale"):
            after = _fold(rest[0]) if rest else ""
        if after not in w.event_words:
            continue
        digits = [_ROMAN_VALUE[c] for c in m.group(1)]
        n = sum(
            -d if i + 1 < len(digits) and d < digits[i + 1] else d
            for i, d in enumerate(digits)
        )
        if 1 < n <= 60:
            return n, m.start(1), m.end(1)
    return None


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
    return any(w in words().venue_words for w in series.split())


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
    # only when what is left is the name's own acronym: "IEEE MultiMedia"
    # (a magazine) and "ACM Multimedia" (a conference) would otherwise meet
    # on "multimedia" (the review of 2026-10-04)
    plain = [w for w in v.series.split() if w not in words().publishers]
    if (
        v.acronym
        and plain == [v.acronym.lower()]
        and len(plain) < len(v.series.split())
    ):
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
    without an edition, when there is one. A joint meeting is an edition
    of every series whose acronym it states too (ICMC and SMC met once
    as one conference), never merged into either."""
    reads = {i: (read(n), d) for i, n, d in entities}
    sets = _Sets()
    for i, (v, _) in reads.items():
        for form in series_names(v, expansions):
            sets.join(("e", i), form)
    groups: dict[object, list[int]] = {}
    for i in reads:
        if sets.has(("e", i)):
            groups.setdefault(sets.find(("e", i)), []).append(i)
    out = Plan([], [])
    series_of: dict[object, int] = {}  # a group's root -> its bare series
    for root, members in groups.items():
        if len(members) < 2:
            continue
        series = _plan_group(members, reads, out)
        if series is not None:
            series_of[root] = series
    for i, (v, _) in reads.items():
        for other in v.others:
            form = other.lower()
            if not sets.has(form):
                continue
            series = series_of.get(sets.find(form))
            if series is not None and series != i and (i, series) not in out.editions:
                out.editions.append((i, series))
    return out


class _Sets:
    """Union-find over the venue names and the forms of their series."""

    def __init__(self) -> None:
        self.parent: dict[object, object] = {}

    def find(self, x: object) -> object:
        parent = self.parent
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def join(self, a: object, b: object) -> None:
        self.parent[self.find(a)] = self.find(b)

    def has(self, x: object) -> bool:
        return x in self.parent


def _plan_group(
    members: list[int], reads: dict[int, tuple[Venue, int]], out: Plan
) -> int | None:
    """One series' names: those of one edition merge into the most
    connected, and each edition is ``part_of`` the bare series. The bare
    series (no edition), or None when the group has none."""
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
        out.editions += [(i, series) for k, i in survivors.items() if k[0] is not None]
    return series


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
    if set(_fold(name).replace("-", " ").split()) & words().venue_words:
        return None
    cues = dict(ontology.lexicon().not_a_venue)
    for kind in NOT_A_VENUE_ORDER:
        if kind in cues and ontology.cue_pattern(cues[kind]).search(name):
            return kind
    return None
