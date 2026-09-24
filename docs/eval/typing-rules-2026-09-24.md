# Are the typing rules any good?

2026-09-24. The review queue's rules (`prax.review`, `decide` and
`decide_unmapped`) have written 22,855 edges and nobody had asked whether
they are right. `docs/stratification.md` step 4 said an afternoon would
answer it. It took an afternoon, and the first answer was that the
question could not be asked.

## The question could not be asked

A rule's decision was not recorded. Every edge the pass wrote carried the
producer `typing-rules` and nothing else, so of 22,855 edges none says
which of the sixty-odd rules decided it. Precision per rule is therefore
not computable from the store, only precision for the pass as a whole.

And the pass as a whole has no signal either. 761 of its edges are
retired, 3.3%, which looks like a quality number and is not: 684 of them
were retired in one minute and 57 in another. Those are `retire_run`
withdrawing two passes wholesale, not a judgement on an edge.

**So the first change is that a rule signs its work**: the producer is
`typing-rules/<rule>` from now on — `typing-rules/flip-authored_by`,
`typing-rules/cites->uses`. The prefix is kept, so everything that asks
for the pass still finds it with `LIKE 'typing-rules%'`. Nothing
retrospective: the 22,855 edges already written stay unattributed.

## What could be measured

The typing *model* pass (`typing:qwen3.6-35b-a3b…`) answers the same
question on the same queue and wrote 7,521 edges. It is not truth — it is
a 35B model — but where it and the rules speak about **the same pair of
names in the same document**, agreement is a signal and disagreement
names a set worth reading.

Only that comparison is made here. A looser one, counting any model edge
touching one of the names, reports a 47% disagreement rate that means
nothing: the model writing some other edge about the same entity is not a
contradiction.

### The rules are mostly silent

| | items | |
|---|---|---|
| the rules had no opinion | 7,172 | 93% |
| a different edge for the same pair | 266 | 3% |
| the same edge | 265 | 3% |
| the rules would have dropped it | 35 | 0% |

Of 7,738 items where both passes spoke about the same pair, the rules
have nothing to say about 93%. What they are silent on is not exotic —
`about` (2,195), `cites` (963), `part_of` (906), `mentions` (767),
`affiliated_with` (522), `uses` (499). The commonest relations in the
library are the ones the rules do not cover, which is why the typing
model earned its place and why 2,048 of the 2,279 items still open would
stay open if the rules ran again tonight.

Where both do speak, they split 265 to 266. A coin flip in aggregate —
but the aggregate is the wrong unit.

### Per rule, it is not a coin flip at all

| rule | same | differ | agreement |
|---|---|---|---|
| `funded_by` | 134 | 9 | **94%** |
| `developed_by` | 12 | 1 | **92%** |
| `affiliation` | 37 | 6 | **86%** |
| `flip-authored_by` | 4 | 1 | 80% |
| `mentions` | 15 | 9 | 62% |
| `part_of-organizations` | 5 | 5 | 50% |
| `published_in` | 20 | 35 | 36% |
| `self-name` | 5 | 12 | 29% |
| `located_in->written_at` | 2 | 5 | 29% |
| `authored_by` | 6 | 21 | 22% |
| `cites->uses` | 3 | 27 | **10%** |
| `retype-cites-paper-document` | 2 | 21 | **9%** |
| `written_at` | 1 | 12 | **8%** |
| `affiliated_with->written_at` | 2 | 54 | **4%** |
| `retype-cites-paper-work` | 0 | 7 | **0%** |
| `about->mentions` | 0 | 8 | **0%** |
| `affiliation->part_of` | 0 | 7 | **0%** |

Three rules carry most of the agreement and four carry most of the
disagreement. `affiliated_with->written_at` differs from the model on 54
of 56 cases, which is the largest single disagreement in the set.

### Reading the disagreements

`cites->uses` turns a citation into "the document uses this thing". The
model says `mentions` or `about`:

```
rules: proceedings of the … uses soundcloud
model: proceedings of the … about soundcloud

rules: music emotion clas… uses mirex mood classif…
model: music emotion clas… mentions mirex mood classif…
```

A proceedings volume does not *use* SoundCloud. On these the model is
plainly right and the rule is manufacturing false edges — which is
exactly the brittleness Snorkel's argument predicts for a rule that *is*
the classifier rather than a vote
(`docs/stratification.md`).

## What follows

1. **Done**: a rule signs its work, so the next pass is measurable
   directly rather than through this proxy.
2. **Before any rule changes**, let the stamped pass run for a while and
   measure precision per rule against retirement and against the model,
   with the rule known rather than inferred.
3. **The four worst rules are candidates for deletion, not repair.**
   `affiliated_with->written_at`, `retype-cites-paper-work`,
   `about->mentions` and `affiliation->part_of` decide 76 edges between
   them and agree with the model on 2. A rule that is wrong more often
   than not is worse than no rule, because the item it closes wrongly
   never reaches the model that would have got it right.
4. **The silence is the bigger number.** 93% is not a gap a few more
   rules would close; it is the shape of the problem. The rules are worth
   keeping for what they are good at — `funded_by`, `developed_by`,
   `affiliation`, all above 85% — and the rest of the queue belongs to
   the model.

## Caveats

- The model is not ground truth. A disagreement means one of the two is
  wrong, and the examples above are read, not counted. A rule with a low
  agreement rate is a candidate for inspection, not an established error.
- Only 7,738 of 39,883 linked items had both passes speaking about the
  same pair, so a rule with fewer than about ten comparisons here (
  `flip-authored_by`, `cites->about`, `developed_by->published_by`) is
  not measured, only glimpsed.
- Nothing was written to the store by this measurement.
