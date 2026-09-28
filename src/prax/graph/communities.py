"""The regions of the library: a partition of the graph's topical entities
into communities, at two levels (docs/communities.md).

The nodes are the kinds of thing a region is made of: concepts, methods,
tools, datasets, materials, techniques, works, and their subtypes. The
documents themselves, people, organizations, claims, specs, places and
events are left out. A paper cited by many, an author of many or a
university that published many connects everything it touches, so a
partition with them in clusters around artifacts. On 2026-09-27, 164 of
the 200 biggest hubs were papers, a university recorded as a venue had
degree 725, and the library's own author 513.

Two topical entities are linked by a direct edge between them (weight 1
each) and by being named in the same document. A document that names m
of them adds 1/(m - 1) to each pair, so every document weighs the same
however many things it names: a survey of three hundred methods does not
outvote a paper about two. Only entities named in at least ``MIN_DOCS``
documents take part. One named once is a detail of its document, and
belongs to the region its document is in.

The partition is Louvain (networkx, no compiled dependency), seeded so the
same graph gives the same answer. Level 0 is its final pass, the coarse
regions. Level 1 splits each region of ``SPLIT`` members or more by
Louvain again inside it; a smaller region is its own single part. Louvain's
own intermediate passes are not used: on this library they went from
576 communities with a median of four members straight to 55.

Nothing here reads the store; ``store.communities_input`` gives the
input and ``store.replace_communities`` keeps the answer."""

from __future__ import annotations

import itertools
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from prax.graph import ontology

# the kinds of thing that are not a region's content (and their subtypes)
NOT_TOPICAL = ("document", "person", "organization", "claim", "spec", "place", "event")
MIN_DOCS = 2  # documents an entity is named in before it takes part
RESOLUTION = 1.0  # Louvain's resolution: higher, more and smaller regions
SPLIT = 200  # a region this large is split into parts at level 1
MIN_SIZE = 3  # a community smaller than this is not kept
SEED = 7


def topical_types(onto: ontology.Ontology | None = None) -> frozenset[str]:
    """The entity types a region is made of."""
    onto = onto or ontology.current()
    return frozenset(
        t for t in onto.types if not any(onto.is_a(t, x) for x in NOT_TOPICAL)
    )


@dataclass
class Partition:
    """The two levels. ``regions`` are level 0; ``parts`` are level 1, each
    with the index of the region it is in. ``weight`` is each entity's
    weighted degree, the order its community names its members in."""

    regions: list[frozenset[int]] = field(default_factory=list)
    parts: list[tuple[int, frozenset[int]]] = field(default_factory=list)
    weight: dict[int, float] = field(default_factory=dict)
    entities: int = 0  # how many took part
    pairs: int = 0
    modularity: float | None = None


def graph_of(
    documents: Iterable[set[int]],
    direct: Counter[tuple[int, int]],
    *,
    min_docs: int = MIN_DOCS,
) -> dict[tuple[int, int], float]:
    """The weighted pairs: shared documents (1/(m - 1) each) and direct
    edges (1 each), among the entities named in ``min_docs`` documents."""
    docs = [set(d) for d in documents]
    count = Counter(e for d in docs for e in d)
    keep = {e for e, n in count.items() if n >= min_docs}
    w: dict[tuple[int, int], float] = defaultdict(float)
    for d in docs:
        members = sorted(d & keep)
        if len(members) < 2:
            continue
        share = 1.0 / (len(members) - 1)
        for a, b in itertools.combinations(members, 2):
            w[(a, b)] += share
    for (a, b), n in direct.items():
        if a != b and a in keep and b in keep:
            w[(min(a, b), max(a, b))] += n
    return dict(w)


def partition(
    pairs: dict[tuple[int, int], float],
    *,
    resolution: float = RESOLUTION,
    split: int = SPLIT,
    min_size: int = MIN_SIZE,
    seed: int = SEED,
) -> Partition:
    """Louvain over the pairs, then again inside every region of ``split``
    members or more."""
    import networkx as nx

    g = nx.Graph()
    g.add_weighted_edges_from((a, b, w) for (a, b), w in pairs.items())
    out = Partition(entities=g.number_of_nodes(), pairs=g.number_of_edges())
    if not g.number_of_edges():
        return out
    found = nx.community.louvain_communities(
        g, weight="weight", resolution=resolution, seed=seed
    )
    out.modularity = round(nx.community.modularity(g, found, weight="weight"), 4)
    out.weight = {n: round(float(d), 4) for n, d in g.degree(weight="weight")}
    regions = sorted(
        (frozenset(c) for c in found if len(c) >= min_size),
        key=lambda c: (-len(c), min(c)),
    )
    out.regions = regions
    for i, region in enumerate(regions):
        if len(region) < split:
            out.parts.append((i, region))
            continue
        inner = nx.community.louvain_communities(
            g.subgraph(region), weight="weight", resolution=resolution, seed=seed
        )
        kept = [frozenset(c) for c in inner if len(c) >= min_size]
        # what fell below the size of a part stays in the region's largest
        rest = region - frozenset().union(*kept) if kept else region
        if kept and rest:
            biggest = max(range(len(kept)), key=lambda k: len(kept[k]))
            kept[biggest] = kept[biggest] | rest
        for part in sorted(kept or [region], key=lambda c: (-len(c), min(c))):
            out.parts.append((i, part))
    return out


def jaccard(a: frozenset[int] | set[int], b: frozenset[int] | set[int]) -> float:
    return len(a & b) / len(a | b) if a or b else 1.0


# ---------------------------------------------------------------- the words

SHOW_MEMBERS = 40  # the members a summary is written from, by weight
SHOW_DOCUMENTS = 8  # and the titles of the documents naming most of them
MAX_LABEL = 60
MAX_SUMMARY = 500

_REGION_WORDS = re.compile(
    r"^(?:this|the)\s+(?:region|cluster|community|group|area|part)\s+", re.IGNORECASE
)


def system() -> str:
    """What the model is told: a name and what the region covers, in the
    language the library is written in."""
    from prax.text import language

    into = language.name(language.canonical()) or "English"
    return (
        "You are shown what one region of a personal library is about: the"
        " things named most in it, and the titles of documents in it. Give"
        f" the region a name and say what it covers, in {into}. Answer with"
        " the name alone on the first line, two to five words, then one to"
        " three sentences on what the region covers, so a reader can tell"
        " whether it is the part of the library they want. No preamble, no"
        " labels, no list, no quotation marks. Name the subjects themselves;"
        ' never write "this region" or "this cluster".'
    )


def user_message(item: dict[str, Any]) -> str:
    """The region, as the door hands it out: its members by weight with
    their kinds, the documents naming most of them, and for a part the
    region it is in."""
    lines = []
    region = str(item.get("region") or "").strip()
    if region:
        lines.append(f"This is one part of the region “{region}”.")
    lines.append("The things named most in it:")
    for m in list(item.get("members") or [])[:SHOW_MEMBERS]:
        lines.append(f"- {m['name']} ({m['type']})")
    docs = list(item.get("documents") or [])[:SHOW_DOCUMENTS]
    if docs:
        lines.append("")
        lines.append("Documents that name the most of them:")
        lines.extend(f"- {d['title']}" for d in docs if d.get("title"))
    lines.append("")
    lines.append("What is this region called, and what does it cover?")
    return "\n".join(lines)


def parse(out: str) -> tuple[str, str] | None:
    """The name and the summary out of what the model returned, or None
    when it did not give both."""
    from prax.text import answers

    text = answers.strip_tokens(out or "")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    label = answers.unwrap(lines[0]).strip().strip("#*").strip()
    summary = answers.unwrap(" ".join(lines[1:]), sentence=True).strip()
    summary = _REGION_WORDS.sub("", summary).strip()
    if summary:
        summary = summary[0].upper() + summary[1:]
    if not label or len(label) > MAX_LABEL or len(summary) < 30:
        return None
    return label, summary[:MAX_SUMMARY].strip()


def summarize(runtime: Any, item: dict[str, Any]) -> tuple[str, str] | None:
    """One region named and described, or None when the model did not
    manage it."""
    out, _usage = runtime.chat(
        system(), user_message(item), max_tokens=220, temperature=0.0
    )
    return parse(out)
