# One edge per fact? A design question of AV

The BWS survey of 2026-11-17 asked for one edge per fact. A later run
that states a fact again would add itself as a producer of the existing
edge instead of writing a second one. This note measures what the second edges
are, sets out three designs, and recommends one. Nothing is built until
the owner decides. Invariant 8 is affected by two of the three designs.

## What the graph holds (live store, 2026-11-20, read-only)

A fact here is (subject, relation, object), with both ends taken as
merged (`canonical_id`).

| | count |
|---|---|
| live edges | 342,947 |
| distinct facts | 321,022 |
| facts held by more than one live edge | 14,561 |
| extra edges over one per fact | 21,925 |

The extra edges come from four places:

| where the second edge comes from | extra edges | what it is |
|---|---|---|
| one document, two readers | 11,691 | two pieces of evidence: Zotero's record and a model (4,870), the typing rules and a model (1,817), Claude and the local model (1,215), a title match and a model (962) |
| several documents | about 7,600 | support: 3,471 facts are stated by two documents or more |
| one document, one reader, two runs | 1,941 | a duplicate: a re-extraction under a later ontology version or prompt kept the old edge |
| one document, one reader, one run | 662 | a duplicate made later: two names a merge has since made one |

Heal's `duplicate-facts` finds the last two rows (1,055 facts on
2026-11-17). Only those rows are duplicates in the strict sense. The
first two rows are what invariant 8 means by "edges are evidence".

What a reader sees: `traverse` lists edges, not facts
(`graph.traversal._side_facts`). A fact held by two edges is listed
twice, and its relation's count in `_side_counts` counts it twice. This is
what BWS met.

## Three designs

### A. An edge per reading; reads grouped by fact

The schema stays. Three changes:

1. **Reads group by fact.** `traverse`'s first hop lists a fact once,
   with its sources (`[{doc, producer}]`, capped) and the number of
   documents that state it. `_side_counts` counts facts. `connect` and
   `why` already work per edge and stay.
2. **A later run supersedes its own reader's edge.** A reader's family
   is its producer without the `/prompt…` and `@host` suffixes. When a
   live edge of the same family already holds the fact from the same
   document, `store.link` writes the new edge and ends the older one. The
   ending's `edge_endings.corrected_by` names the new edge. `restore_run` of the
   new run then states the old edge again, as it does for any repair.
   This stops the 1,941 at the source.
3. **A merge ends the twins it makes.** When `merge_entities` folds two
   names that one run used for one fact, it ends the later edge under
   the merge's run, so `unmerge_run` puts it back. This stops the 662.

Heal's `duplicate-facts` then clears what is there now: the owner's
`prax heal --apply`. Invariant 8 is unchanged.

Cost: a stage. Risk: low. Every write path already ends edges this way.

### B. One edge per fact and document; readers in a table beside it

A new table, `edge_support(edge_id, producer, run, evidence, place,
at, ended_at)`, one row per reading. The edge keeps its first producer.
`retire_run` ends a support row; the edge ends when its last support
ends. Migration: the 11,691 + 2,603 second edges become support rows.

Invariant 8 changes: provenance would no longer be one column set per
edge. Each of these would be rewritten: `retire_run`, `restore_run`,
`unmerge_run`, `edge_endings`, the rules' `NOT LIKE 'rule:%'` tests, the
wall's premise test (`hidden_by_premise`), the review of a run, and
heal's run-scoped repairs. A fact two readers agree on and one reader
later withdraws must stay live, so each of them needs a case for it.

Cost: two stages and a migration of some 14,000 edges. Risk: high. The
undo machinery is the part of prax with the most tests and the most
past incidents (2026-09-22).

### C. A facts table with evidence rows

`facts(s, rel, d, world dates)` and `evidence(fact_id, doc, producer,
run, quote, place, valid_from, valid_to)`. This is the RDF-star shape,
and the cleanest one. It is also a rewrite of the graph package and of
every read: 157 SQL statements in 35 modules name `edges`. Not justified by 22,000 of
343,000 edges.

## Recommendation

A. Of BWS's two complaints, a fact shown twice is fixed in the read, and
a fact written twice is fixed at the write. Design A does both without
moving invariant 8. It also prepares the second design question of AV:
support counted by work. Once a fact's sources are grouped, the count
of works (`example_of`, AS) among its documents is one more column of
the same read.

## To decide

- A, B or C. If A: whether its third change, the merge ending its
  twins, comes in the same stage.
- Whether a fact from a record and a model on one document counts as
  one source or two in the number of documents shown. A counts
  documents, so it shows one.
