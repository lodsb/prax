"""The shape of a name for matching: two names are candidates for one thing
when their keys are equal (``normalize``). Case, accents, punctuation and a
name's suffixes (Jr., PhD) do not count, nor, for a thing rather than a
person, a trailing plural ``s``.

Entity resolution builds its tiers on it (``prax.graph.resolution``) and
the store's review lists read it (a merge that narrows a name); it
imports nothing of prax, like the rest of ``prax.text``.
"""

from __future__ import annotations

import re
import unicodedata

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "dr", "prof"}
_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def normalize(name: str, *, plural: bool = True) -> str:
    """Case-, accent- and punctuation-insensitive key; name suffixes dropped;
    with ``plural`` a trailing ``s`` on the last word is dropped too (for
    concepts and methods, never for people)."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _PUNCT.sub(" ", s.lower())
    words = [w for w in _SPACES.split(s) if w and w not in SUFFIXES]
    if plural and words and len(words[-1]) > 4 and words[-1].endswith("s"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


def words(name: str, *, plural: bool = True) -> list[str]:
    """``normalize``'s key as its words."""
    return [w for w in _SPACES.split(normalize(name, plural=plural)) if w]
