# A follow-up question, and the vote of what the first was about

2026-10-06. The plan's "a focus entity as a vote" (from Graphiti's node
distance), measured on a copy of the store before it was wired into
`ask`. Not shipped: it moved nothing on the new follow-up set. The set
and its script stay, and the first number they give is for what `ask`
already does.

## The set

`tests/eval/followups-library.yaml`: 30 cases, each a first question and
a follow-up that leans on it ("Has it been applied to guitar
amplifiers?" after a question about DDSP). The follow-up never repeats
the thing's name. 19 cases are research, 5 kitchen, 4 studio and 2
computing. The expected documents were chosen by reading them, never
from the graph's edges, so a vote that uses the graph is not graded by
its own data. 12 were checked in their text and 18 from the abstract or
the passage the search returned. In 28 cases the expected document is
not the first question's own top hit.

`scripts/eval_followups.py --store <a copy>` runs it. A copy is the
database read with SQLite's backup API (36 s for 3.1 GB) and the vector
files copied.

## What `ask` does today

`ask.search_query` adds the previous question's words to a follow-up
that is short or points back ("it", "that"). Its gain had never been
measured:

| arm | hit@1 | hit@3 | hit@10 | MRR |
|---|---|---|---|---|
| the follow-up alone | 0.40 | 0.60 | 0.70 | 0.510 |
| joined, as `ask` searches | 0.63 | 0.73 | 0.90 | 0.716 |

+0.21 MRR, and six more expected documents in the top ten.

## The vote

`store.focus_entities` looked up the first question's runs of one to
four words, longest first. It kept the graph entities whose name or
label a run is, at most two and one per name. A name stated in more than 300
documents is a field. A lone word held by more than 100 document fields
was skipped. Without that rule the first version took "string", "method",
"work" and "library" as the focus. Each is a dozen small entities of
different types, and only the text says they are generic.

`_focus_vote` then scored the fused candidates. A fact about the focus
was worth 2 points, and a fact about one of its first-hop neighbours 1
point (neighbours stated in at most 50 documents). The candidates with
points got one more vote in the fusion, ranked by their points, as the
domain prior does. Only a candidate the
lists found was moved, so a hidden document could not come in through
it. The code is `docs/eval/followups-focus-vote-2026-10-06.diff`.

On the joined query, with the focus the second rule found (DDSP, BLEP,
FAUST, onset detection, pumpkin, mapo tofu):

| arm | hit@1 | hit@3 | hit@10 | MRR |
|---|---|---|---|---|
| joined | 0.63 | 0.73 | 0.90 | 0.716 |
| focus, weight 0.5 | 0.60 | 0.80 | 0.87 | 0.701 |
| focus, weight 1 | 0.60 | 0.73 | 0.87 | 0.689 |
| focus, weight 2 | 0.63 | 0.73 | 0.87 | 0.700 |

Two cases gained: the pumpkin soup with kimchi (rank 4 to 1) and the
graph-based RAG paper (4 to 3). Three lost:

- the DDSP guitar amplifier, rank 1 to 2, 5 and 7 as the weight grew;
- the differentiable wave digital filters for diodes, 1 to 4 and 7;
- SuperCollider 3 against 2, 9 to out of the top ten.

## Why not, and when again

The vote rewards what the graph knows, and in the cases it lost the
graph did not know the answer. The DDSP guitar amplifier (doc 9778) and
the differentiable wave digital filters for diodes (doc 9782) state no
fact about the focus entity at all: their extraction never linked them
to DDSP or to wave digital filters. Without the vote both were rank 1.
With it, the documents the graph does connect to the focus moved above
them: the DDSP review and the singing vocoder in the first case, the
Lambert-function and op-amp papers in the second. The previous question's
words already bring the neighbourhood in, through the text, which holds
the link the graph is missing.

It comes back when the graph links a paper to the method it builds on,
measured on this set again: the two documents above are the test. A
vote through the neighbour the follow-up itself names ("guitar
amplifier") rather than through the focus's whole neighbourhood is the
other shape worth a run. Thirty cases are too few to tune a weight on,
so none was tuned.
