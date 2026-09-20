"""A paper's own reference list, read from its text: the entries of a
bibliography and what each one names, so a citation can be matched to a
document already in the library.

The citation importer (``prax.importers.citations``) asks Crossref or
OpenAlex and needs a DOI, which four documents in five here do not have;
their reference lists are in the parsed text all the same, under a
"References" or "Bibliography" heading, and the LLM extractor never sees
them (it reads the first part of a paper). This module is the rules for
that text, and nothing else: no store, no model. ``entries`` splits a
bibliography's text into its entries (numbered "[12]", "12.", or
author-year paragraphs, a continuation line joined to its entry);
``parse`` reads one entry into a ``Reference`` — the surnames, the year,
the title, a DOI or arXiv id when printed. ``similarity`` scores a
reference against a library document's title, creators and year, so the
caller can decide with a threshold and a margin (``match``).

The shapes come from the library's own texts (pymupdf4llm and marker
Markdown): IEEE ("[3] M. Plumbley, T. Blumensath, … “Title,” *Venue*,
2010."), APA ("Boden, M. A. (1994b). What is creativity? In …"), ACM
("JACOB, R. J. K., … 1994. Integrality and separability …"), Springer
("1. Lalegani Dezaki M, … (2021) An overview of …. Rapid Prototyp J"),
MDPI ("9. Allen, M.; Girod, L.; … VoxNet: An Interactive …. In
Proceedings …"). A title in quotes wins; else the sentence after the
year in an author-year entry; else the sentence after the author run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

_BLANK = re.compile(r"\n\s*\n")
_MARK = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:\[(?P<n1>\d{1,4})\]|\((?P<n2>\d{1,4})\)|(?P<n3>\d{1,4})\.(?=\s))\s*"
)
_LIST = re.compile(r"^\s*[-*•]\s+")
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>,;)\]]+)")
_ARXIV = re.compile(r"arXiv[:\s]*(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})[a-z]?(?!\d)")
_QUOTED = re.compile(r"[“\"„«]\s*(.+?)\s*[,.]?\s*[”\"«»]")
_EMPH = re.compile(r"(?<!\w)[_*]{1,2}(.+?)[_*]{1,2}(?!\w)")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_INITIAL = re.compile(r"^[A-ZÀ-Ý]\.?$")
_NOISE = {
    "and",
    "et",
    "al",
    "in",
    "eds",
    "ed",
    "the",
    "of",
    "de",
    "van",
    "von",
    "der",
    "den",
    "la",
    "le",
    "di",
    "da",
    "pp",
    "vol",
    "no",
    "proc",
    "proceedings",
    "ieee",
    "acm",
    "journal",
    "trans",
    "conference",
    "int",
    "on",
}
# an author group: "Surname, I. I." / "Surname I" (Springer, the bare
# initials followed by a separator or the year) / "I. I. Surname" (the
# surname followed by a separator: "S. Rounding corners" is a title) /
# "SURNAME, I. I." / "Two Words, I."; groups run on separated by commas,
# semicolons, "and", "&", "et al."
_NAME = r"[A-ZÀ-Ý][\w'’\-]+(?:\s[A-ZÀ-Ý][\w'’\-]+)?"
_DOTTED = (
    r"[A-ZÀ-Ý]\.(?:[\s\-]?[A-ZÀ-Ý]\.){0,2}"  # never a trailing space: the separator's
)
_BARE = r"[A-ZÀ-Ý]{1,3}(?=\s*[,;(]|\s+(?:and|AND|&|et)\b|\s*$)"
_AFTER = r"(?=\s*[,;.:(]|\s+(?:and|AND|&|et)\b|\s*$)"
_GROUP = rf"(?:{_NAME},?\s+(?:{_DOTTED}|{_BARE})|{_DOTTED}\s*{_NAME}{_AFTER})"
_SEP = r"\s*(?:,\s*(?:and|AND|&)\s+|;\s*|,\s*|\s(?:and|AND|&)\s+|\s+)"
_SUFFIX = r"(?:,?\s*(?:Jr\.?|Sr\.?|III|II|IV)(?![A-Za-z]))?"
_RUN = re.compile(
    rf"^(?:{_GROUP}{_SUFFIX}(?:{_SEP}(?:{_GROUP}{_SUFFIX}|et\s+al\.?))*)[.,;:]?\s*"
)
_YEAR_HEAD = re.compile(
    r"^(?:\(\s*(?:19|20)\d{2}[a-z]?\s*\)|(?:19|20)\d{2}[a-z]?)[.,:]?\s*"
)
# what the extractors wrap an entry in: Markdown links (Elsevier's refhub
# anchors around the whole entry), HTML tags (marker's page anchors, a
# <sup> over an accent), anchors of the text's own
_LINK = re.compile(r"\[([^\]]*)\]\((?:https?://|#)(?:[^()\s]|\([^()\s]*\))*\)")
_TAG = re.compile(r"<[^>]{1,80}>")
_ESCAPED = re.compile(r"\\([()\[\]*_])")  # Markdown's escapes: \(10\) is (10)
# entries run together in one paragraph (Elsevier: "… 8054. [20] K. …")
# or one to a line without a blank line between: split before a marker
# that follows a space or a line break, when the paragraph holds several
_INLINE_MARK = re.compile(r"(?<=[.\d)\]])\s+(?=\[\d{1,4}\]\s)")
_LINE_MARK = re.compile(
    r"\n\s*(?=(?:[-*•]\s*)?(?:\[\d{1,4}\]|\(\d{1,4}\)|\d{1,4}\.)\s)"
)
# the bibliography headings the chunker and the pass recognise
_BIBLIOGRAPHY = ("references", "bibliography", "literatur", "works cited", "literature")


_STRAY = re.compile(r"[´`ˆ¨˜]")  # an accent the extractor set beside its letter


def _plain(text: str) -> str:
    text = _TAG.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _ESCAPED.sub(r"\1", text)
    return _STRAY.sub("", text)


_MIN_ENTRY = 25  # a shorter paragraph is a running header or a page number
_MIN_TITLE_WORDS = 2


@dataclass
class Reference:
    """One entry of a reference list, as read."""

    raw: str
    number: int | None = None
    surnames: list[str] = field(default_factory=list)
    year: int | None = None
    title: str = ""
    doi: str | None = None
    arxiv: str | None = None


def is_bibliography_heading(title: str) -> bool:
    """Does a heading open a reference list ("References", "7. References",
    "Bibliography", "Literaturverzeichnis", "Works Cited")?"""
    h = re.sub(r"^[\d.\s]+", "", (title or "").strip().lower())
    return any(h.startswith(x) or h.endswith(x) for x in _BIBLIOGRAPHY)


def entry_spans(text: str) -> list[tuple[int, int, bool]]:
    """Where the entries of a bibliography's text are, as ``(start, end,
    opens)`` over the text as given — a chunk is a region of the artifact,
    so the offsets are the raw text's, links and tags and all. A piece is
    a paragraph (blank-line separated), a line that opens with a number
    or a list marker, or the tail behind an inline "[20]" (Elsevier's one
    paragraph). ``opens`` says the piece opens an entry — a number, a list
    marker, an author run — rather than continuing the one before it (a
    title wrapped onto the next paragraph by the extractor). Running
    headers and page numbers (short, no letters to speak of) are left out."""
    out: list[tuple[int, int, bool]] = []
    text = text or ""
    pos = 0
    pieces: list[tuple[int, int]] = []
    for m in _BLANK.finditer(text):
        pieces.append((pos, m.start()))
        pos = m.end()
    pieces.append((pos, len(text)))
    finer: list[tuple[int, int]] = []
    for a, b in pieces:
        para = text[a:b]
        cuts = [0] + [m.end() for m in _LINE_MARK.finditer(para)] + [len(para)]
        for i in range(len(cuts) - 1):
            line = para[cuts[i] : cuts[i + 1]]
            inline = [0] + [m.end() for m in _INLINE_MARK.finditer(line)] + [len(line)]
            for j in range(len(inline) - 1):
                finer.append((a + cuts[i] + inline[j], a + cuts[i] + inline[j + 1]))
    for a, b in finer:
        raw = text[a:b]
        lead = len(raw) - len(raw.lstrip())
        trail = len(raw) - len(raw.rstrip())
        a, b = a + lead, b - trail
        if b <= a:
            continue
        p = " ".join(_plain(text[a:b]).split())
        opens = bool(_MARK.match(p) or _LIST.match(p) or _author_run(p))
        letters = sum(ch.isalpha() for ch in p)
        if not opens and (len(p) < _MIN_ENTRY or letters < _MIN_ENTRY // 2):
            continue  # "**1948**", "Sensors 2014, 14": a running header
        out.append((a, b, opens))
    return out


def entries(text: str) -> list[str]:
    """The entries of a bibliography's text, each one line of plain text
    (``entry_spans``, a continuation joined to the entry before it)."""
    out: list[str] = []
    for a, b, opens in entry_spans(text):
        p = " ".join(_plain((text or "")[a:b]).split())
        if out and not opens:
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out


def _author_run(p: str) -> bool:
    """Does the paragraph open like an author list: "Surname, I." or
    "SURNAME, I. I.," or "Surname I," (Springer) or "Surname, I.;"."""
    return bool(_RUN.match(p[:80]))


def parse(entry: str) -> Reference:
    """One entry read: number, surnames, year, title, ids. The title is
    the first quoted span; else, in an author-year entry, the sentence
    after the year; else the sentence after the author run. Emphasis
    markers are stripped throughout (an italic venue is not a title
    unless nothing else offers)."""
    p = " ".join(_plain(entry or "").split())
    ref = Reference(raw=p)
    m = _MARK.match(p)
    if m:
        ref.number = int(m.group("n1") or m.group("n2") or m.group("n3"))
        p = p[m.end() :]
    elif _LIST.match(p):
        p = _LIST.sub("", p, count=1)
    d = _DOI.search(p)
    if d:
        ref.doi = d.group(1).rstrip(".").lower()
    a = _ARXIV.search(p)
    if a:
        ref.arxiv = a.group(1).lower()
    y = _YEAR.search(p)
    if y:
        ref.year = int(y.group(1))
    q = _QUOTED.search(p)
    plain = _EMPH.sub(r"\1", p)
    if q and len(_WORD.findall(q.group(1))) >= _MIN_TITLE_WORDS:
        ref.title = _clean_title(q.group(1))
        head = (
            plain[: plain.find(q.group(1)[:20])]
            if q.group(1)[:20] in plain
            else plain[: q.start()]
        )
    else:
        ref.title, head = _title_after_year_or_authors(plain)
    ref.surnames = _surnames(head)
    return ref


def _author_run_end(plain: str) -> int:
    """Where the author run ends: after the last "Surname, I." group and
    the separators between them, 0 when the entry does not open with one."""
    m = _RUN.match(plain)
    return m.end() if m else 0


def _title_after_year_or_authors(plain: str) -> tuple[str, str]:
    """The sentence after the author run (and the year that may follow it:
    "(2021) Title", "1994. Title"); else, in an author-year entry, the
    sentence after the year; else the first sentence that is not names
    and initials; else an emphasised span; else the longest sentence.
    Returns (title, the text before it)."""
    end = _author_run_end(plain)
    if end:
        rest = plain[end:]
        y = _YEAR_HEAD.match(rest)
        if y:
            rest = rest[y.end() :]
        sent = _first_sentence(rest)
        if sent and len(_WORD.findall(sent)) >= _MIN_TITLE_WORDS:
            return _clean_title(sent), plain[:end]
    m = re.search(r"\(\s*(?:19|20)\d{2}[a-z]?\s*\)\.?\s*", plain) or re.search(
        r"\s(?:19|20)\d{2}[a-z]?\.\s+", plain
    )
    if m:
        sent = _first_sentence(plain[m.end() :])
        if sent and len(_WORD.findall(sent)) >= _MIN_TITLE_WORDS:
            return _clean_title(sent), plain[: m.start()]
    pos = 0
    for sent, stop in _sentences(plain):
        if len(_WORD.findall(sent)) >= _MIN_TITLE_WORDS and not _looks_like_names(sent):
            return _clean_title(sent), plain[:pos]
        pos = stop
    e = _EMPH.search(plain)
    if e and len(_WORD.findall(e.group(1))) >= _MIN_TITLE_WORDS:
        return _clean_title(e.group(1)), plain[: e.start()]
    longest = max((s for s, _ in _sentences(plain)), key=len, default="")
    return _clean_title(longest), ""


def _sentences(text: str) -> list[tuple[str, int]]:
    """Sentences by ". " — but not after an initial ("J. C. Brown") or a
    common abbreviation. Each with the offset where it ends."""
    out: list[tuple[str, int]] = []
    start = 0
    for m in re.finditer(r"[.?!](?=\s)", text):
        before = text[max(0, m.start() - 3) : m.start() + 1]
        if re.search(r"(?:^|\s)[A-ZÀ-Ý]\.$", before) or re.search(
            r"(?:vol|no|pp|ed|eds|proc|jr|st|inc|cf|eg|ie)\.$", before, re.IGNORECASE
        ):
            continue
        stop = m.end() if m.group(0) in "?!" else m.start()  # a question keeps its mark
        sent = text[start:stop].strip(" ,;:")
        if sent:
            out.append((sent, m.end()))
        start = m.end()
    tail = text[start:].strip(" ,;:")
    if tail:
        out.append((tail, len(text)))
    return out


def _first_sentence(text: str) -> str:
    sents = _sentences(text)
    return sents[0][0] if sents else ""


def _looks_like_names(sent: str) -> bool:
    """A sentence of surnames, initials, "and", "et al." and years."""
    toks = sent.replace(",", " ").replace(";", " ").split()
    if not toks:
        return True
    namey = 0
    for t in toks:
        t2 = t.strip("().")
        if not t2:
            continue
        capitalised = t2[0].isupper() and (t2.isupper() or t2[1:].islower())
        namelike = _INITIAL.match(t2) or t2.lower() in _NOISE or t2.isdigit()
        if namelike or (capitalised and len(t2) <= 20):
            namey += 1
    return namey >= max(1, int(0.8 * len(toks)))


_EDITION = re.compile(
    r"\s*\((?:\d+(?:st|nd|rd|th)\s+ed\.?|ed\.|eds\.|vol\.?\s*\d+|[^)]*\bedition\b)[^)]*\)\s*$",
    re.IGNORECASE,
)


# a venue glued to the title after a comma (Elsevier: "Title, Phys. Rev. E
# 92 (2015)"; "Title, Ph.D. thesis"; "Title, Preprint"): the tail has a
# digit, or a venue word, or is short and capitalised
_VENUE_WORD = re.compile(
    r"\b(?:journal|j|proc|proceedings|trans|transactions|conference|conf|press|"
    r"preprint|thesis|dissertation|ph\.?d|master|report|lecture|notes|vol|"
    r"symposium|workshop|review|letters|magazine|arxiv|univ|university|"
    r"wiley|springer|elsevier|online|library|verlag)\b",
    re.IGNORECASE,
)


def _cut_venue(s: str) -> str:
    while ", " in s:
        head, tail = s.rsplit(", ", 1)
        words = tail.split()
        # a capitalised tail is a venue only behind a sentence-cased title
        # ("Title, Phys Rev"), not in a Title Case one ("VoxNet: An
        # Interactive, Rapidly-Deployable Acoustic Monitoring Platform")
        long_words = [w for w in head.split() if len(w) > 3]
        sentence_cased = long_words and sum(
            w[:1].isupper() for w in long_words
        ) < 0.5 * len(long_words)
        venueish = (
            any(ch.isdigit() for ch in tail)
            or _VENUE_WORD.search(tail)
            or (
                sentence_cased
                and len(words) <= 4
                and all(w[:1].isupper() for w in words)
            )
        )
        if venueish and len(_WORD.findall(head)) >= _MIN_TITLE_WORDS:
            s = head
        else:
            break
    return s


def _clean_title(s: str) -> str:
    s = _EMPH.sub(r"\1", s)
    s = re.sub(r"^\s*(?:in|In)\s*:\s*", "", s)
    s = _EDITION.sub("", s)  # "(2nd ed.)" is not part of the title
    s = _cut_venue(s.strip(' \t"“”„«»,.;:'))
    return s.strip(' \t"“”„«»,.;:').strip()


def _surnames(head: str) -> list[str]:
    """The surnames in the author run: capitalised words that are not
    initials, connectives or venue words; an ALL-CAPS surname is
    title-cased. At most twelve."""
    head = _EMPH.sub(r"\1", head)
    head = re.sub(r"\(\s*(?:19|20)\d{2}[a-z]?\s*\)", " ", head)
    out: list[str] = []
    for tok in re.split(r"[\s,;&]+", head):
        t = tok.strip('().:"“”')
        if len(t) < 2 or _INITIAL.match(t) or not t[0].isalpha():
            continue
        if t.lower() in _NOISE or not t[0].isupper():
            continue
        if "." in t and len(t.replace(".", "").replace("-", "")) <= 3:
            continue  # "P.V.E.", "X.-R."
        if t in ("Jr", "Sr", "II", "III", "IV"):
            continue
        if any(ch.isdigit() for ch in t):
            continue
        name = t.title() if t.isupper() else t
        if name not in out:
            out.append(name)
        if len(out) >= 12:
            break
    return out


def normalize_title(title: str) -> str:
    """Lower case, letters only, hyphens closed up ("non-negative" is
    "nonnegative"), a plural's s dropped."""
    text = (title or "").lower().replace("-", "").replace("‐", "")
    words = _WORD.findall(text)
    return " ".join(w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words)


def _content(normalised: str) -> list[str]:
    return [w for w in normalised.split() if len(w) > 2 and w not in _NOISE]


def similarity(
    ref: Reference,
    *,
    title: str,
    creators: list[str] | None = None,
    year: int | None = None,
) -> float:
    """How well a reference names a document, 0–1: the titles' similarity
    (a normalised sequence ratio, so an extractor's hyphenation or a
    subtitle costs little), raised by a shared surname and a year within
    one, lowered by a year two or more apart. A reference without a title
    scores 0."""
    a, b = normalize_title(ref.title), normalize_title(title)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    # one title holds the other whole, the rest a subtitle or a head
    # ("Title: A Matlab Companion" is another book; "State of the art
    # report: Title" is the paper): the extra part must sit behind a
    # colon or a dash in the longer, as the raw titles have it
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    raw_long = title if long_ is b else ref.title
    held = short != long_ and len(short.split()) >= 3 and _held_as_part(short, raw_long)
    if held:
        ratio = max(ratio, 0.86)
    # the shorter title's content words must nearly all be in the longer
    # ("3-D compact explicit" and "2-D interpolated" share the rest and
    # are two papers), and the longer may not open with words the shorter
    # lacks ("Shifted NMF…", "More than 50 years…" are other papers)
    # unless they sit before a colon
    ca, cb = _content(a), _content(b)
    small, big = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    coverage = len(set(small) & set(big)) / len(small) if small else 0.0
    other_head = bool(big) and big[0] not in set(small) and small != big
    capped = coverage < 0.85 or (other_head and not held)
    score = ratio
    if creators:
        names = " ".join(creators).lower()
        shared = sum(1 for s in ref.surnames if s.lower() in names)
        if shared:
            score += 0.08 * min(shared, 2)
        elif ref.surnames:
            score -= 0.12
    if year and ref.year:
        if abs(year - ref.year) <= 1:
            score += 0.04
        elif abs(year - ref.year) >= 2:
            score -= 0.2  # two years apart with both known: another paper
    if capped:
        score = min(score, 0.7)
    return max(0.0, min(1.0, score))


def _held_as_part(short: str, raw_long: str) -> bool:
    """Is ``short`` (normalised) one side of a colon, dash or bracket in
    the longer raw title — and a title, not a publisher's tag ("Wiley
    Online Library")?"""
    if _VENUE_WORD.search(short):
        return False
    for part in re.split(r"\s*(?::|\s[-–—]\s|\(|\)|\?)\s*", raw_long):
        if normalize_title(part) == short:
            return True
    return False


THRESHOLD = 0.82  # the score a match needs
MARGIN = 0.06  # two candidates closer than this: ambiguous


def match(
    scored: list[tuple[int, float]],
    *,
    threshold: float = THRESHOLD,
    margin: float = MARGIN,
) -> tuple[list[int], str]:
    """From (doc_id, score) pairs: the documents the reference names and
    how sure — one and "sure" (over the threshold, the runner-up a margin
    below), several and "ambiguous" (all over the threshold and within
    the margin of the best: twins of one paper in the library, or two
    papers under one title), none and "none"."""
    ranked = sorted(scored, key=lambda p: -p[1])
    if not ranked or ranked[0][1] < threshold:
        return [], "none"
    best = ranked[0][1]
    tied = [d for d, s in ranked if s >= threshold and best - s < margin]
    if len(tied) > 1:
        return tied, "ambiguous"
    return [ranked[0][0]], "sure"
