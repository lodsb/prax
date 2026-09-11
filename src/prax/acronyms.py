"""Acronyms the library defines for itself.

Papers introduce their acronyms in one shape: the phrase, then the letters
in parentheses ("antiderivative antialiasing (ADAA)", "hidden Markov model
(HMM)"). ``find`` collects those from a text and keeps only the ones whose
letters are the initials of the phrase's last words, so "for Computer
Supported Collaborative Music (CSCM)" yields ``cscm -> computer supported
collaborative music`` and not the leading "for". ``scripts/build_acronyms.py``
runs it over every text artifact and writes the ``acronyms`` table
(migration 0008), counting the documents behind each pairing; the store's
search expands a query token that is a known acronym on both the keyword
and the vector side (``store.expand_query``). This is why "adaa" finds the
papers that only ever write the phrase out, and why the embedding of a
query is not left to guess at letters it has never seen.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

DEFINITION = re.compile(
    r"((?:[A-Za-z][A-Za-z\-]*[a-z] ){1,6}[A-Za-z][A-Za-z\-]*[a-z])"
    r" \(([A-Z][A-Za-z0-9]{1,7})\)"
)
MIN_LETTERS = 2
MAX_LETTERS = 8
LEAD = {"the", "a", "an", "called", "so-called", "termed", "of", "for", "or", "and"}


def _initials(words: list[str]) -> str:
    return "".join(w[0] for w in words if w)


def find(text: str) -> set[tuple[str, str]]:
    """``{(acronym, expansion)}`` defined in ``text``, both lowercased."""
    out: set[tuple[str, str]] = set()
    for m in DEFINITION.finditer(text):
        phrase, acr = m.group(1), m.group(2)
        letters = re.sub(r"[^a-z]", "", acr.lower())
        if not MIN_LETTERS <= len(letters) <= MAX_LETTERS:
            continue
        words = [w for w in re.split(r"[ \-]+", phrase.lower()) if w]
        # the acronym is the initials of a suffix of the phrase; hyphenated
        # parts count as words ("anti-derivative anti aliasing" -> adaa)
        for start in range(len(words)):
            suffix = words[start:]
            if _initials(suffix) == letters:
                while suffix and suffix[0] in LEAD:
                    suffix = suffix[1:]
                if len(suffix) >= 2 or (suffix and len(suffix[0]) > len(letters)):
                    out.add((letters, " ".join(suffix)))
                break
    return out


def tally(texts: Iterable[str]) -> Counter[tuple[str, str]]:
    """Definitions across texts, counted once per text."""
    counts: Counter[tuple[str, str]] = Counter()
    for text in texts:
        counts.update(find(text))
    return counts
