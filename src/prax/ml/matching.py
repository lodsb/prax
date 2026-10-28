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
    persons, starts, counts = np.unique(
        who[order], return_index=True, return_counts=True
    )
    column_of = np.repeat(np.arange(len(persons)), counts)  # sorted row -> person
    column = {int(p): i for i, p in enumerate(persons)}
    named_sorted = np.asarray(named, dtype=np.float32)[order]
    excluded = excluded or {}
    step = max(1, SLICE // len(named))
    kk = min(k, len(named))
    out: list[Match | None] = []
    for lo in range(0, m, step):
        sims = np.asarray(queries[lo : lo + step], dtype=np.float32) @ named_sorted.T
        n = len(sims)
        for i, ps in excluded.items():
            if lo <= i < lo + n:
                for p in ps:
                    if p in column:
                        c = column[p]
                        sims[i - lo, starts[c] : starts[c] + counts[c]] = -np.inf
        best = np.maximum.reduceat(sims, starts, axis=1)
        near = np.argpartition(-sims, kk - 1, axis=1)[:, :kk]
        alive = np.isfinite(np.take_along_axis(sims, near, axis=1))
        votes = np.zeros((n, len(persons)), dtype=np.int64)
        np.add.at(
            votes, (np.repeat(np.arange(n), kk), column_of[near].ravel()), alive.ravel()
        )
        most = votes.max(axis=1)
        # the most votes, the nearer face on a tie
        top = np.argmax(np.where(votes == most[:, None], best, -np.inf), axis=1)
        rows = np.arange(n)
        sim = best[rows, top]
        others = best.copy()
        others[rows, top] = -np.inf
        other = others.max(axis=1)
        mine = np.where(column_of[None, :] == top[:, None], sims, -np.inf)
        like = order[np.argmax(mine, axis=1)]
        for i in range(n):
            if most[i] == 0:
                out.append(None)
                continue
            out.append(
                Match(
                    person=int(persons[top[i]]),
                    similarity=float(sim[i]),
                    margin=float(sim[i] - other[i]) if np.isfinite(other[i]) else 1.0,
                    votes=int(most[i]),
                    like=int(like[i]),
                )
            )
    return out
