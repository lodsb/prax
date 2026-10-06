# The judge of a pair, with a person's decisions beside it

2026-10-06, the 27B on the 4090. The plan's "precedents for the judge"
(from Utopia's adjudication): does the local model judge a likely pair
better when it is shown the decisions a person made on pairs like it?
Not adopted. The measurement found a fault in the measurement itself,
and with it a better fit for the judge that waits for the user's word.

## How

`scripts/eval_confidence.py`, as on 2026-10-05: `gold` takes the pairs
a person decided on the Review page, `ask` puts each to the local model
for one token, and `platt` fits Platt's map and scores it by five-fold
cross-validation. New is `--precedents K`. Before the question, the
prompt shows the K decisions most like the pair
(`resolution.precedents_for`): of its type first, then by how alike the
names are, character 3-grams of both names. The pair's own decision is
never among them, so each pair is asked as if the rest were what a
person had decided before. A person's decision has no reason recorded
with it, so a precedent is the pair and its answer only.

## The set was not what the judge is asked

`gold` took 296 decisions. 56 of them are of type `split-names`: pairs
of the heal check of that name, two entities with one name ("MIDI" and
"MIDI") that a person kept apart on what their documents say. No judge
of names can decide those, and the nightly judge is never asked them.
With six precedents the judge was sure and wrong on six of them, all
"MIDI" against "MIDI", shown "Medium" against "Medium": yes. The fit in
the host's `prax.yaml` (2026-10-05) was made with them in. `gold` now
keeps only pairs of an entity type: 240, 160 of them the same.

## The answer, on the 240

| prompt | agreement | ECE | settled at 0.95 | of those, agreeing |
|---|---|---|---|---|
| plain | 0.762 | 0.037 | 32% | 74/76 |
| 3 precedents | 0.779 | 0.017 | 27% | 63/65 |
| 6 precedents | 0.800 | 0.038 | 29% | 67/70 |

Agreement and ECE are cross-validated. Precedents raise the agreement
on the pairs the judge is unsure of, by up to nine pairs. The nightly
judge acts only on what it settles at 0.95, and there the plain prompt
settles more pairs and is right a little more often. Every difference is
two to three pairs, so none of it is a reason to change the prompt. The
code stays as an option of the script.

## The fit that waits

Fitted on the 240 pairs of entity types, the plain prompt's map is
`{a: 0.8183, b: 0.8627}`, against `{a: 0.4877, b: 0.4917}` in use:

| fit | settled at 0.95 | of those, agreeing |
|---|---|---|
| in use (2026-10-05, the 296 with the split pairs) | 11% | 34/34 |
| the 240 of entity types | 32% | 74/76 |

The new fit settles three times as many pairs without a person, at 97%
agreement instead of 100%. Each settled "same" is a merge the nightly
round makes, and `unmerge_run` takes a round back. That is the user's
choice.
