# The regions of the library

2026-09-27. Stage P. `prax.graph.communities` computes the regions,
`store.graph.communities` keeps them, the `communities` pass of
`prax maintain` rebuilds them nightly, and the `communities` step names
and describes them with the local model.

## What a region is

`traverse` answers "what is next to the Fourier transform". It had nothing
to say about "what region of the library is this". The standard answer
(GraphRAG's) is to partition the entity graph into communities, to write
a summary of each with a model, and to keep the summary as a thing of
its own. prax had the parts: the local model and a pass shaped like
`sections`.

**The nodes are the topical kinds of thing:** concepts, methods, tools,
datasets, materials, techniques and works, with their subtypes (devices,
ingredients, dishes, features, standards, part kinds). Documents, people,
organizations, claims, specs, places and events are left out. That is
the hub decision the plan asked for first. On 2026-09-27, 164 of the 200
biggest hubs were papers. A university recorded as a venue had degree
725, and the library's own author 513. A partition with them in it
clusters around artifacts, one region per prolific author or container.
Left out, they no longer have to be decided one by one.

**Two things are linked** by a direct edge between them (weight 1 each),
and by being named in the same document. A document naming m of them
adds 1/(m − 1) to each pair, so every document weighs the same: a survey
of three hundred methods does not outvote a paper about two. Only
entities named in two documents or more take part (8,488 of 42,452 on
2026-09-27). One named once is a detail of its document.

**The partition** is Louvain (networkx, pure Python, no compiled
dependency), seeded so the same graph gives the same answer. Leiden is
the better-known refinement: it guarantees that no community is
internally disconnected, which Louvain does not. It needs two compiled
packages (`igraph`, `leidenalg`), so it is an optional improvement in
`docs/PLAN.md` rather than the default. Level 0 is
its final pass: 24 regions, modularity 0.61. Level 1 splits each region
of 200 members or more by Louvain again inside it: 153 parts. Louvain's
own intermediate passes were not usable: 576 communities with a median
of four members, then 55. The whole run takes 3.3 s on the library.

What it found, the largest regions and some of their parts:

| region | members | parts, by their heaviest members |
|---|---|---|
| sound synthesis, MIDI, Max/MSP | 1,634 | synthesis languages; DAWs and sampling; EQ and dynamics; virtual analog and antialiasing |
| Java, operating systems | 1,013 | object-oriented programming; concurrency; computer architecture; parallel computing |
| DSP, Fourier, NMF | 928 | transforms; source separation; pitch detection; time-frequency analysis |
| tangible interfaces, HCI | 925 | groupware; the reacTable and vision; gesture and performance |
| timbre, MIR, machine learning | 729 | audio features; classifiers; HMMs and speech; dimensionality reduction |
| music theory | 539 | tuning; harmony and voice leading; mathematical music theory; maqam |
| the kitchen | 132 | salt, olive oil, onions, garlic |

The small domains get regions of their own: the kitchen, 3D printing,
PCB design, even the transistors of the electronics datasheets. It also
shows the resolver's leftovers (`java` twice, `LaTeX` and `LATEX`).

## Kept across nights

A derived index like chunks: it may be dropped and rebuilt at any time
(migration 0028: `communities`, `entity_communities`). A rebuild
compares each new community with the old ones of its level. One it
overlaps by half or more (Jaccard) takes the old one's id, label and
summary, so a link to a region survives a night. If the overlap is under
0.8, the summary is kept but marked stale, and the communities step
writes it again: a moved partition makes a summary stale rather than
wrong, the rule `meta.sections` follows.

## Named and described

The `communities` step (watched: the standing worker runs it when the
host configures a model) hands out regions without a summary or with a
stale one, regions before parts, largest first, from five members up.
The model sees the forty members that weigh most with their types, and
the titles of the eight documents that name most of them. It answers
with a name of two to five words and one to three sentences on what
the region covers. A part waits until its region has a name and is
described inside it ("one part of the region “Everyday cooking”").

## The ways in

- `traverse` carries `community`: the region and the part the walked
  entity is in, with labels and sizes. Absent for an entity outside the
  partition.
- `GET /communities` (the regions, or with `level=1&parent=` a region's
  parts) and `GET /communities/{id}` (summary, members, parts, the
  documents that name most of its members).
- The graph view lists the regions below the overview, and
  `#graph?community=N` shows one.

- `search` with `regions` opens with the region most of its first five
  hits' entities live in, as an item of its own (`kind: "region"`), when
  that region holds half their weight. Its part comes too, when the part
  holds nine tenths of its region's parts. The search page shows it above
  the hits, and the MCP tool asks for it. Measured in
  `docs/eval/regions-2026-09-28.md`: right for 92% of the queries it is
  shown for. Matching the query's words against the summaries was right
  under half the time.

- `ask` can carry the region's summary (`regions`), and does not by
  default: over the 21 named regions an answer named 0.25 of the region's
  heaviest members with it and 0.24 without (the same write-up).
