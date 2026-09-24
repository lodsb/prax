# The second hop: 3.4 MB, and what it should have been

2026-09-25. `traverse` at two hops on a well-connected entity returns
3.4 MB. This is the measurement of why, the two explanations that turned
out to be wrong, and the one that survived.

The entry point throughout is `Fourier transform`, a `method` with 151
edges — not the largest hub, a normal well-connected node.

## What comes back now

| | edges | bytes |
|---|---|---|
| hop 1 | 151 | 70 KB |
| hop 2 | 7,032 | 3,482 KB |

Hop 1 is a fact list and it is the right size. Hop 2 is 98% of the
payload. The first thing to note is that it is not mostly evidence:

    evidence 590 KB · src 518 · dst 212 · producer 189 · valid_from 154
    run 143 · ontology_version 132 · confidence 75 · rel 57

Provenance repeated over 7,183 rows is 693 KB. Dictionary-encoding it is
free and lossless, and it is not the answer — 1,688 of those tuples are
distinct, because `valid_from` varies per edge.

## The graph is scale-free, which is not the problem

The user's guess was that the fan-out is a natural phenomenon. It is.
Over 100,942 entities with a live edge and 324,600 edge endpoints:

    mean degree 3.22 · median 1 · 90th 6 · 99th 38 · max 1,951

    xmin   2: alpha = 1.92      xmin  10: alpha = 2.34
    xmin   5: alpha = 1.96      xmin  20: alpha = 2.86

A Clauset–Shalizi–Newman fit puts the tail exponent in the 2–3 band that
defines a scale-free degree distribution, and 69,549 of the 100,942
nodes have degree 1. So the shape is the expected one and no amount of
extraction hygiene will flatten it.

But the hubs carry less than folklore suggests: the top 10 nodes hold
1.5% of all edge endpoints, the top 100 hold 5.2%, the top 1,000 hold
20.6%. **A single traversal does not blow up because it hit a hub.**

## The hubs are topological

Of the 200 biggest hubs, **164 are papers**. The largest is not a
concept at all:

    1951  paper   Proceedings of the International Conference…   about×730, cites×348
     528  venue   Technische Universität München                 written_at×257
     389  paper   Wave digital filters: Theory and practice      cites×383
     242  method  nonnegative matrix factorization               about×91, extends×61
     224  method  short-time Fourier transform                   uses×117, about×65

The pattern is clear. A paper's degree measures how thoroughly it was
extracted and how long its bibliography is; a concept's degree measures
how central it is. They are different quantities wearing the same name.
`Proceedings of the International Conference` is a container that should
probably not be an entity at all.

## Two explanations that did not survive

**Inverse-degree weighting of the destination** (`paths / log(degree)`).
Ineffective: the top 50 stayed 44 papers. It also cannot be trusted
here, because degree counted by canonical id undercuts — edge rows keep
the alias ids they were written with, which `traverse`'s own comment
says. Several results came back as degree 1 when they plainly are not.

**A bipartite projection artifact.** 90.6% of hop-2 edges pass *through*
a paper, which looked decisive, so Zhou et al.'s resource-allocation
weighting (Phys. Rev. E 76:046115) was the obvious correction: divide a
shared intermediate's contribution by that intermediate's degree, so a
survey citing 383 papers says less about any pair than a paper citing 5.
Measured on this neighbourhood it changed almost nothing — 47 papers in
the top 50 instead of 44. The intermediates here are mostly low-degree,
so 1/k does not discriminate.

## What it actually is

The hop-2 edges, by shape:

    1571  paper -cites->       paper
    1368  paper -about->       method
    1143  paper -about->       concept
     511  paper -authored_by-> author
     446  paper -uses->        method
     376  paper -uses->        tool

Hop 1 reaches about a hundred papers. Hop 2 then returns **each of those
papers' entire record** — its bibliography (22% of the payload is
`cites` alone), its authors, its venue, everything it is about. It is not
a hub problem and not a projection artifact. It is that a document node
is a *record*, not a concept, and expanding one returns its catalogue
card.

The consequence is that the second hop leaves the conceptual layer
entirely. Asking what is near `Fourier transform` returns, mostly, the
bibliographies of papers that mention it.

## What works

Constrain the second hop to a path shape — *through* a document, *to* an
idea — and never follow `cites`. Then rank by **how many distinct
documents connect the idea to the entry point**:

     12 docs  [method ] fast Fourier transform        via about, uses
     10 docs  [method ] convolution                   via about
      8 docs  [method ] image processing              via about
      7 docs  [concept] aliasing                      via about
      7 docs  [method ] digital signal processing     via about
      7 docs  [method ] z-transform                   via about
      6 docs  [method ] dft                           via about, mentions
      5 docs  [method ] Hilbert transform             via about, uses
      5 docs  [method ] Laplace Transform             via about, proposes

**3,552 KB → 73 KB**, and the type mix is 15 methods, 4 concepts, 1 tool
where raw path count gave 44 papers of 50.

The discriminating statistic: of 1,771 ideas reachable at hop 2,
**1,667 (94%) are reached through exactly one document**. Requiring two
independent documents removes 94% of the candidates and loses nothing a
reader wants.

Three things recommend document support over the cleverer rankings.
It needs no degree lookup, so the alias/canonical unreliability above
cannot corrupt it. It is a meaningful quantity rather than a proxy —
"how many independent sources put these two ideas together". And it is
what invariant 8 already says the graph is: edges are evidence, so
counting independent evidence is the ranking the design implies.

## Where this sits in the literature

- **HippoRAG** (NeurIPS'24) and **HippoRAG 2** run Personalized PageRank
  from query-matched seed nodes, which is the principled version of
  "rank the neighbourhood by proximity to where I started". PPR would
  subsume the document-support ranking and cost an iterative computation
  per query; document support is one pass and explainable to the reader.
- **GraphRAG**'s Leiden communities with model-written summaries are the
  answer to the second half of the question below.
- The 2026 agentic-traversal work (`arXiv:2605.15109`,
  `arXiv:2601.13969`) converges on the same three moves: relevance-rank
  the neighbourhood, cap breadth, and let the agent choose depth rather
  than fixing it.

## The open question: a node for the hubs

A community summary node — Leiden over the entity graph, one sentence
per cluster from the local model — is the standard answer and would fit
prax cheaply, since the `sections` pass already writes summaries with
the same model. It would give `traverse` something to say at the level
above the neighbourhood, and `ask` a cheaper way in than chunks.

It is not proposed here. The measurement above fixes the breach for one
call's cost, and a community layer is a pass, a table and a staleness
rule. It belongs in the plan, not in this change.
