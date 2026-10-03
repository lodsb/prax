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
    s = name
    if not s.isascii():  # ASCII has no accents to take off: the same key
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _PUNCT.sub(" ", s.lower())
    words = [w for w in _SPACES.split(s) if w and w not in SUFFIXES]
    if plural and words and len(words[-1]) > 4 and words[-1].endswith("s"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


def words(name: str, *, plural: bool = True) -> list[str]:
    """``normalize``'s key as its words."""
    return [w for w in _SPACES.split(normalize(name, plural=plural)) if w]


# -------------------------------------------------- names written nearly alike
# A name written two ways that normalizing does not join: a paper cited as
# "…: an overview" and "… - an overview", "onWeb" and "on Web", a word
# missing an article. Character 3-grams, MinHash with locality-sensitive
# hashing to find the pairs worth comparing, then their Jaccard. Graphiti
# merges at 0.9 outright; here a pair is only proposed, and never when the
# numbers in the two names differ: the 17th and the 20th ISMIR, ICASSP 2012
# and 2018 and "Part 2" are other things (2026-10-03, on the library).

NEAR_JACCARD = 0.9
NEAR_MIN_CHARS = 6  # a short name's 3-grams say too little
_PERMS, _BANDS = 32, 8
_DIGITS = re.compile(r"\d+")


def _shingles(s: str) -> set[str]:
    s = f" {s} "
    return {s[i : i + 3] for i in range(len(s) - 2)}


def same_numbers(a: str, b: str) -> bool:
    """Whether two names hold the same numbers: an edition, a year, a part
    or a version written in one and not the other makes them two things."""
    return set(_DIGITS.findall(normalize(a))) == set(_DIGITS.findall(normalize(b)))


def near_pairs(
    items: list[tuple[int, str]], *, threshold: float = NEAR_JACCARD
) -> list[tuple[int, int, float]]:
    """``(id, id, jaccard)``, the smaller id first, highest first: the
    names written nearly alike (3-gram Jaccard of ``threshold`` or more)
    that are not equal once normalized and hold the same numbers. A few
    seconds for ten thousand names."""
    import hashlib

    keys = [i.to_bytes(4, "little") for i in range(_PERMS)]
    rows = _PERMS // _BANDS
    norm: dict[int, str] = {}
    grams: dict[int, set[str]] = {}
    buckets: dict[tuple[int, tuple[int, ...]], list[int]] = {}
    for ident, name in items:
        n = normalize(name)
        if len(n) < NEAR_MIN_CHARS:
            continue
        norm[ident] = n
        grams[ident] = _shingles(n)
        coded = [g.encode() for g in grams[ident]]
        signature = [
            min(
                int.from_bytes(
                    hashlib.blake2b(g, digest_size=8, key=k).digest(), "little"
                )
                for g in coded
            )
            for k in keys
        ]
        for b in range(_BANDS):
            buckets.setdefault(
                (b, tuple(signature[b * rows : (b + 1) * rows])), []
            ).append(ident)
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int, float]] = []
    for members in buckets.values():
        if len(members) < 2 or len(members) > 50:  # a crowded bucket is noise
            continue
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                a, b = sorted((members[x], members[y]))
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                if norm[a] == norm[b]:
                    continue  # equal once normalized: the sure tier's
                if set(_DIGITS.findall(norm[a])) != set(_DIGITS.findall(norm[b])):
                    continue  # another edition, year or part
                j = len(grams[a] & grams[b]) / len(grams[a] | grams[b])
                if j >= threshold:
                    out.append((a, b, round(j, 4)))
    return sorted(out, key=lambda t: -t[2])
