"""Whose face this probably is (plan item AT step 5): each face matched
against every face a person has named, never against a mean per person,
so each face named is one more for the next to land near.

A face's ``k`` nearest named faces vote; the person with the most votes
(the nearer on a tie) is its match, with the similarity of their nearest
face and the margin to the nearest face of anyone else. A person may be
left out for a face (``excluded``): one a person said it is not, and one
already named in the same picture, since a picture rarely shows a
person twice.

Pure: numpy only, no import of prax. The work is a product of the faces
asked about and the named ones, taken in slices of about four million
numbers, so the memory it holds stays near 16 MB on any library.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

SLICE = 1 << 22  # similarities held at once


@dataclass(frozen=True)
class Match:
    person: int
    similarity: float  # cosine, to the person's nearest named face
    margin: float  # over the nearest named face of anyone else; 1.0 for none
    votes: int  # of the k nearest named faces
    like: int  # the row of that nearest face in ``named``


def unit(rows: np.ndarray) -> np.ndarray:
    """Rows scaled to length one (a zero row stays zero)."""
    rows = np.asarray(rows, dtype=np.float32)
    norm = np.linalg.norm(rows, axis=1, keepdims=True)
    return rows / np.where(norm == 0, 1.0, norm)


def nearest_people(
    named: np.ndarray,
    people: Sequence[int],
    queries: np.ndarray,
    *,
    k: int = 5,
    excluded: Mapping[int, set[int]] | None = None,
) -> list[Match | None]:
    """The match of each row of ``queries`` among the rows of ``named``
    (unit vectors; ``people[i]`` the person of named row ``i``), or None
    where no person is left. ``excluded`` maps a query's row to the
    people it may not be."""
    m = len(queries)
    if not m:
        return []
    if not len(named):
        return [None] * m
    who = np.asarray(people, dtype=np.int64)
    # named rows sorted by person, for each person's nearest at once
    order = np.argsort(who, kind="stable")
    who_sorted = who[order]
    persons, starts = np.unique(who_sorted, return_index=True)
    column = {int(p): i for i, p in enumerate(persons)}
    rows_of = np.split(np.arange(len(order)), starts[1:])
    named_sorted = np.asarray(named, dtype=np.float32)[order]
    excluded = excluded or {}
    step = max(1, SLICE // len(named))
    kk = min(k, len(named))
    out: list[Match | None] = []
    for lo in range(0, m, step):
        sims = np.asarray(queries[lo : lo + step], dtype=np.float32) @ named_sorted.T
        for i in range(len(sims)):
            for p in excluded.get(lo + i, ()):
                if p in column:
                    sims[i, rows_of[column[p]]] = -np.inf
        best = np.maximum.reduceat(sims, starts, axis=1)
        near = np.argpartition(-sims, kk - 1, axis=1)[:, :kk]
        for i in range(len(sims)):
            row = sims[i]
            votes: dict[int, int] = {}
            for j in near[i]:
                if np.isfinite(row[j]):
                    c = int(np.searchsorted(starts, j, side="right") - 1)
                    votes[c] = votes.get(c, 0) + 1
            if not votes:
                out.append(None)
                continue
            top = max(votes, key=lambda c: (votes[c], best[i, c]))
            others = np.delete(best[i], top)
            other = float(others.max()) if len(others) else -np.inf
            sim = float(best[i, top])
            j = int(rows_of[top][np.argmax(row[rows_of[top]])])
            out.append(
                Match(
                    person=int(persons[top]),
                    similarity=sim,
                    margin=sim - other if np.isfinite(other) else 1.0,
                    votes=votes[top],
                    like=int(order[j]),
                )
            )
    return out
