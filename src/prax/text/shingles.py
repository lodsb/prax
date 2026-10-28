"""How much of one text another says, word for word, after an edit: the
overlap of their runs of ``SHINGLE`` words (Broder's shingles), which an
edit changes only where it falls. Chunk hashes, which find a copy (heal's
``twin-documents``), change wholly with one word of a chunk, and with a
chunker's other cut: of 1,067 pairs of one title (2026-10-28), 53 shared
under a tenth of their chunks and over seven tenths of their shingles.

Pure: imports nothing of prax.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

SHINGLE = 5  # words in a run
_WORD = re.compile(r"\w+")


def shingles(texts: Iterable[str], n: int = SHINGLE) -> frozenset[int]:
    """The runs of ``n`` words of the texts read as one, lower-cased, each
    as 8 bytes of its blake2b: the same numbers in every process."""
    words = _WORD.findall(" ".join(texts).lower())
    # a text shorter than one run is one run
    runs = (
        [words]
        if 0 < len(words) < n
        else [words[i : i + n] for i in range(len(words) - n + 1)]
    )
    return frozenset(
        int.from_bytes(
            hashlib.blake2b(" ".join(r).encode(), digest_size=8).digest(), "big"
        )
        for r in runs
    )


def overlap(a: frozenset[int], b: frozenset[int]) -> float:
    """Jaccard similarity of two shingle sets; 0.0 when either is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
