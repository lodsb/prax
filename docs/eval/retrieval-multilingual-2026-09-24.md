# German queries against an English-indexed library

2026-09-24. `scripts/eval_retrieval.py` over the fixture store, the 20
queries of `tests/eval/queries.yaml` asked twice: once in English, once
in the German line (`de:`) added beside each of them on 2026-09-23. 19
of the 20 have a German form; the acronym query has none worth writing.

The question was whether the embedder should change. A fifth of the
library is German (`docs/research-multilingual-2026-09-23.md`) and
`bge-small-en-v1.5` is trained on English, so the obvious move is
`multilingual-e5-small`: same 384 dimensions, same class of model, a
drop-in for `VEC_DIM`.

## What it measured

| embedder | queries | fts MRR | fts hit@1 | vec MRR | vec hit@1 | hybrid MRR | hybrid hit@1 |
|---|---|---|---|---|---|---|---|
| bge-small-en-v1.5 | en (20) | 0.91 | 0.90 | **0.95** | **0.95** | 0.87 | 0.80 |
| bge-small-en-v1.5 | de (19) | 0.39 | 0.37 | **0.82** | **0.79** | 0.80 | 0.74 |
| multilingual-e5-small | en (20) | 0.91 | 0.90 | 0.79 | 0.75 | 0.84 | 0.75 |
| multilingual-e5-small | de (19) | 0.39 | 0.37 | 0.57 | 0.37 | 0.70 | 0.58 |

## What it says

**The multilingual model is worse, in both languages.** Not slightly:
German vector hit@1 halves, 0.79 to 0.37, and English gives up 0.20 MRR.
The reason is visible in the fixture — the targets are English documents,
and an English-trained model reading an English corpus beats a
multilingual one of the same size at the only job it has here. A model
that speaks six languages spends its 384 dimensions on six languages.

**So the embedder is not the problem, and this is not the eval that
would find one.** The fixture is 12 documents and 365 vectors; the
German queries name targets whose English text shares proper nouns,
numbers and loan words with the query, which is exactly the case where an
English model does not need German. The measurement that would decide the
embedder is German queries whose English targets share few tokens, run
against the live library. Until that exists, no switch.

**The keyword half is the real gap.** 0.39 against 0.91, identical under
both embedders, because BM25 has no way to know that *Faltung* and
*convolution* are the same word. Three levers, cheapest first:

1. **Translate the query**, not the index: the German half of a query
   joins the match expression as its English terms. One local model call
   per query, or a dictionary for the terms that recur.
2. **Index the native summary.** `meta.summaries` already holds the
   German summary of a German document (2026-09-24); the document field
   does not index it yet. This puts German tokens in the field a German
   query searches.
3. **Standardize the graph's vocabulary**, so `Olivenöl` and `olive oil`
   are one entity and a traversal from either reaches the same place.

None of the three needs a new embedder, and all three are measurable
against this file.

## Caveats

- The fixture is small (12 documents). Treat the absolute numbers as a
  comparison between rows, not as the library's retrieval quality.
- The German queries are translations of the English ones, so they ask
  about the same English documents. A German query about a German
  document is the case this eval does not cover and the live library
  would.
- Both runs ended in a `PermissionError` cleaning up the temporary
  directory: the usearch file is still memory-mapped when
  `TemporaryDirectory` removes it. Cosmetic, after the numbers are
  printed; the fix is `release_vector_views()` before cleanup.
