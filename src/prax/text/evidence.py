"""Whether a quote can carry the fact it is the evidence of (AV, the BWS
survey of 2026-11-17): the shapes of a quote that cannot, read from the
quote alone, without a model.

- ``contents_line``: a line of a table of contents or an index ("5.1 FM
  synthesis ..... 257", "reverb, 12, 45, 101"). It says a book has a
  chapter on something, which is evidence of what the book is about and
  of nothing it asserts. ``contents_share`` is the same shape over a
  block: the share of its words that are page numbers, which a reading in
  full reads to leave an index out (``prax.graph.parts``).
- ``negated``: a negation next to an end of the edge ("this technique is
  unrelated to wavetable synthesis" as the evidence of ``uses wavetable
  synthesis``). For a relation that asserts something (``uses``,
  ``implements``), it names a fact that may be turned round; for a
  topical one (``about``, ``contrasts``) a negation changes nothing.
- ``commentary``: the model's own note where the quote belongs ("Note:
  Title inferred from context", "the text only says ..."). It is never
  the document's words, for any relation.
- ``unnamed``: a quote that does not name the target of a relation a
  model fills in from what it knows ("Parker uses diminution" as the
  evidence of ``Charlie Parker plays saxophone``). Only the relations
  the lexicon lists as ``named``.
- ``noise``: OCR that came out as no language. Weak: code, formulas and
  file names look like noise to it too, so it is reported and never
  refuses.

Which relations are topical and which words negate are the lexicon's
(``ontology/lexicon.yaml``, section ``evidence``), passed in as ``Cues``.
Whether the quote is in its document at all is ``prax.text.quotes``.
Imports nothing of prax.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# a contents line: leaders or wide spaces and a page number at the end,
# or a section number at the start and a page number at the end
_LEADERS = re.compile(r"(?:\.\s?){3,}\s*\d{1,4}\s*$|…\s*\d{1,4}\s*$")
_NUMBERED = re.compile(
    r"^\s*(?:\d{1,2}(?:\.\d{1,2}){0,3}|[IVX]{1,5}\.?|[A-Z]\.)\s+\S.*?\s\d{1,4}\s*$"
)
# an index line: a term and a run of page numbers
_INDEX = re.compile(
    r"^[^\d,;]{2,60},\s*\d{1,4}(?:\s*[-–]\s*\d{1,4})?"
    r"(?:\s*,\s*\d{1,4}(?:\s*[-–]\s*\d{1,4})?){1,}\s*\.?$"
)

_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)
_TOKEN = re.compile(r"\S+")
_VOWELS = set("aeiouyäöüàâéèêëîïôûùáíóúåæøœ")
NOISE_SHARE = 0.5  # under this share of word-like tokens, a quote is noise
NOISE_MIN_TOKENS = 4  # a shorter quote is too short to judge
NEGATION_REACH = 4  # words before an end a negation is read in
# what a PDF's text layer leaves of an accent beside its letter (Kl¨ugel,
# Ren´ee): a broken word, not noise
_LOOSE_ACCENTS = str.maketrans("", "", "¨´`˜^¸")
_MARKUP = "*_|`#~"
_PUNCT = ".,;:!?()[]{}\"'«»„“”‚‘’…"
# what a negation followed by these adds rather than denies: "not only"
_ADDING = ("only", "just", "merely", "least")


@dataclass(frozen=True)
class Cues:
    """The lexicon's words: what negates, the relations a negation or a
    contents line does not speak against, those whose quote must name
    their target, and those a model's doubt does not speak against while
    their quote names the target (``stands_unsure``)."""

    negations: tuple[str, ...] = ()
    topical: frozenset[str] = field(default_factory=frozenset)
    commentary: tuple[str, ...] = ()
    named: frozenset[str] = field(default_factory=frozenset)
    sure_when_named: frozenset[str] = field(default_factory=frozenset)

    def commented(self) -> re.Pattern[str] | None:
        if not self.commentary:
            return None
        words = [re.escape(w).replace(r"\ ", r"\s+") for w in self.commentary]
        return re.compile("|".join(words), re.IGNORECASE)

    def negation(self) -> re.Pattern[str] | None:
        if not self.negations:
            return None
        words = [re.escape(w).replace(r"\ ", r"\s+") for w in self.negations]
        inside = [w for w in words if not w[0].isalpha()]  # n't: inside a word
        whole = [w for w in words if w[0].isalpha()]
        parts = []
        if whole:
            parts.append(r"\b(?:" + "|".join(whole) + r")\b")
        if inside:
            parts.append(r"(?:" + "|".join(inside) + r")\b")
        adding = "|".join(_ADDING)
        return re.compile(
            "(?:" + "|".join(parts) + rf")(?!\s+(?:{adding})\b)", re.IGNORECASE
        )


_PAGE_NUMBER = re.compile(r"^\(?\d{1,4}(?:\s*[-–]\s*\d{1,4})?[,;.)]?$")
CONTENTS_MIN_WORDS = 30  # a shorter block is too short to judge


def contents_share(text: str, *, min_words: int = CONTENTS_MIN_WORDS) -> float:
    """The share of a block's words that are page numbers ("106–10,",
    "43", "(12)"): an index or a contents page is a third or more of them,
    prose a tenth or less. A block under ``min_words`` words is 0."""
    words = text.split()
    if not words or len(words) < min_words:
        return 0.0
    return sum(bool(_PAGE_NUMBER.match(w)) for w in words) / len(words)


def contents_line(quote: str) -> bool:
    """A line of a table of contents or an index."""
    line = " ".join(quote.split())
    if not line:
        return False
    return bool(_LEADERS.search(line) or _NUMBERED.match(line) or _INDEX.match(line))


def _latin(token: str) -> bool:
    return all(ord(c) < 0x250 for c in token)


def _wordlike(token: str) -> bool:
    """Letters only, of a word's length; a Latin word past three letters
    with a vowel. A replacement character (U+FFFD) is never a word: it is
    what a broken encoding leaves."""
    if "�" in token:
        return False
    token = token.translate(_LOOSE_ACCENTS).strip(_MARKUP)
    words = _WORD.findall(token)
    if len(words) != 1 or words[0] != token or len(token) > 24:
        return False  # letters glued to digits ("sdnoa6"), or no word at all
    if len(token) <= 3 or not _latin(token):
        return True  # Arabic, Greek, Cyrillic, CJK: letters are enough
    return any(c in _VOWELS for c in token.lower())


def noise(quote: str) -> bool:
    """OCR that is no language: fewer than ``NOISE_SHARE`` of its tokens
    look like words (``_wordlike``), in a quote of at least
    ``NOISE_MIN_TOKENS``. A number counts neither way (a page, a value),
    and a quote with ``$`` or ``=`` (maths, code) is left alone. A word
    list would catch more; this needs none, and says nothing of a garble
    made of short words."""
    if "$" in quote or "=" in quote:
        return False
    tokens = [t.strip(_PUNCT) for t in _TOKEN.findall(quote)]
    real = [t for t in tokens if t and not re.fullmatch(r"[\d.,%/+-]+", t)]
    if len(real) < NOISE_MIN_TOKENS:
        return False
    return sum(1 for t in real if _wordlike(t)) / len(real) < NOISE_SHARE


def negated(quote: str, src: str, dst: str, cues: Cues) -> bool:
    """A negation within ``NEGATION_REACH`` words before the place the
    quote names an end of the edge ("unrelated to wavetable synthesis").
    An end the quote does not name is not looked for."""
    pattern = cues.negation()
    if pattern is None:
        return False
    low = quote.lower()
    for name in (dst, src):
        key = name.strip().lower()
        if len(key) < 3:
            continue
        at = low.find(key)
        while at >= 0:
            before = _TOKEN.findall(low[:at])[-NEGATION_REACH:]
            if pattern.search(" ".join(before)):
                return True
            at = low.find(key, at + 1)
    return False


def _stem(word: str) -> str:
    """A word without the ending an inflection or a derivation adds:
    "saxophone" is "saxoph", which "saxophonist" begins with too."""
    return word if len(word) <= 4 else word[: max(4, len(word) - 3)]


def names(quote: str, name: str) -> bool:
    """Whether the quote names the thing: a word of the quote begins with
    the stem of a word of the name ("bass line by Jaco Pastorius" names
    the electric bass). A name with no word of three letters is taken as
    named, since nothing in it can be looked for."""
    stems = [_stem(w) for w in _WORD.findall(name.lower()) if len(w) >= 3]
    if not stems:
        return True
    words = _WORD.findall(quote.lower())
    return any(w.startswith(s) for s in stems for w in words)


def stands_unsure(quote: str | None, rel: str, dst: str, cues: Cues) -> bool:
    """Whether a triple its model called AMBIGUOUS stands all the same: a
    relation of ``cues.sure_when_named`` whose quote names the target. A
    paper's in-text citation is the case ("Huron (1989a) showed that…"):
    the model doubts what kind of thing the cited work is, not that the
    text cites it."""
    return rel in cues.sure_when_named and names(quote or "", dst)


# what an extraction sends to the review queue rather than writing: the
# three that are rarely wrong (measured 2026-11-17, docs/log.md), and a
# target the quote does not name behind a relation that needs it named
# (2026-11-26); noise is reported only
REFUSED = ("commentary", "contents", "negated", "unnamed")
# how such a review item's reason opens: the typing rules leave it to a
# person, since a rule that settles a type would write the fact anyway
REASON = "the quote is no evidence"


def flags(quote: str | None, src: str, rel: str, dst: str, cues: Cues) -> list[str]:
    """What is wrong with a quote as the evidence of ``src rel dst``, as
    the words heal counts by: ``contents`` and ``negated`` for a relation
    that asserts something, ``commentary`` and ``noise`` for any,
    ``unnamed`` for a relation of the cues' ``named``; empty when nothing
    is."""
    if not quote or not quote.strip():
        return []
    out = []
    note = cues.commented()
    if note is not None and note.search(quote):
        out.append("commentary")
    asserts = rel not in cues.topical
    if asserts and contents_line(quote):
        out.append("contents")
    if asserts and negated(quote, src, dst, cues):
        out.append("negated")
    if rel in cues.named and not names(quote, dst):
        out.append("unnamed")
    if noise(quote):
        out.append("noise")
    return out
