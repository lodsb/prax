# When a fact holds: the `worlddates` step, measured

2026-10-06, the desktop, the 27B (`server-27b-u`). AL step 5's open item:
world dates come from a pass of their own, since the general extraction
gave none on five documents that state them
(`docs/eval/extraction-standard-names-and-dates-2026-10-05.md`).

## What the step does

`prax.graph.worlddates`, the `worlddates` step:

1. **The sentences.** A document's `text` chunks, split into sentences.
   A sentence is read when it holds a year outside a citation ("(Smith
   2003)", "[12]") and a word of lasting or happening from the lexicon's
   `world_time:` section ("since", "founded", "joined", "bought",
   "until"). At most 12 a document.
2. **The model** sees them numbered, with only the relations a date fits:
   kind `state` or `event`, and an `eternal` one done by someone
   (`developed_by`, not `authored_by`). A grammar bounds the answer to
   `none` or up to eight triple lines, each with a `from` or a `to`.
3. **The checks**, without a model: the quote is in one of the sentences,
   each date's year is in the quote (`extraction.checked_date`), neither
   end is a date or a stand-in ("author", "unknown"), and the ontology
   takes the triple.
4. **The judge.** Each fact that passed is put to the same model as a
   sentence ("Live developed by Ableton, in 2001.") against the sentence
   it came from: yes or no, P(yes) from the first token's log
   probabilities. A fact needs `steps.worlddates.supported`, 0.9.
5. **The door** checks again what a worker posts, against the sentences
   it reads itself. An undated edge of the same fact and document is
   ended (`edge_endings`, `corrected_by`) and stated again with the dates;
   a fact the document had not stated is linked new. Producer
   `world-dates:<model>`, run `worlddates-<stamp>`; `restore_run` undoes
   a run whole. The document is stamped `meta.world_dates` with its text
   hash, so a new text is read again.

## How much there is to read

On 600 documents chosen at random, 238 (40%) hold at least one sentence
to read, 1,057 sentences in all at the cap of 12. Over the library that
is some 5,300 documents with a model call and 8,000 stamped without one.

## What the first prompt got wrong

Three runs of 30 to 50 documents (`scripts/bench_world_dates.py`, which
applies nothing) found what the checks and the prompt now say:

- The prompt showed the separator as the word `<TAB>`, and the model
  wrote it into names. It now shows a tab, as `lineformat` does.
- Asked for an end, the model wrote `to=unknown` on every fact, and none
  of their sentences said the fact had ended. This reading writes no
  "ended, date unknown"; the grammar has no `unknown`.
- With no relation for "released in", it made the year an entity
  (`1984`, `June 1977`). A date is never a name now, and `developed_by`
  is offered.
- A purchase was not read at all: "bought" was not among the cue words.

## The judge, and where its line is

Of 45 facts that passed the checks on the second sample, a reading by
hand found 23 right. The rest are a real date of the wrong fact: a
person's life dates for a device, the year in a grant number, a title's
year, a header line, a vague end ("various bands", a concept).

| judge's line | sample A (45 checked, 23 right) | held-out (37 checked) |
|---|---|---|
| none | 45 kept, 23 right | 37 kept |
| 0.5 | 15 kept, 14 right | 11 kept, 8 right |
| 0.9 | **12 kept, 12 right** | **6 kept, 6 right** |

Sample A was read with the facts written as the judge now reads them,
which was chosen on it: an event's place "in" its date, a state "since"
it, dates in words. The held-out sample (60 documents, another seed) was
read once, after. At 0.9, 18 of 18 kept facts are right, at about half
the recall. A wrong date written as evidence costs more than a missing
one, as with the pair judge's 0.95.

## Cost and yield

About 4 s a document with sentences, both calls included; 6 hours of the
card for the library. On the held-out sample, 6 facts from 60 documents
at 0.9, so some 500 dated facts over the library. Most are new facts
rather than dates for existing edges: few of them were among the
general extraction's twenty triples.

## Decision

The step is built and watched, and off: `STEP_DEFAULTS` gives it no
model, so it runs on a host only when `prax.yaml` names a served one for
it (`steps.worlddates.model`). Turning it on is the user's call: it
writes some 500 facts into the graph over a night or two.
