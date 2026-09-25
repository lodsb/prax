# The multilingual embedder, measured after the switch

2026-09-25. `embeddings.model` went from `bge-small-en-v1.5` to
`multilingual-e5-small` — the last open step of the multilingual study
(`docs/log.md`, "the night of 2026-09-23"). 1,120,677 chunk vectors and
9,998 document fields rebuilt, about five hours with the card free.

This is what it bought, which is less than nothing on both axes measured.

## Method, and the error found in it

An A/B on a copy of the store, the document field rebuilt both ways; the
harness computes its own vectors rather than reading the index, so both
embedders answer the same questions on one run and neither is graded
through the other's HNSW recall.

**The first run of this was wrong and said so.** Queries were embedded
with `embed()`, which applies the *passage* prefix. `multilingual-e5-small`
carries both a `query: ` and a `passage: ` prefix, so that penalised
exactly the model under test. Corrected to `embed_query()`, which is what
the door uses. Every number below is from the corrected run.

## English, 300 questions from sections' own text

| | hit@10 | MRR |
|---|---|---|
| bge-small-en-v1.5, no sections | 0.230 | 0.115 |
| **bge-small-en-v1.5** | **0.333** | **0.195** |
| multilingual-e5-small, no sections | 0.097 | 0.056 |
| **multilingual-e5-small** | **0.187** | **0.095** |

Section summaries earn their place either way — **+0.103** for bge,
**+0.090** for e5 — which is the sections result confirmed independently
of the embedder.

The embedder result is the other one: **bge-small is nearly twice as
good**, 0.333 against 0.187, with more than double the MRR.

## The crossing, which is what the switch was for

The 19 questions in `tests/eval/queries.yaml` that carry a German form,
each asked in both languages against the same documents. The English
top hit is the answer the German question has to find.

| | still in the German top 10 | still first |
|---|---|---|
| **bge-small-en-v1.5** | **9 / 19** | **5 / 19** |
| multilingual-e5-small | 6 / 19 | 1 / 19 |

The English-only model crosses *better* than the multilingual one, on the
library the multilingual one was chosen for.

Nineteen questions is a small sample and 9 against 6 is within touching
distance of noise; 5 against 1 is harder to wave away, and neither points
the way the switch assumed. Each model is graded against its own English
answer, which measures self-consistency across the two languages rather
than correctness — but that is the property the switch was supposed to
improve.

## What this means

The switch cost about half the English retrieval quality and bought no
crossing. The plan's reasoning was sound and its premise was not: it
assumed a multilingual model would cross better on this library, and the
only thing that could settle that was the measurement nobody had run.

Going back is bookkeeping rather than compute. `chunk_embeddings` holds
one model per chunk, so the record was overwritten as the new vectors
landed — but the old 1,380 MB index was never touched, and
`store.adopt_vectors` (written the same day, before it was needed) puts
the rows back from its keys: `embeddings.model` in prax.yaml, restart the
door, `prax maintain --adopt-vectors bge-small-en-v1.5`.

## What would change the answer

A library with more German in it. This one is 7,196 English documents to
1,988 German, and the document field of a German document is largely
English already, because the summary is written in `graph.language` and
the title is often as printed. The multilingual model's advantage is in
matching a German query to a *German* passage, which is the chunk side
rather than the document field, and is not what either measurement here
looked at.

So: not "multilingual embedders do not work", but "this one, on this
library, on the document field, is worse on both counts". A chunk-side
measurement on the German documents would be the fair rematch, and wants
the query set to grow past nineteen.
