"""How is A connected to B: a path index over the graph (stage AM).

A derived structure, never canonical: built from the live edges by the
store (``store.connect``), held in memory in compact arrays, and rebuilt
when the edges change. One fact per (source, relation, target), with the
edges (and so the documents) behind it. At 121,000 entities and 194,000
facts it builds in about a second and answers in a millisecond
(docs/log.md, 2026-10-04), so the memory-mapped file with a delta that
the design sketched waits for a graph that needs it.

What a path here respects, which no graph engine knows:

- **Evidence per hop.** A hop costs by its relation (a strong one such as
  ``cites`` or ``authored_by`` least, ``about`` more, ``mentions`` most),
  its confidence class, and the number of documents that state it.
- **Hubs.** Passing through an entity costs ``HUB`` times the log of its
  degree, as inverse document frequency does in text search; past
  ``NO_PASS`` facts an entity is an end only. Otherwise every answer runs
  through "machine learning".
- **Relations.** A set of them may be required.
- **The wall.** A fact is crossable when one of its documents is visible
  (or it has none): the hidden documents are a filter in the loop.

A path's cost is the sum of its hops and of the hubs it passes. Paths at
or under ``SOUND`` are sound: measured on a copy of the library
(``scripts/eval_paths.py``), 111 of 150 citation pairs (the direct edge
banned) and 41 of 91 pairs from a topic page have one, 7 of 300 random
pairs of papers, most of those real connections when read, and none of
150 pairs of a recipe and a paper.
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Iterable
from dataclasses import dataclass, field

STRONG = frozenset(
    {
        "cites", "extends", "implements", "uses", "proposes", "defines",
        "part_of", "has_part", "authored_by", "developed_by", "calls_for",
        "describes", "supports", "contrasts", "synthesizes", "makes",
        "variant_of", "evaluates", "compares", "supersedes", "invalidates",
        "advised_by", "succeeds", "derived_from", "written_in",
    }
)  # fmt: skip
WEAK = frozenset({"mentions", "annotates"})
STRONG_COST, MEDIUM_COST, WEAK_COST = 1.0, 2.0, 3.0
CONFIDENCE_COST = {"EXTRACTED": 1.0, "INFERRED": 1.35, "AMBIGUOUS": 2.0}
HUB = 0.5  # the cost of passing through an entity, times log(1 + degree)
NO_PASS = 1500  # an entity with more facts is an end only
SOUND = 6.0  # a path at or under this cost is sound
MAX_HOPS = 4


def hop_cost(rel: str, confidence: str, documents: int) -> float:
    """What one fact costs to cross: its relation, its confidence class,
    and fewer the more documents state it."""
    base = STRONG_COST if rel in STRONG else WEAK_COST if rel in WEAK else MEDIUM_COST
    return (
        base * CONFIDENCE_COST.get(confidence, 2.0) / (1 + math.log2(max(1, documents)))
    )


@dataclass
class PathIndex:
    """The graph a path may cross, as arrays: entities as nodes, facts as
    the adjacency's slots (each fact twice, once from each end), and the
    edges behind each fact."""

    ids: array[int] = field(default_factory=lambda: array("q"))  # node -> entity id
    node_of: dict[int, int] = field(default_factory=dict)  # entity id -> node
    off: array[int] = field(default_factory=lambda: array("q"))  # CSR offsets
    nbr: array[int] = field(default_factory=lambda: array("q"))  # slot -> other node
    slot_fact: array[int] = field(default_factory=lambda: array("q"))  # slot -> fact
    src: array[int] = field(default_factory=lambda: array("q"))  # fact -> node
    dst: array[int] = field(default_factory=lambda: array("q"))
    rel: array[int] = field(default_factory=lambda: array("H"))  # fact -> relation id
    rels: list[str] = field(default_factory=list)
    cost: array[float] = field(default_factory=lambda: array("f"))
    conf: array[int] = field(default_factory=lambda: array("B"))  # index in CONFIDENCES
    eoff: array[int] = field(default_factory=lambda: array("q"))  # fact -> its edges
    edge: array[int] = field(default_factory=lambda: array("q"))
    doc: array[int] = field(default_factory=lambda: array("q"))  # -1: no document

    def degree(self, node: int) -> int:
        return int(self.off[node + 1] - self.off[node])

    def edges_of(self, fact: int) -> list[tuple[int, int | None]]:
        """The edges behind a fact: ``(edge id, document or None)``."""
        return [
            (int(self.edge[i]), None if self.doc[i] < 0 else int(self.doc[i]))
            for i in range(self.eoff[fact], self.eoff[fact + 1])
        ]

    def crossable(self, fact: int, hidden: frozenset[int]) -> bool:
        if not hidden:
            return True
        return any(
            self.doc[i] < 0 or int(self.doc[i]) not in hidden
            for i in range(self.eoff[fact], self.eoff[fact + 1])
        )

    def hub(self, node: int) -> float:
        d = self.degree(node)
        return math.inf if d > NO_PASS else HUB * math.log1p(d)


CONFIDENCES = ("EXTRACTED", "INFERRED", "AMBIGUOUS")
Row = tuple[int, int, str, int, str, int | None]  # edge, src, rel, dst, conf, doc


def build(rows: Iterable[Row]) -> PathIndex:
    """The index from edges: ``(edge id, source entity, relation, target
    entity, confidence, document)``, the entities already canonical. A
    loop is dropped; edges of one (source, relation, target) are one fact,
    its confidence the best of theirs."""
    facts: dict[tuple[int, str, int], list[tuple[int, int | None, str]]] = {}
    for eid, s, r, d, conf, doc in rows:
        if s != d:
            facts.setdefault((s, r, d), []).append((eid, doc, conf))
    ix = PathIndex()
    rel_id: dict[str, int] = {}
    adjacency: dict[int, list[tuple[int, int]]] = {}

    def node(entity: int) -> int:
        n = ix.node_of.get(entity)
        if n is None:
            n = ix.node_of[entity] = len(ix.ids)
            ix.ids.append(entity)
            adjacency[n] = []
        return n

    ix.eoff.append(0)
    for (s, r, d), behind in facts.items():
        f = len(ix.src)
        a, b = node(s), node(d)
        ix.src.append(a)
        ix.dst.append(b)
        if r not in rel_id:
            rel_id[r] = len(ix.rels)
            ix.rels.append(r)
        ix.rel.append(rel_id[r])
        best = min(
            CONFIDENCES.index(c) if c in CONFIDENCES else 2 for _, _, c in behind
        )
        ix.conf.append(best)
        documents = len({doc for _, doc, _ in behind if doc is not None})
        ix.cost.append(hop_cost(r, CONFIDENCES[best], documents))
        for eid, doc, _ in behind:
            ix.edge.append(eid)
            ix.doc.append(-1 if doc is None else doc)
        ix.eoff.append(len(ix.edge))
        adjacency[a].append((b, f))
        adjacency[b].append((a, f))
    ix.off.append(0)
    for n in range(len(ix.ids)):
        for other, f in adjacency[n]:
            ix.nbr.append(other)
            ix.slot_fact.append(f)
        ix.off.append(len(ix.nbr))
    return ix


@dataclass
class Path:
    cost: float
    facts: list[int]  # in order from the start to the end


def _grow(
    ix: PathIndex,
    starts: list[int],
    depth: int,
    hidden: frozenset[int],
    allowed: frozenset[int] | None,
    banned: frozenset[int],
) -> dict[int, tuple[float, int, int]]:
    """The best cost to every node within ``depth`` hops of the starts:
    node -> (cost, previous node, fact); a start's previous node is -1.
    Passing through a node costs its hub price; a start does not."""
    best: dict[int, tuple[float, int, int]] = dict.fromkeys(starts, (0.0, -1, -1))
    frontier = set(starts)
    for _ in range(depth):
        nxt: set[int] = set()
        for u in frontier:
            cu = best[u][0]
            if best[u][1] != -1:
                price = ix.hub(u)
                if price == math.inf:
                    continue
                cu += price
            for slot in range(ix.off[u], ix.off[u + 1]):
                f = int(ix.slot_fact[slot])
                if f in banned or (allowed is not None and ix.rel[f] not in allowed):
                    continue
                if not ix.crossable(f, hidden):
                    continue
                v = int(ix.nbr[slot])
                c = cu + ix.cost[f]
                if v not in best or c < best[v][0]:
                    best[v] = (c, u, f)
                    nxt.add(v)
        frontier = nxt
    return best


def _back(best: dict[int, tuple[float, int, int]], node: int) -> list[int]:
    out = []
    while best[node][1] != -1:
        _, prev, f = best[node]
        out.append(f)
        node = prev
    return out[::-1]


def connect(
    ix: PathIndex,
    starts: list[int],
    ends: list[int],
    *,
    max_hops: int = MAX_HOPS,
    k: int = 3,
    relations: Iterable[str] | None = None,
    hidden: frozenset[int] = frozenset(),
    banned: frozenset[int] = frozenset(),
) -> list[Path]:
    """The ``k`` best paths from any of ``starts`` to any of ``ends``
    (entity ids) of at most ``max_hops`` facts, cheapest first, each
    through other entities than the ones before it. The two sides are
    grown halfway and met in the middle."""
    a = [ix.node_of[s] for s in starts if s in ix.node_of]
    b = [ix.node_of[e] for e in ends if e in ix.node_of]
    if not a or not b:
        return []
    allowed = (
        None
        if relations is None
        else frozenset(i for i, r in enumerate(ix.rels) if r in set(relations))
    )
    left = _grow(ix, a, (max_hops + 1) // 2, hidden, allowed, banned)
    right = _grow(ix, b, max_hops // 2, hidden, allowed, banned)
    ends_ = set(a) | set(b)
    found: list[tuple[float, list[int]]] = []
    for m in left.keys() & right.keys():
        hub = 0.0 if m in ends_ else ix.hub(m)
        if hub == math.inf:
            continue
        facts = _back(left, m) + _back(right, m)[::-1]
        if facts and len(facts) <= max_hops:
            found.append((left[m][0] + right[m][0] + hub, facts))
    found.sort(key=lambda x: x[0])
    out: list[Path] = []
    seen: set[frozenset[int]] = set()
    for cost, facts in found:
        inner = frozenset(n for f in facts for n in (ix.src[f], ix.dst[f])) - ends_
        if inner in seen:
            continue
        seen.add(inner)
        out.append(Path(round(cost, 2), facts))
        if len(out) >= k:
            break
    return out
