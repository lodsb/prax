# What the graph's own dictionary is worth to a German query

2026-09-24. The German keyword half of retrieval scores MRR 0.39 against
English's 0.91 (`retrieval-multilingual-2026-09-24.md`), and the reason
is not the embedder — it is that nothing tells FTS5 that *Faltung* and
*convolution* are one word. The vectors cope; BM25 cannot.

The vocabulary pass left a dictionary behind it without being asked to.
`Olivenöl` is a label of the entity called `olive oil`, `Verklemmung` of
`deadlock`, `halteproblem` of `halting problem` — 717 German labels, each
with a document behind the pair, and each the library's own word for the
thing rather than a translation service's. `store.expand_query` adds it
as one more alternative per term, which is the mechanism the acronyms
already use.

## What was measured

The query set in `tests/eval/` runs against a twelve-document fixture,
which holds none of the library's labels, so it cannot see this at all.
This is a before/after on the live store and on one mechanism: for 80
German names the graph knows an English name for, search for the German
word with the expansion and without it.

| | |
|---|---|
| queries that reach documents they did not before | **32 of 80 (40%)** |
| unchanged | 48 |
| that found nothing at all before | 0 |
| documents newly reached | 130 |
| **of those, English** | **83 (64%)** |
| German | 42 (32%) |

That last row is the mechanism doing what it was built for: a German
query now reaches the English half of the library. Before it, a search
for `klimaschutz` saw only what was written in German.

## What this is not

**Not an MRR, and not a claim that retrieval got better.** Reaching more
documents is only an improvement if they are the right ones, and there
are no relevance judgements for these queries. What is measured is
*reach*: 40% of German queries surface documents the German word alone
did not.

The honest next measurement is a query set of German questions whose
answers are English documents, judged. That is the eval
`retrieval-multilingual-2026-09-24.md` asked for and still does not
exist; it would settle both this and the embedder question, which is
also open.

## Limits found while measuring

- **The library must not be its own evidence.** `Olivenöl` had been
  taken out of the vocabulary pass's candidate net because a briefing
  page of prax's own said "Olivenöl sits beside olive oil" — an English
  document containing the German word. The corpus test skips pages now,
  and 85 more names were folded on the next run.
- **The dictionary only knows what the graph knows.** A German word that
  never became an entity is not in it. This is a dictionary of the
  library's *concepts*, not of German.
- Two lookups per query term, both on `idx_entity_labels_label`, plus
  one for the whole query. No measurable cost at this size.
