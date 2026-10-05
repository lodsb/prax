# The extraction prompt with standard names, and with world dates

2026-10-05. Two changes to the extraction prompt, each behind a switch
so it could be measured before it touches the library:

- **Standard names** (AN, "more meaning in standard words"): each
  relation's line in the prompt also gives its `same_as`
  (`authored_by (document -> person) [schema:author]: …`).
  `extraction.standard_names`, `PRAX_EXTRACT_STANDARD`.
- **World dates** (AL step 5): a triple may carry `from`/`to` (YYYY,
  YYYY-MM or YYYY-MM-DD; `to` may be `unknown`), kept only when its year
  is in the triple's own quote (`extraction.checked_date`).
  `extraction.world_dates`, `PRAX_EXTRACT_DATES`.

Measured with `scripts/bench_extractor.py` (it applies nothing) on the
local 27B (`server-27b-u`), which shared the card with the figure
readings. One run per setting, so a difference of a few triples is
within what a run varies.

## Five documents of four modules

Two papers, an essay, a manual, an audio fingerprinting paper (ids 685,
7625, 10140, 13056, 9269); 92 live edges of other producers to compare
against.

| prompt | valid triples | overlap with live edges |
|---|---|---|
| as it is | 72 | 25 |
| with standard names | 73 | 27 |
| with world dates | 79 | 29 |

The essay (10140) gives few valid triples whatever the prompt: that is
the society module, not these switches.

## Five documents that state dated facts

Chosen by their text: a company history ("joined … in 1975"), an
introduction to creativity research ("founded in"), an essay on
electronic music ("from 19.. to 19.."), a feature on desktop music
("since 19.."), a record review ("died in"). Ids 9213, 4479, 613,
3511, 12714.

| prompt | valid triples | overlap | dated triples | dates kept |
|---|---|---|---|---|
| as it is | 89 | 26 | 0 | 0 |
| with world dates | 93 | 19 | 0 | 0 |

The model gave no date at all. Its twenty triples go to what the prompt
ranks first (authors, venue, what the document is about, methods,
tools), and a dated fact is rarely among them; the optional fields of
the line format are as rarely used as `src_as`/`dst_as`.

## Decision

Both switches stay off. Standard names change nothing measurable for
some 70 more input tokens a document. World dates do not come from the
general extraction: they want a pass of their own over the sentences
that hold a year and a relation that lasts (an affiliation, a part of, a
law in force), with the same check on the quote. That is the plan's next
step for AL's world time.
