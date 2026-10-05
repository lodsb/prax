# The graph's facts as one more search list

2026-10-05. The plan's "facts as one more search list" (from Graphiti's
fact sentence and LightRAG's relation vectors), measured before it was
built into the door. Not shipped: it moved nothing on the library's
question set.

## What was tried

A keyword index (FTS5) over each live edge: its subject's name, its
relation in words, its object's name and the quote that states it, with
the document it was read from. Rule-derived edges and the references
pass's citations were left out, as reference lists are left out of
search. Triggers kept it in step with every write and ending of an edge.
A search ranked documents by their best fact and gave that list one vote
in the fusion, beside the chunk, document-field and vector lists.

On a copy of the store (the database read with SQLite's backup API, the
vector files copied): 208,939 facts, about 48 MB (15 MB of index, 33 MB of
text), 22 s to fill.

## Measured

`tests/eval/queries-library.yaml`, 62 questions, depth 10, hybrid mode
(the keyword-only mode has no document lists and did not move):

| fusion | hit@1 | hit@3 | MRR | keyword | paraphrase | structure |
|---|---|---|---|---|---|---|
| without facts | 0.87 | 0.92 | 0.90 | 0.99 | 0.81 | 0.77 |
| facts, a full vote | 0.85 | 0.92 | 0.89 | 1.00 | 0.74 | 0.77 |
| facts, half a vote | 0.87 | 0.92 | 0.90 | 0.99 | 0.81 | 0.77 |

With a full vote two questions gained (spectral granular synthesis to
rank 1; the German question about independent components from 10 to 7)
and three lost (two paraphrases from rank 1 to 2 and 3, the S-transform
question from 8 to 10). With half a vote, two questions moved by a rank
or two and nothing else did. No more weights were tried: 62 questions
are too few to tune one on.

## Why not, and when again

The set has no question shaped like a fact: "which papers use NMF",
"who built the Buchla 259", "what does this recipe call for". Those are
where a list of facts should help, and where `traverse` and `connect`
answer today. The list comes back if such questions join the set and the
list wins on them without losing the rest. The found bug of the evening
came from this work: a graph label holding a quote and a colon made every
search for "matrix" fail (`docs/log.md`, 2026-10-05).
