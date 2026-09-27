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

## Asked again, by pair number

The adjudicator now names each pair it answers (`resolution.answers_by_number`),
and a pair it does not answer is neither merged nor declined.
`eval_confidence.py relabel` put the same 6,126 pairs to Opus 5 again, forty
a call in a shuffled order: $3.18, fifteen minutes, every pair answered.

| recorded | asked again: same | different |
|---|---|---|
| same | 2,978 | 444 |
| different | 640 | 2,064 |

Opus agrees with its recorded answers on 82% of the pairs. The spelling
variants it had declined come back "same" (continuous wavelet transform and
transformation, Wiener filtering), which fits the shift. But most of the
changes either way are one boundary: a thing against a narrower one, a
version, or a part (Fourier transform and DFT, Max/MSP and Max/MSP 5,
Freesound and its API, spatial audio and spatial audio rendering). The
question as asked does not decide those, and Opus decides them differently
each time.

Some recorded merges are plainly wrong, and they are in the graph: EUSIPCO
2018 and 2022, ISMIR 2010 and 2011, TEI '08 and '10. Venue editions were
folded into one another.

The local model against the new labels:

| map | Brier | ECE | agreement at 0.5 |
|---|---|---|---|
| raw | 0.102 | 0.047 | 0.863 |
| Platt | 0.100 | 0.021 | 0.864 |
| isotonic | 0.101 | 0.025 | 0.864 |

| settle when (isotonic) | settled | agree with Opus |
|---|---|---|
| p ≥ 0.99 or ≤ 0.01 | 20.7% | 0.995 |
| p ≥ 0.95 or ≤ 0.05 | 43.9% | 0.978 |
| p ≥ 0.9 or ≤ 0.1 | 64.7% | 0.950 |

Against labels that are not shifted, a threshold of 0.9 settles two pairs
in three at 95% agreement, where it settled one in five before. Its
strongest disagreements left are all the same boundary: "user modeling"
and "user model", "digital delay" and "digital delay line".

## What it means now

- The shift was real, and it was not the whole story. The rest is a
  question nobody has answered: is a version, an edition, a narrower
  method or a part the same thing? A rule for that, written into the
  question both models are asked, comes before the gold sample, or the
  sample measures the vagueness again.
- The venue editions merged into one another are to be found and undone.

## Asked with a rule

`resolution.SAME_RULE` says what "the same thing" means. Spelling, plural,
abbreviation, translation and filler words are the same. A narrower kind, a
version or edition, a part or interface, a task and its tool, and names one
word apart are not. The Review guide gives the person the same list. Both
models were asked the 6,126 pairs again with it ($3.30 for Opus).

With the rule, Opus says "same" to 2,949 pairs, not 3,618; it agrees with
its answers without the rule on 88.7%. What it now keeps apart is mostly the
narrower kind ("audio filter" and "digital audio filter", "zero-delay
feedback" and "zero-delay feedback filter").

The local model against Opus, both with the rule:

| map | Brier | ECE | agreement at 0.5 |
|---|---|---|---|
| raw | 0.093 | 0.060 | 0.877 |
| Platt | 0.085 | 0.019 | 0.885 |
| isotonic | 0.085 | 0.018 | 0.884 |

| settle when (isotonic) | settled | agree with Opus |
|---|---|---|
| p ≥ 0.99 or ≤ 0.01 | 33.0% | 0.993 |
| p ≥ 0.95 or ≤ 0.05 | 43.0% | 0.989 |
| p ≥ 0.9 or ≤ 0.1 | 60.0% | 0.972 |
| p ≥ 0.8 or ≤ 0.2 | 71.5% | 0.960 |

The rule makes the question decidable, and both models answer it more
alike: at 0.9 the local model settles 60% of the pairs at 97% agreement.
Its strongest disagreements left are "motif discovery" and "motif discovery
method" (a filler word, which Opus called different), and a German name
against its English one ("Bibliotheksbenutzer" and "library use"), which
are different anyway.

The gold sample is next: a person's decisions under the same rule, to say
which of the two models is right where they differ.

## The conference editions

Of the 23,970 merges in the graph, 117 join names with different numbers.
Opus under the rule called 84 of them different; 77 are two things
wrongly joined (68 venue editions, seven tools such as Am2904 and Am2910,
x86-64 and x86). The other seven are a file name joined to its paper's
title and two names garbled by an extractor, which the merge tidied.

They were split through the door on 2026-09-28, which records each pair as
decided "different" by a person. Opus chose them, so they are listed in
`confidence-2026-09-28-splits.json` and stay out of the gold sample.
