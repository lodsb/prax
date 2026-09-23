"""What language a text is in.

The library is a fifth German and nothing recorded it
(`docs/research-multilingual-2026-09-23.md`), so retrieval, extraction
and the UI all had to guess. A document's language is one field in
``meta``; this module is what fills it.

Two detectors, in order. ``py3langid`` when it is installed — pure
Python over a naive-Bayes model of 97 languages, a few milliseconds for
a page, and restricted here to the languages a host says it expects
(``parse.languages`` in ``prax.yaml``), which is what makes a short text
reliable. Otherwise a stopword count over the same sample, which knows
the six languages this library holds and says nothing when it is not
sure. Neither is asked to be certain: ``detect`` returns ``None`` where
the text is too short or the counts too close, and a missing language is
always allowed.

The sample is the head of the text and never the whole of it: a page is
as much evidence as a book, and a book is 2 MB.
"""

from __future__ import annotations

import re

SAMPLE = 4000  # characters read from the head of a text
MIN_WORDS = 20  # fewer than this and no guess is made
MARGIN = 1.25  # how far the leader must be ahead of the runner-up
MIN_PROBABILITY = 0.5  # under this the model is guessing

# what the library holds (docs/research-multilingual-2026-09-23.md): the
# fallback knows these, and a host may narrow the detector to them
LIKELY = ("en", "de", "fr", "es", "it", "nl")
NAMES = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pt": "Portuguese",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "pl": "Polish",
    "cs": "Czech",
    "ru": "Russian",
    "tr": "Turkish",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "ar": "Arabic",
    "he": "Hebrew",
    "el": "Greek",
    "la": "Latin",
}
# the commonest words of each, which no document of that language avoids
_WORDS = {
    "en": "the and is not of to in for on with that this by are as from at it be"
    " we can which has have was will or their its been more than",
    "de": "der die das und ist nicht ein eine einer den dem des mit für auf von"
    " im zu aus wird werden sich auch wenn oder als kann nach bei über um durch"
    " diese dieser wie sind hat haben war beim zum zur",
    "fr": "le la les des une est pour dans qui avec sur pas plus par nous vous"
    " leur cette sont été être fait comme mais ou",
    "es": "el la los las una es por para con que del se su como más pero sus"
    " este esta son han sido entre cuando",
    "it": "il lo la gli le una è per con che del della nel sono come anche più"
    " questo questa sono stato essere",
    "nl": "de het een en van is dat niet op voor met aan zijn worden wordt deze"
    " door ook maar als naar bij uit",
}
_STOPWORDS = {code: frozenset(words.split()) for code, words in _WORDS.items()}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def name(code: str | None) -> str:
    """The language under a name a reader knows, the code otherwise."""
    if not code:
        return ""
    return NAMES.get(code, code)


def _likely() -> tuple[str, ...]:
    """The languages this host expects, from ``parse.languages``."""
    from prax import config

    chosen = config.setting("parse.languages")
    if isinstance(chosen, str):
        chosen = [x.strip() for x in chosen.split(",") if x.strip()]
    if isinstance(chosen, list) and chosen:
        return tuple(str(x) for x in chosen)
    return LIKELY


def _by_stopwords(words: list[str]) -> str | None:
    counts = {code: sum(w in stop for w in words) for code, stop in _STOPWORDS.items()}
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < max(3, len(words) * 0.02):  # not enough of anything
        return None
    if second and top < second * MARGIN:  # two too close to call
        return None
    return best


_narrowed = False  # py3langid keeps one identifier; it is told once


def _by_model(sample: str) -> str | None:
    global _narrowed
    try:
        import py3langid
    except ImportError:  # the fallback knows this library's six languages
        return None
    if not _narrowed:
        # the languages the host expects: a naive-Bayes model asked for 97
        # of them will find Tagalog in a page of German
        py3langid.set_languages(list(_likely()))
        _narrowed = True
    code, probability = py3langid.classify(sample)
    return str(code) if probability >= MIN_PROBABILITY else None


def detect(text: str | None) -> str | None:
    """The language of a text as an ISO 639-1 code, or None when the text
    is too short to tell or the evidence is split."""
    if not text:
        return None
    sample = text[:SAMPLE]
    words = [w.lower() for w in _WORD.findall(sample)]
    if len(words) < MIN_WORDS:
        return None
    return _by_model(sample) or _by_stopwords(words)
