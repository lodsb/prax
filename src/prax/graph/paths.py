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
or under ``SOUND`` are sound, measured on the library
(``scripts/eval_paths.py``). At 7 (since 2026-10-05; 6 before), 126 of
150 citation pairs (the direct edge banned) and 59 of 91 pairs from a
topic page have one, 12 of 300 random pairs of papers and none of 150
pairs of a recipe and a paper. The seven random pairs between 6 and 7
were read (``--show random:6:7``): five real connections (two papers
citing one work, two about one kind of interface), one through a
university only, one through a wrong authorship. The line moved for
the client's chains of resolved citations, which cost 6.1 (AL step 9,
G1).
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Iterable
from dataclasses import dataclass, field

from prax.graph import ontology

# how strongly a relation connects is the relation's own ``strength`` in
# its module (strong, weak, or between), the composed ontology's
STRONG_COST, MEDIUM_COST, WEAK_COST = 1.0, 2.0, 3.0
CONFIDENCE_COST = {"EXTRACTED": 1.0, "INFERRED": 1.35, "AMBIGUOUS": 2.0}
HUB = 0.5  # the cost of passing through an entity, times log(1 + degree)
NO_PASS = 1500  # an entity with more facts is an end only
SOUND = 7.0  # a path at or under this cost is sound (6 until 2026-10-05)
MAX_HOPS = 4


def relation_strengths() -> dict[str, str]:
    """Each relation's ``strength`` as the ontology on disk declares it."""
    return {
        r.name: r.strength for r in ontology.current().relations.values() if r.strength
    }


def hop_cost(
    rel: str, confidence: str, documents: int, strength: str | None = None
) -> float:
    """What one fact costs to cross: its relation's strength (looked up
    when not given), its confidence class, and fewer the more documents
    state it."""
    if strength is None:
        strength = relation_strengths().get(rel, "")
    base = {"strong": STRONG_COST, "weak": WEAK_COST}.get(strength, MEDIUM_COST)
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
    strengths: list[str] = field(default_factory=list)  # beside ``rels``
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


def build(rows: Iterable[Row], strengths: dict[str, str] | None = None) -> PathIndex:
    """The index from edges: ``(edge id, source entity, relation, target
    entity, confidence, document)``, the entities already canonical. A
    loop is dropped; edges of one (source, relation, target) are one fact,
    its confidence the best of theirs. ``strengths`` are the relations'
    (``relation_strengths`` when None)."""
    strength = relation_strengths() if strengths is None else strengths
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
            ix.strengths.append(strength.get(r, ""))
        ix.rel.append(rel_id[r])
        best = min(
            CONFIDENCES.index(c) if c in CONFIDENCES else 2 for _, _, c in behind
        )
        ix.conf.append(best)
        documents = len({doc for _, doc, _ in behind if doc is not None})
        ix.cost.append(
            hop_cost(r, CONFIDENCES[best], documents, ix.strengths[rel_id[r]])
        )
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


class _View:
    """The index as one viewer sees it: with hidden documents, a fact's
    cost counts only the documents it may see, and an entity's hub price
    only the facts it may cross, so neither a cost nor a ranking says what
    a hidden document states (the review of 2026-10-04)."""

    def __init__(self, ix: PathIndex, hidden: frozenset[int]) -> None:
        self.ix, self.hidden = ix, hidden
        self._cost: dict[int, float] = {}
        self._hub: dict[int, float] = {}

    def cost(self, f: int) -> float:
        if not self.hidden:
            return float(self.ix.cost[f])
        got = self._cost.get(f)
        if got is None:
            docs = {
                d
                for _, d in self.ix.edges_of(f)
                if d is not None and d not in self.hidden
            }
            conf = CONFIDENCES[self.ix.conf[f]]
            r = self.ix.rel[f]
            got = self._cost[f] = hop_cost(
                self.ix.rels[r], conf, len(docs), self.ix.strengths[r]
            )
        return got

    def hub(self, node: int) -> float:
        if not self.hidden:
            return self.ix.hub(node)
        got = self._hub.get(node)
        if got is None:
            ix = self.ix
            seen = sum(
                1
                for slot in range(ix.off[node], ix.off[node + 1])
                if ix.crossable(int(ix.slot_fact[slot]), self.hidden)
            )
            got = self._hub[node] = (
                math.inf if seen > NO_PASS else HUB * math.log1p(seen)
            )
        return got


Layer = dict[int, tuple[float, int, int]]  # node -> (cost, previous node, fact)


def _grow(
    ix: PathIndex,
    view: _View,
    starts: list[int],
    depth: int,
    hidden: frozenset[int],
    allowed: frozenset[int] | None,
    banned: frozenset[int],
) -> list[Layer]:
    """Layer by layer from the starts: ``layers[h]`` is the best way to
    each node in exactly ``h`` hops, built from layer ``h - 1`` alone, so
    a cheaper but longer way never overwrites a shorter one (the review of
    2026-10-04 found a path to a node lost to a longer, cheaper one).
    Passing through a node costs its hub price; a start does not."""
    layers: list[Layer] = [dict.fromkeys(starts, (0.0, -1, -1))]
    for h in range(depth):
        nxt: Layer = {}
        for u, (cu, _, _) in layers[-1].items():
            if h > 0:
                price = view.hub(u)
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
                c = cu + view.cost(f)
                if v not in nxt or c < nxt[v][0]:
                    nxt[v] = (c, u, f)
        layers.append(nxt)
    return layers


def _back(layers: list[Layer], hops: int, node: int) -> tuple[list[int], list[int]]:
    """The facts and the nodes of the way to ``node`` in ``hops`` hops,
    from its start."""
    facts, nodes = [], [node]
    while hops > 0:
        _, prev, f = layers[hops][node]
        facts.append(f)
        nodes.append(prev)
        node, hops = prev, hops - 1
    return facts[::-1], nodes[::-1]


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
    view = _View(ix, hidden)
    left = _grow(ix, view, a, (max_hops + 1) // 2, hidden, allowed, banned)
    right = _grow(ix, view, b, max_hops // 2, hidden, allowed, banned)
    ends_ = set(a) | set(b)
    found: list[tuple[float, list[int]]] = []
    meeting = set().union(*left) & set().union(*right)
    for m in meeting:
        hub = 0.0 if m in ends_ else view.hub(m)
        if hub == math.inf:
            continue
        for dl, lay in enumerate(left):
            if m not in lay:
                continue
            for dr, ray in enumerate(right):
                if m not in ray or not 0 < dl + dr <= max_hops:
                    continue
                lf, ln = _back(left, dl, m)
                rf, rn = _back(right, dr, m)
                nodes = ln + rn[::-1][1:]
                facts = lf + rf[::-1]
                # a simple path only: no entity and no fact twice
                if len(set(nodes)) != len(nodes) or len(set(facts)) != len(facts):
                    continue
                found.append((lay[m][0] + ray[m][0] + hub, facts))
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
