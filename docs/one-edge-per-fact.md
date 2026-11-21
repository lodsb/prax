# Facts and the edges that state them

The BWS survey of 2026-11-17 asked for one edge per fact. A later run
that states a fact again would add itself as a producer of the existing
edge instead of writing a second one. This note records what the graph
held, the designs weighed, the one chosen on 2026-11-21, and what was
built.

## The model

An edge is one reader's assertion: this producer, in this run, read in
this document that s rel d, with this quote, at this time. A fact is
what edges state: a relation between two things, each thing its
canonical entity. Edges are stored. A fact is derived from the live
edges under the current entity resolution, and is never stored.

This is the separation of a claim from a fact that Google's Knowledge
Vault makes between extractions and fused facts, and Wikidata makes
between a statement and its references. Invariant 8 already said edges
are evidence. It now also says what a fact is.

A fact is derived, not stored, because which edges state one fact depends
on entity resolution, and that changes: merges, unmerges, renames. A
facts table would be rewritten by every merge and every undo, a cache of
the resolution kept as if it were the record. Where a read needs facts
fast, they are built as a derived index, as the path index of `connect`
already does (`prax.graph.paths`: "one fact per (source, relation,
target), with the edges behind it").

## What the graph held (live store, 2026-11-20, read-only)

| | count |
|---|---|
| live edges | 342,947 |
| distinct facts | 321,022 |
| facts held by more than one live edge | 14,561 |
| extra edges over one per fact | 21,925 |

| where the second edge comes from | extra edges | what it is |
|---|---|---|
| one document, two readers | 11,691 | two assertions: Zotero's record and a model (4,870), the typing rules and a model (1,817), Claude and the local model (1,215) |
| several documents | about 7,600 | support: 3,471 facts are stated by two documents or more |
| one reader of one document, two runs | 3,039 | a pass that stated its fact again after a merge or an ontology bump: `polysemy` 2,175, `page` 379, `typing-rules` 347, `references` 93, `world-dates` 41 |
| one reader of one document, one run | 669 | the same fact twice in one reading (450), or two names a merge has since made one (219) |

`traverse` listed edges, not facts, so a fact held by two edges was
listed and counted twice. That is what BWS met.

The measurement also found the opposite fault. Extraction, the
importers and the passes asked `find_edges` whether a fact was held, and
`find_edges` answered for any document. A second document stating a fact
the graph held under the same names got no edge: its evidence was lost,
and the fact went with the first document when that was retired.
`find_edges` also matched only the exact names written, so after a merge
it missed the fact and a pass wrote it again: the 3,039.

## The designs weighed

- **A. An edge per assertion, facts derived in the reads.** Chosen.
- **B. One edge per fact and document, later readers in a table beside
  it** (`edge_support`). Changing an assertion after it was written loses
  the second reader's quote, place, run and time, or keeps them in a side
  table that is the edge table again under another name. Every undo
  (`retire_run`, `restore_run`, `unmerge_run`, `edge_endings`, the rules'
  premises, the wall's `hidden_by_premise`) would have to ask whether
  another reader still holds the edge.
- **C. A facts table with evidence rows** (the RDF-star shape). It stores
  what entity resolution decides, so every merge and unmerge rewrites it,
  and it is a rewrite of every read: 157 SQL statements in 35 modules name
  `edges`.

## What was built (2026-11-21)

### Writes: a fact's identity, and a writer's own source

`store.find_edges(con, edge, *, source_doc, producer)` and
`document_edges_of` find the live edges that state a fact
(`graph.edges._fact_edges`). Each name is resolved as `link` resolves it
and followed to its survivor, and an edge written under any name of that
survivor's group counts. A writer asks whether its own source states the
fact already, never whether anyone does:

| writer | asks |
|---|---|
| extraction | this document, this reader (`producer` matches the reader with any `/prompt…` suffix) |
| the citation import, Zotero | this document, this source |
| the review replay, its rules, the typing pass | this document: the triple they settle is the model's reading of it |
| polysemy, a page's links, a project's members | this document |
| resolution's editions, a sync with `defer` | any document: a fact of the graph itself, or one another reader holds |

### Reads: a fact listed once

`store.graph.edges.as_facts(rows, key)` groups edges
into facts, the one grouping every read that lists facts uses. A fact is
shown as its best edge (the surest confidence, then one with a quote,
then the oldest), keeping that edge's fields and `edge_id`, so the
canvas and `why` work unchanged. When more than one edge states it,
`sources` counts the edges, `support` the documents, and `also` lists the
others' `edge_id`, document and `producer` (the first four).
A version of a work (`example_of`, plan item AS) is no second witness:
`support` counts a work's versions once (`as_facts(works=)`, from
`store.retrieval.works_of`, 2026-11-22; migration 73 indexes the live
edges by relation and document for it).
`traverse`'s first hop lists facts, keyed by the canonical entities, and
caps and counts them (`left_out`). `stated` is the earliest dated document
behind a fact, and `disputed` counts the conflicts of all its edges. A
person's page and the list of people count and list facts. A document's
own counts (the facts it states, what retiring it ends) stay counts of
edges, now worded "statements".

### Already fact-shaped, unchanged

The rules pass plans by canonical
(subject, object) pair, so a premise stated twice gives one derivation.
The conflicts pass groups by canonical subject and value. The path index
of `connect` is one fact per (source, relation, target). `changes` is
about record time and lists edges, which is right for what was written
and ended.

### The duplicates ended (2026-11-22)

`prax heal --apply` ended the later edges of each fact one reader stated
twice in one document: 3,429 edges of 730 facts (`heal:duplicate-facts`,
the oldest kept, `restore_run` takes them back). Two readers of one
document stay two assertions.

## What is left

- The evidence lost before 2026-11-21: a document whose facts were
  already held got no edges for them. Only a new extraction of it brings
  them back. The extraction reports counted them as `existing`, together
  with a fact a reading stated twice, which lost nothing: 2,773 live
  documents read before then counted 8,832 (2026-11-22). The 1,117
  documents heal's `stale-extractions` sent back to the extract step say
  how many of those were lost: each is read again under the new rule, and
  its earlier stamp is in `meta.extraction_history`.
