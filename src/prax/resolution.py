"""Entity resolution: find entities that name the same thing and merge them.

Two tiers, both recorded through ``store.merge_entities`` (``canonical_id``,
nothing deleted; ``traverse`` follows the pointers):

* **sure**: two entities of one type whose normalized names are equal
  (case, punctuation, diacritics, whitespace, a trailing plural ``s``,
  name suffixes such as ``Jr``/``III``), or, for authors, where one name
  is an initials form of the other (``J. O. Smith`` and ``Julius O. Smith``).
  These merge automatically.
* **likely**: same type, close by embedding similarity of the names
  (``bge-small`` on short strings) but not equal after normalization. These
  go to an adjudicator, which by default merges nothing; ``ClaudeAdjudicator``
  asks the model, ``StubAdjudicator`` accepts above a similarity threshold
  in tests.

The survivor of a merge is the entity with more currently valid edges,
then the longer name (usually the fuller one).
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from prax import embeddings, extraction, store

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "dr", "prof"}
LIKELY_THRESHOLD = 0.92  # cosine of name embeddings to become a candidate
# Types whose names are descriptive enough for embedding similarity to mean
# "the same thing". Author names embed poorly (initials handle them); paper
# titles and claims that differ by a part number or a qualifier embed almost
# identically while naming different things, so they are never candidates.
LIKELY_TYPES = frozenset({"concept", "method", "tool", "dataset", "venue"})
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


def initials_form(name: str) -> tuple[str, ...] | None:
    """``("j", "o", "smith")`` for an author name: initials of the given
    names plus the full last name, or None when there is no given name."""
    words = [w for w in _SPACES.split(normalize(name, plural=False)) if w]
    if len(words) < 2:
        return None
    return tuple([w[0] for w in words[:-1]] + [words[-1]])


@dataclass
class Candidate:
    keep: int  # survivor id
    drop: int  # duplicate id
    keep_name: str
    drop_name: str
    type: str
    tier: str  # sure | likely
    score: float  # 1.0 for sure, cosine similarity for likely


@dataclass
class Plan:
    sure: list[Candidate] = field(default_factory=list)
    likely: list[Candidate] = field(default_factory=list)
    twins: list[Candidate] = field(default_factory=list)  # concept + method, one name


# A technique extracted as both a concept and a method (the v1 prompt let
# that happen; "empirical mode decomposition" had 47 and 103 edges): the
# ontology says a technique is a method, so the concept merges into it.
TWIN_TYPES = ("concept", "method")


def _entities(con: sqlite3.Connection, etype: str | None) -> list[dict[str, Any]]:
    where = "WHERE canonical_id IS NULL" + (" AND type = ?" if etype else "")
    args: tuple[Any, ...] = (etype,) if etype else ()
    degree: dict[int, int] = defaultdict(int)
    for col in ("src", "dst"):  # two indexed group-bys instead of an OR per entity
        for r in con.execute(
            f"SELECT {col} AS id, count(*) AS n FROM edges WHERE valid_to IS NULL"
            f" GROUP BY {col}"
        ):
            degree[r["id"]] += r["n"]
    rows = con.execute(
        f"SELECT e.id, e.name, e.type FROM entities e {where} ORDER BY e.id", args
    ).fetchall()
    return [{**dict(r), "degree": degree.get(r["id"], 0)} for r in rows]


def _rank(e: dict[str, Any]) -> tuple[int, int, int, int]:
    """Higher survives: more edges, then a properly cased name (capitals),
    then the shorter one (a bare suffix or a typo tends to be longer), then
    the older id."""
    capitals = sum(ch.isupper() for ch in e["name"])
    return (e["degree"], capitals, -len(e["name"]), -e["id"])


def _survivor(
    a: dict[str, Any], b: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (a, b) if _rank(a) >= _rank(b) else (b, a)


def plan(
    con: sqlite3.Connection,
    *,
    etype: str | None = None,
    embed: bool = True,
    likely_threshold: float = LIKELY_THRESHOLD,
) -> Plan:
    """Candidate merges among unmerged entities, without writing anything."""
    ents = _entities(con, etype)
    out = Plan()
    taken: set[int] = set()

    # tier 1a: equal normalized names within a type
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in ents:
        key = normalize(e["name"], plural=e["type"] != "author")
        groups[(e["type"], key)].append(e)
    for (t, _), members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=_rank, reverse=True)
        keep = members[0]
        for drop in members[1:]:
            out.sure.append(
                Candidate(
                    keep["id"], drop["id"], keep["name"], drop["name"], t, "sure", 1.0
                )
            )
            taken.add(drop["id"])

    # tier 1b: authors by initials form (J. O. Smith == Julius O. Smith)
    by_initials: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for e in ents:
        if e["type"] != "author" or e["id"] in taken:
            continue
        form = initials_form(e["name"])
        if form:
            by_initials[form].append(e)
    for form, members in by_initials.items():
        if len(members) < 2:
            continue
        # only merge when one member is an abbreviation of another, i.e. the
        # normalized names differ only by given names being initials
        full = [m for m in members if not _is_initials_only(m["name"])]
        short = [m for m in members if _is_initials_only(m["name"])]
        if len(full) != 1 or not short:
            continue  # two different full names sharing initials: not sure
        keep = full[0]  # the full name survives regardless of edge counts
        for drop in short:
            out.sure.append(
                Candidate(
                    keep["id"],
                    drop["id"],
                    keep["name"],
                    drop["name"],
                    "author",
                    "sure",
                    1.0,
                )
            )
            taken.add(drop["id"])

    # tier 1c: the same name as a concept and as a method; the method survives
    if etype is None or etype in TWIN_TYPES:
        by_key: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for e in ents:
            if e["type"] in TWIN_TYPES and e["id"] not in taken:
                by_key[normalize(e["name"])].setdefault(e["type"], e)
        for members in by_key.values():
            if len(members) == 2:
                keep, drop = members["method"], members["concept"]
                out.twins.append(
                    Candidate(
                        keep["id"],
                        drop["id"],
                        keep["name"],
                        drop["name"],
                        "method",
                        "twins",
                        1.0,
                    )
                )
                taken.add(drop["id"])

    # tier 2: close by name embedding, same type, not already taken
    if embed and (emb := embeddings.current()) is not None:
        rest = [e for e in ents if e["id"] not in taken]
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in rest:
            by_type[e["type"]].append(e)
        for t, members in by_type.items():
            if len(members) < 2 or t not in LIKELY_TYPES:
                continue
            vectors = emb.embed([m["name"] for m in members])
            sims = vectors @ vectors.T
            np.fill_diagonal(sims, 0.0)
            seen: set[tuple[int, int]] = set()
            for i, j in zip(*np.where(sims >= likely_threshold), strict=True):
                a, b = members[int(i)], members[int(j)]
                pair = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                if pair in seen:
                    continue
                seen.add(pair)
                keep, drop = _survivor(a, b)
                out.likely.append(
                    Candidate(
                        keep["id"],
                        drop["id"],
                        keep["name"],
                        drop["name"],
                        t,
                        "likely",
                        float(sims[i, j]),
                    )
                )
    out.likely.sort(key=lambda c: -c.score)
    return out


def _is_initials_only(name: str) -> bool:
    words = [w for w in _SPACES.split(normalize(name)) if w]
    return len(words) >= 2 and all(len(w) == 1 for w in words[:-1])


# ------------------------------------------------------------ adjudication


class Adjudicator(Protocol):
    name: str

    def decide(self, candidates: list[Candidate]) -> list[bool]: ...


@dataclass
class NoAdjudicator:
    name: str = "none"

    def decide(self, candidates: list[Candidate]) -> list[bool]:
        return [False] * len(candidates)


@dataclass
class StubAdjudicator:
    """Accepts a candidate above a similarity threshold; for tests."""

    threshold: float = 0.97
    name: str = "stub"

    def decide(self, candidates: list[Candidate]) -> list[bool]:
        return [c.score >= self.threshold for c in candidates]


@dataclass
class ClaudeAdjudicator:
    """Asks Claude whether each pair names the same thing, in one call per
    batch of candidates, with a structured yes/no list."""

    model: str = "claude-opus-5"
    client: Any = None
    batch: int = 40

    @property
    def name(self) -> str:
        return self.model

    def decide(self, candidates: list[Candidate]) -> list[bool]:
        if self.client is None:
            import anthropic

            self.client = anthropic.Anthropic()
        out: list[bool] = []
        for start in range(0, len(candidates), self.batch):
            part = candidates[start : start + self.batch]
            lines = "\n".join(
                f'{i}. [{c.type}] "{c.keep_name}"  vs  "{c.drop_name}"'
                for i, c in enumerate(part)
            )
            output_config: dict[str, Any] = {
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "same": {"type": "array", "items": {"type": "boolean"}}
                        },
                        "required": ["same"],
                        "additionalProperties": False,
                    },
                }
            }
            if extraction.supports_effort(self.model):
                output_config["effort"] = "low"
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                output_config=output_config,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "For each numbered pair below, decide whether the two"
                            " names refer to the same entity in a research library"
                            " about audio and signal processing. Spelling variants,"
                            " abbreviations and singular/plural are the same thing;"
                            " different methods, people or concepts are not. Answer"
                            " with a boolean per pair, in order.\n\n" + lines
                        ),
                    }
                ],
            )
            text = next(b.text for b in response.content if b.type == "text")
            same = json.loads(text)["same"]
            same = list(same)[: len(part)] + [False] * (len(part) - len(same))
            out.extend(bool(x) for x in same)
        return out


# ------------------------------------------------------------------ apply


@dataclass
class Report:
    merged_sure: int = 0
    merged_likely: int = 0
    merged_twins: int = 0
    declined: int = 0


def apply(
    con: sqlite3.Connection,
    p: Plan,
    *,
    adjudicator: Adjudicator | None = None,
    twins: bool = False,
) -> Report:
    """Merge every sure candidate, the concept/method twins when asked,
    and ask the adjudicator about the likely ones. Idempotent: a re-run
    finds nothing left to merge."""
    report = Report()
    for c in p.sure:
        store.merge_entities(con, c.drop, c.keep)
        report.merged_sure += 1
    if twins:
        for c in p.twins:
            store.merge_entities(con, c.drop, c.keep, across_types=True)
            report.merged_twins += 1
    if p.likely:
        decisions = (adjudicator or NoAdjudicator()).decide(p.likely)
        for c, yes in zip(p.likely, decisions, strict=True):
            if yes:
                try:
                    store.merge_entities(con, c.drop, c.keep)
                except ValueError:
                    continue  # already merged the other way this round
                report.merged_likely += 1
            else:
                report.declined += 1
    return report
