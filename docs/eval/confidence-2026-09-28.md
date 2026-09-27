# A measured confidence: the local model against the adjudicator

2026-09-28. Stage Q (docs/PLAN.md). `scripts/eval_confidence.py` over
6,126 labelled pairs. 2,704 are declines Opus 5 recorded on 2026-09-17.
3,422 are its merges, reconstructed: the unstamped merges of the likely
types that differ after normalization and pass the 0.92 name cosine
(Opus made 3,683). The local model (qwen3.6-35b, llama-server) was asked each pair
alone, in the adjudicator's words, for one token; P(yes) is the mass on
"yes" against "no" among the top ten. All 6,126 answered, in 620 s at
about ten pairs a second.

## Calibration

Fitted on half the pairs, measured on the other half.

| map | Brier | ECE | agreement with Opus at 0.5 |
|---|---|---|---|
| raw | 0.161 | 0.115 | 0.794 |
| Platt | 0.152 | 0.070 | 0.796 |
| isotonic | 0.147 | 0.022 | 0.799 |
| always 0.55 | 0.247 | | |

The raw probability is overconfident: said 0.98, agreed 88%; said 0.02,
disagreed 15%. Isotonic regression maps it to a number that means what
it says: in every bin with more than a few pairs, "said" and "happened"
are within 0.06, and within 0.01 at the top.

| settle when (isotonic) | settled | agree with Opus |
|---|---|---|
| p ≥ 0.95 or ≤ 0.05 | 5.6% | 0.929 |
| p ≥ 0.9 or ≤ 0.1 | 18.8% | 0.932 |
| p ≥ 0.8 or ≤ 0.2 | 56.9% | 0.886 |

## The labels

The strongest disagreements are the label's error far more often than
the model's. The local model said "same" at 1.000, and Opus "different",
for these:

- cutoff / cut-off frequency
- realtime / real-time systems
- parallelization / parallelisation
- shifted NMF spelled -isation and -ization
- Synthesis ToolKit / Synthesis ToolKit (STK)
- infrared filter / IR filter
- mu- and µ-recursive functions

The other way round, the model said "different" at 0.000 where the
labels say "same": preorder / postorder traversal.

Opus does not decline "cut-off frequency" against "cutoff frequency" one
pair at a time. `ClaudeAdjudicator` asks forty pairs in one call for a
list of booleans, and fills a list that comes back short with `False`. A
list one short or one long shifts every answer after the gap onto the
wrong pair. That the declines of plain spelling variants cluster fits
that, and it is a likely fault in the paid tier: the declines it recorded
are not all Opus's judgement. Unconfirmed; the check is the batches'
positions, which were not kept.

So agreement with these labels understates the local model, and "agree
with Opus 93%" at a threshold is a floor, not the accuracy.

## What it means

- The local model's probability, once mapped, is a usable confidence.
- The gold sample (docs/review.md, the Review page) is still what a
  decision about acting on a threshold needs: labels a person gave.
- The adjudicator should answer each pair by its number, not by its
  position in a list, before it is used again.
