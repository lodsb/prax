# Resolving a paper's own reference list against the library, 2026-09-20

Trigger (`niggles.txt`, "model"): "citations of papers does not work
yet: would be nice if we had a resolution of papers/sources that are
cited and are in the library (probably a probability)". Diagnosis on
the store: `prax import citations` (Crossref, by DOI) had run over
9,236 documents and resolved 1,545 — the ones with a DOI; 7,691 stayed
unresolved. Its 25,641 `cites` edges land on a library document 2,075
times (785 distinct titles), matched by exact DOI or exact title. The
reference lists of the other four fifths are in the parsed text all the
same: 3,437 documents have chunks under a References/Bibliography
heading, 147,384 entries in all.

Change measured: `prax.references` (rules, no model, no network) splits
a bibliography into entries and reads each one — surnames, year, title,
a printed DOI or arXiv id — and `scripts/eval_references.py` matches
every entry against the library: an id when printed, else the document
field index's top ten (`documents_fts`: title, creators, venue) scored
by `references.similarity` (a sequence ratio over normalised titles,
raised by a shared surname and a year within one, lowered by a missing
surname or a year two or more apart; capped when the shorter title's
content words are not nearly all in the longer, or the longer opens
with words the shorter lacks and no colon sets them apart) and decided
by `references.match` (threshold 0.82, a margin of 0.06 between the
best and the runner-up: closer than that, and both over the threshold,
is "ambiguous" — twins of one paper in the library, or two papers under
one title — and every tied candidate is kept).

The labelled set is Crossref's own links into the library, for the
citing documents whose bibliography the splitter sees (489 documents,
1,092 links). It is incomplete — Crossref's reference lists carry a
title for about half the entries — so "false positives" against it are
mostly links Crossref missed; 30 of them read by eye at each stage
decided the rules above.

| stage | entries | sure links | ambiguous | precision vs Crossref | recall vs Crossref | new links (documents Crossref never resolved) |
|---|---|---|---|---|---|---|
| first rules, 300-document sample | 12,889 | 680 | 108 | 0.66 | 0.54 | 472 from 108 documents |
| the author run read structurally, Markdown links and HTML tags stripped | 12,830 | 704 | 113 | 0.62 | 0.56 | 501 |
| coverage cap, other-head cap, venue tails cut, year penalty | 12,830 | 638 | 244 (all tied candidates) | 0.61 | 0.58 | 454 |
| Elsevier's one-paragraph lists split at their inline markers, `\(10\)` unescaped, "Jr."/"III" | 13,323 | 701 | 271 | 0.65 | 0.80 | 469 |
| the whole library | 147,384 | 6,551 | 2,735 | 0.61 | 0.75 | 5,629 from 1,236 documents |

What the by-eye reading of the sure matches under 0.95 says, at the
last stage (30 of 38 in the sample): the matches are right except for
the same-title-different-work cases that no title rule separates — a
book and its Matlab companion (0.94, the companion's authors overlap),
a paper and its extension by the same authors with one word changed
("source" for "speech", 0.90), a conference paper and its journal
version (0.94; the year penalty needs both years and the library often
has none). Exact title matches are 590 of the 680 sure links in the
sample; the score is honest about the rest.

Where the misses against Crossref went, at the last stage (51 in the
sample): 18 titles not in the bibliography text at all (a heading the
chunker did not see as the reference list, or a list the extractor
lost), 14 entries read but scored under the threshold (an editor's
"(Ed.) DAFX: Digital Audio Effects, 2nd ed.; Wiley" stays 0.67 because
the title never ends), the rest entries still mangled by an extractor's
line shapes.

Decision: build the `references` pass on these rules. Edges
`paper --cites--> paper`, confidence `EXTRACTED` for an id match,
`INFERRED` for a sure title match with the score in the evidence,
`AMBIGUOUS` for each tied candidate; producer `references`, a run per
pass, `meta.references` as the stamp (entries, linked, at), re-run when
the text is read again. 6,551 sure and 2,735 ambiguous links from 1,612
documents against the 2,075 Crossref found, and 5,629 of them from
documents Crossref could not resolve. The Crossref edges stay: they
name the works outside the library too. A model is not needed for the
entries the rules cannot shape; the eval says the rules read 99.9 %
into a title, and what they get wrong is the unmatchable kind.

Reproduce: `python scripts/eval_references.py [--limit 300] [--show 25]
[--threshold 0.82 --margin 0.06]` on the live store (reads only, four
minutes for the library).
