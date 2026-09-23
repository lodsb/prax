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
  in tests. The embedding is never the door's: a worker takes a type's
  names through the work protocol (``prax.work``, step ``resolve``), finds
  the close pairs (``likely_pairs``) and posts them; the door keeps them
  (``store.entity_candidates``) and the plan reads them from there.
  Embedding 138,000 names inside the serving process, and the N² of
  similarities after, crashed the door on 2026-09-17 (invariant 7).

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
from datetime import UTC, datetime
from typing import Any, Protocol

import numpy as np

from prax import extraction, ontology, store

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "dr", "prof"}
PRODUCER = "resolution"  # who signs a merge, for retiring a bad round
LIKELY_THRESHOLD = 0.92  # cosine of name embeddings to become a candidate
LIKELY_BLOCK = 2048  # names per block of the similarity computation (a worker)
LIKELY_DAYS = 7  # a type's pairs are computed again after this long
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
    subtypes: list[Candidate] = field(default_factory=list)  # a person who is an author


# The store's own kinds of document. A page is written here and a project
# is declared here; neither is ever the extractor reaching for a general
# type, so neither takes part in the subtype fold.
SELF_KINDS = frozenset({"page", "project"})


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


def likely_pairs(
    names: list[tuple[int, str]],
    emb: Any,
    *,
    threshold: float = LIKELY_THRESHOLD,
    block: int = LIKELY_BLOCK,
) -> list[tuple[int, int, float]]:
    """The pairs of names close by embedding: ``(id, id, cosine)`` with
    the smaller id first, highest first. Done a block of rows at a time
    against all the columns, so thirty thousand names cost a few hundred
    megabytes and not the square; the work step of a worker, never the
    door's."""
    if len(names) < 2:
        return []
    vectors = np.asarray(emb.embed([n for _, n in names]), dtype=np.float32)
    ids = [i for i, _ in names]
    found: dict[tuple[int, int], float] = {}
    for start in range(0, len(names), block):
        sims = vectors[start : start + block] @ vectors.T
        for r, c in zip(*np.where(sims >= threshold), strict=True):
            i, j = int(start + r), int(c)
            if i == j:
                continue
            a, b = (ids[i], ids[j]) if ids[i] < ids[j] else (ids[j], ids[i])
            found[(a, b)] = max(found.get((a, b), 0.0), float(sims[r, c]))
    return sorted(((a, b, s) for (a, b), s in found.items()), key=lambda t: -t[2])


def plan(
    con: sqlite3.Connection,
    *,
    etype: str | None = None,
    likely: bool = True,
) -> Plan:
    """Candidate merges among unmerged entities, without writing anything:
    the sure ones and the twins from the names, the likely ones from the
    pairs a worker left (``store.entity_candidates``; the threshold was
    the worker's, handed out with the names)."""
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

    # tier 1c: one name under a type and its subtype. The ontology says an
    # author is a person, a paper is a document, a venue is an organization;
    # the extractor reaches for the general type when a document does not
    # make the specific one plain, and the two are one thing. The specific
    # side survives, because it says more and the general is implied.
    onto = ontology.current()
    by_plain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in ents:
        if e["id"] not in taken and e["type"] not in SELF_KINDS:
            by_plain[normalize(e["name"], plural=False)].append(e)
    for members in by_plain.values():
        if len(members) < 2:
            continue
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in members:
            by_type[e["type"]].append(e)
        for specific, kin in by_type.items():
            kin.sort(key=_rank, reverse=True)
            keep = kin[0]
            for general, others in by_type.items():
                if general == specific or not onto.is_a(specific, general):
                    continue
                for drop in others:
                    if drop["id"] in taken or drop["id"] == keep["id"]:
                        continue
                    out.subtypes.append(
                        Candidate(
                            keep["id"],
                            drop["id"],
                            keep["name"],
                            drop["name"],
                            f"{general}→{specific}",
                            "subtype",
                            1.0,
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

    # tier 2: close by name embedding — the pairs a worker computed and
    # posted, read back with both entities still unmerged and not taken
    if likely:
        by_id = {e["id"]: e for e in ents}
        for row in store.entity_candidates(con, etype):
            if row["type"] not in LIKELY_TYPES:
                continue
            a, b = by_id.get(row["a"]), by_id.get(row["b"])
            if a is None or b is None or a["id"] in taken or b["id"] in taken:
                continue
            keep, drop = _survivor(a, b)
            out.likely.append(
                Candidate(
                    keep["id"],
                    drop["id"],
                    keep["name"],
                    drop["name"],
                    row["type"],
                    "likely",
                    float(row["score"]),
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
    usage: dict[str, int] = field(default_factory=dict)  # summed over the calls

    @property
    def name(self) -> str:
        return self.model

    @property
    def cost(self) -> float:
        """What the calls so far cost, in USD, by the price table."""
        return extraction.cost_usd(self.model, self.usage)

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
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
                got = getattr(response.usage, key, None) or 0
                self.usage[key] = self.usage.get(key, 0) + int(got)
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
    merged_subtypes: int = 0
    declined: int = 0


def apply(
    con: sqlite3.Connection,
    p: Plan,
    *,
    adjudicator: Adjudicator | None = None,
    twins: bool = False,
    subtypes: bool = False,
    run: str | None = None,
) -> Report:
    """Merge every sure candidate, the concept/method twins and the
    subtype folds when asked, and ask the adjudicator about the likely
    ones. Idempotent: a re-run finds nothing left to merge."""
    report = Report()
    run = run or "resolve-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    for c in p.sure:
        store.merge_entities(con, c.drop, c.keep, producer=PRODUCER, run=run)
        report.merged_sure += 1
    if subtypes:
        for c in p.subtypes:
            store.merge_entities(
                con, c.drop, c.keep, across_types=True, producer=PRODUCER, run=run
            )
            report.merged_subtypes += 1
    if twins:
        for c in p.twins:
            store.merge_entities(
                con, c.drop, c.keep, across_types=True, producer=PRODUCER, run=run
            )
            report.merged_twins += 1
    if p.likely:
        decisions = (adjudicator or NoAdjudicator()).decide(p.likely)
        declined: list[tuple[int, int]] = []
        for c, yes in zip(p.likely, decisions, strict=True):
            if yes:
                try:
                    store.merge_entities(
                        con, c.drop, c.keep, producer=PRODUCER, run=run
                    )
                except ValueError:
                    continue  # already merged the other way this round
                report.merged_likely += 1
            else:
                report.declined += 1
                declined.append((c.keep, c.drop))
        if (
            declined
            and adjudicator is not None
            and not isinstance(adjudicator, NoAdjudicator)
        ):
            # a real no is recorded, so the pair is not asked about again;
            # the default adjudicator's no is only "nobody asked"
            store.decide_candidates(con, declined, by=adjudicator.name)
    return report


def decide(
    con: sqlite3.Connection,
    items: list[dict[str, Any]],
    adjudicator: Adjudicator,
) -> Report:
    """The adjudicate work step's batch: pairs the door handed out
    (``keep``, ``drop``, names, type, score), decided and applied — a
    merge for a yes, a recorded no otherwise."""
    candidates = [
        Candidate(
            int(it["keep"]),
            int(it["drop"]),
            str(it.get("keep_name") or ""),
            str(it.get("drop_name") or ""),
            str(it.get("type") or ""),
            "likely",
            float(it.get("score") or 0.0),
        )
        for it in items
    ]
    return apply(con, Plan(likely=candidates), adjudicator=adjudicator)


class DecidedAdjudicator:
    """Decisions a worker already took, replayed on the door: the take-in
    of the adjudicate step."""

    def __init__(self, name: str, same: list[bool]) -> None:
        self.name = name
        self.same = list(same)

    def decide(self, candidates: list[Candidate]) -> list[bool]:
        return self.same[: len(candidates)] + [False] * (
            len(candidates) - len(self.same)
        )
