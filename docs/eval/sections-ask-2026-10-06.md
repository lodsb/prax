# What a book's chapter summaries are worth to `ask`

2026-10-06, the 27B. The second reason for the `sections` pass was a
cheaper way into a long book than its chunks
(`docs/eval/sections-2026-09-25.md` measured the first, search). Asked
of `ask`, they made no difference that the noise does not also make.

## How

`tests/eval/questions-sections.yaml`: 30 questions about the middle of
long documents, one a document, written by a subagent from a passage
44–51% of the way into each text. The chapter summaries and the
document's own summary were not read, since they are what is measured.
15 are research books and theses, 8 manuals of software and gear, and 7
other long documents.

Two doors answered them. The live door kept the section lines in the
document field. A door on a copy of the store had the field of its
2,006 sectioned documents rebuilt without them, and their field vectors
embedded again (19 s). `scripts/eval_ask.py --steps 0`: one answer from
the bundle, no surfing, so the arms differ only in what search hands the
model. Same model, same questions, one run each.

17 of the 30 documents have section summaries. The other 13 have none
yet, documents the pass has not reached, so both doors are the same for
them: they are the control, and what changes there is the noise of
asking twice.

## The answer

| documents | door | sources | cited | match |
|---|---|---|---|---|
| 17 with sections | with them | 17 | 17 | 12 |
| | without | 16 | 16 | 12 |
| 13 without (control) | live | 9 | 8 | 5 |
| | copy | 10 | 10 | 5 |

`sources`: the expected document among the passages; `cited`: the
answer cites it; `match`: a cited passage of it holds the expected
words. With sections one question more found its book (doc 9772, about the
audio oracle). In the control two questions changed, where
nothing that matters differs. One question of 17 is within that.

## Why

A question about the middle of a book finds the book through the chunk
that holds the passage. 26 of the 30 expected documents were among the
sources in each arm, and every passage-level hit carries its book with
it. The document field says what a whole document is, and these
questions do not ask that.

The sections still earn their place in search, where a query
paraphrases a chapter rather than naming a fact in it (keyword hit@10
0.087 to 0.130, vector 0.210 to 0.300, on 2026-09-25). They earn none in
`ask` on this set. A surfing `ask` (steps above 0) was not run: it
reads further into a document by words, which the chunks serve too.
