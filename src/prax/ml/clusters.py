"""Groups by density over a nearest-neighbour graph: HDBSCAN (Campello,
Moulavi and Sander 2013; McInnes and Healy 2017), for the faces of plan
item AT step 3.

The input is each point's nearest neighbours and their distances, not
the points: the caller asks the face index for them, so the work grows
with the number of points times ``k`` rather than with its square, which
is what a board can carry at a hundred thousand faces. A pair the
caller leaves out of every list (two faces of one picture) is never
joined directly. The steps are the algorithm's: the core distance of a
point (its ``min_samples``-th neighbour), the mutual reachability of a
pair (the larger of the two core distances and their own), the minimum
spanning forest of that graph, the tree of how the groups of at least
``min_size`` points come apart as the distance shrinks, and the groups
that last longest in it (excess of mass). The parts of the forest that
the neighbour lists do not connect are joined at ``FAR``, so they come
apart first.

Nothing here reads the store or a file; it imports nothing of prax.
Iterative throughout: a chain of thousands of single merges is the
usual shape of this tree, and recursion would run out of stack.
"""

from __future__ import annotations

from dataclasses import dataclass

FAR = 2.0  # the largest cosine distance: where unconnected parts meet
NOISE = -1


@dataclass(frozen=True)
class Grouping:
    """``labels[i]`` is point i's group (0, 1, … or ``NOISE``) and
    ``strength[i]`` how firmly it belongs, 0 to 1: the distance at which
    it left the group over the closest any member stayed (HDBSCAN's
    membership probability). ``persistence[g]`` is group g's stability."""

    labels: list[int]
    strength: list[float]
    persistence: list[float]


def _core(neighbours: list[list[tuple[int, float]]], min_samples: int) -> list[float]:
    return [
        row[min_samples - 1][1] if len(row) >= min_samples else FAR
        for row in neighbours
    ]


def _forest(
    n: int, neighbours: list[list[tuple[int, float]]], core: list[float]
) -> list[tuple[int, int, float]]:
    """The minimum spanning forest of mutual reachability over the
    neighbour lists (Kruskal), as merges (a, b, distance), the parts it
    leaves apart then merged at ``FAR``."""
    edges: dict[tuple[int, int], float] = {}
    for i, row in enumerate(neighbours):
        for j, d in row:
            if j == i:
                continue
            key = (i, j) if i < j else (j, i)
            w = max(core[i], core[j], d)
            if w < edges.get(key, FAR + 1):
                edges[key] = w
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    merges = []
    for (a, b), w in sorted(edges.items(), key=lambda kv: kv[1]):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
            merges.append((a, b, min(w, FAR)))
    roots = sorted({find(i) for i in range(n)})
    merges.extend((roots[0], r, FAR) for r in roots[1:])
    return merges


def _tree(
    n: int, merges: list[tuple[int, int, float]]
) -> tuple[list[tuple[int, int, float]], list[int]]:
    """The single-linkage tree: node n + k is merge k of two nodes at a
    distance, leaves 0 … n-1; and each node's size."""
    parent = list(range(n))
    top = list(range(n))  # a union's root -> the tree node it is now
    size = [1] * n
    nodes: list[tuple[int, int, float]] = []

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, w in merges:
        ra, rb = find(a), find(b)
        node = n + len(nodes)
        nodes.append((top[ra], top[rb], w))
        size.append(size[top[ra]] + size[top[rb]])
        parent[ra] = rb
        top[rb] = node
    return nodes, size


def group(
    neighbours: list[list[tuple[int, float]]],
    *,
    min_size: int = 3,
    min_samples: int = 3,
    floor: float = 0.0,
) -> Grouping:
    """HDBSCAN's groups of points given their neighbour lists: row i holds
    point i's nearest others as (index, distance), nearest first, with at
    least ``min_samples`` entries where it can. A distance is in [0,
    ``FAR``]. Fewer than ``min_size`` points make no group. A distance
    under ``floor`` counts as ``floor`` (HDBSCAN's cluster selection
    epsilon): a group is never split below it, so near copies (a burst of
    shots, one picture saved twice) do not make a group of their own and
    leave the rest of the person's faces out."""
    if min_size < 2:
        raise ValueError("a group has two points at least")
    n = len(neighbours)
    if n < min_size:
        return Grouping([NOISE] * n, [0.0] * n, [])
    core = _core(neighbours, min_samples)
    nodes, size = _tree(n, _forest(n, neighbours, core))

    least = max(floor, 1e-6)

    def lam(w: float) -> float:
        return 1.0 / max(w, least)

    def leaves(node: int) -> list[int]:
        out, stack = [], [node]
        while stack:
            x = stack.pop()
            if x < n:
                out.append(x)
            else:
                a, b, _ = nodes[x - n]
                stack += (a, b)
        return out

    # the condensed tree: cluster 0 is the root, never chosen
    birth = [0.0]
    up = [-1]
    stability = [0.0]
    left_at = [0.0] * n  # the lambda a point left its last cluster at
    last = [0] * n  # that cluster
    root = n + len(nodes) - 1
    stack = [(root, 0)]
    while stack:
        node, c = stack.pop()
        a, b, w = nodes[node - n]
        at = lam(w)
        big = [x for x in (a, b) if size[x] >= min_size]
        if len(big) == 2:
            stability[c] += size[node] * (at - birth[c])
            for x in (a, b):
                birth.append(at)
                up.append(c)
                stability.append(0.0)
                stack.append((x, len(birth) - 1))
            continue
        for x in (a, b):
            if size[x] >= min_size:
                stack.append((x, c))
                continue
            for p in leaves(x):
                left_at[p], last[p] = at, c
            stability[c] += size[x] * (at - birth[c])

    # excess of mass, from the deepest clusters up
    k = len(birth)
    kids: list[list[int]] = [[] for _ in range(k)]
    for c in range(1, k):
        kids[up[c]].append(c)
    chosen = [False] * k
    value = [0.0] * k
    for c in range(k - 1, 0, -1):
        below = sum(value[x] for x in kids[c])
        if kids[c] and below > stability[c]:
            value[c] = below
        else:
            value[c] = stability[c]
            # a group that lasted no distance at all is no evidence: the
            # floor makes its points leave where it was born
            chosen[c] = stability[c] > 0
    # a chosen cluster under a chosen one is the larger one's
    final = [-1] * k
    order: list[int] = []
    for c in range(1, k):  # parents come before their children
        held = final[up[c]] if up[c] > 0 else -1
        if held >= 0:
            final[c] = held
        elif chosen[c]:
            final[c] = c
            order.append(c)
    number = {c: i for i, c in enumerate(order)}
    labels = [
        number[final[last[p]]] if final[last[p]] >= 0 else NOISE for p in range(n)
    ]
    deepest = [0.0] * len(order)
    for p in range(n):
        if labels[p] != NOISE:
            deepest[labels[p]] = max(deepest[labels[p]], left_at[p])
    strength = [
        min(1.0, left_at[p] / deepest[labels[p]])
        if labels[p] != NOISE and deepest[labels[p]] > 0
        else 0.0
        for p in range(n)
    ]
    return Grouping(labels, strength, [stability[c] for c in order])
