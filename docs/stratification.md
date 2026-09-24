# The strata: what the patterns are for, and where they belong

2026-09-24. Written after the summaries and vocabulary passes, because
both of them added patterns to a codebase that already had a hundred and
fifty of them and nowhere to put one.

This is an inventory, a reading of what comparable systems do, and a plan
that moves the patterns rather than adding to them. Nothing here is a
rewrite: every stratum below already exists in the code, unnamed and in
several places at once.

## What is there

`re.compile` appears 158 times across `src/prax`. That number on its own
says nothing — a parser of a text format is regular expressions and
should be. What it hides is that the 158 do five different jobs, and that
two of the five have no home, so each module writes its own.

| stratum | what it is | where it is now | roughly |
|---|---|---|---|
| A. the artifact's own grammar | the markup prax writes into a text and reads back | 11 modules | 45 |
| B. what a model said, cleaned | preambles, fences, quotes, labels off an answer | 9 modules | 15 |
| C. knowledge about the world | which words mean an organization, a person, a unit, a tool | 4 modules | 25 |
| D. a domain's own parser | bibliography, ingredient amounts, timestamps, glyphs | 4 modules | 45 |
| E. housekeeping | slugs, tokens, tracking parameters, surrogates | scattered | 22 |

D and E are fine. A, B and C are the duct tape, for three different
reasons.

### A. The artifact's grammar has no owner

prax writes a format and reads it back: `--- end of page.page_number=N
---` between pages, `![caption](figure:<sha256>)` for a picture, `*Read
by …*` under one, `## Comments` and `## Figures` as section marks,
`<!-- prax:ask id=q1 -->` around a standing question, `[title](#doc/N)`
for a link to a document. It is a real format with a real specification —
`CLAUDE.md` describes most of it — and no module owns it.

So it is written in one place and matched in others, by hand, each time:

- the page mark is *written* in four places in `parsers/__init__.py` and
  *matched* by three separate identical regexes, two of them in the same
  file (`chunking.py:93`, `figures.py:51`, `figures.py:328`).
- a Markdown heading has five spellings across `chunking.py`,
  `ingredients.py`, `importers/project.py`, `importers/chats.py` and
  `parsers/__init__.py`, three of which differ in what they capture.
- "a word" is `_WORD` in six modules with four different definitions:
  `\w+`, `[A-Za-z]{2,}`, `[A-Za-z][A-Za-z-]{3,}`, `[^\W\d_]+`.

Every new reader of the format — `furniture.py` for advertising,
`ingredients.py` for a recipe's list, `blocks.py` for an ask block — adds
its own copy of the neighbouring patterns because there is nothing to
import. That is how a format drifts: the writer changes and a matcher
somewhere does not.

### B. Every module that calls a model cleans up after it separately

`summaries.py` strips a preamble, a code fence, quotation marks and the
prompt's own labels. `vocabulary.py` strips a preamble, quotation marks
and a trailing full stop. `titles.py` strips a label, quotation marks and
`<tool_call>`. `lineformat.py`, `typing_pass.py`, `surf.py` and `ask.py`
each have their own. They were written at different times, they catch
different failures, and a failure caught in one is not caught in the
others — which is exactly how four summaries came back wearing
`Document title:` on 2026-09-24 after `titles.py` had been stripping
labels for a fortnight.

This is one function with one test suite, and it is nine.

### C. Knowledge about the world is in Python

`review.py` decides what an extracted name *is* from a list of words: a
name containing `universit|institut|laborator|…|records|project` is an
organization; a name matching `^[A-ZÀ-Ý]\w+( [A-ZÀ-Ý]\w+){1,4}$` is a
person; a name containing `tool|software|library|plugin|…` is a tool, a
method or a dataset by a lookup table. `ingredients.py` knows units.
`furniture.py` knows what an advertisement sounds like. `references.py`
knows what a venue is called.

These are lexicons for the ontology's own types, and the ontology is
already data: YAML, versioned, composed per domain, with a `naming:` key
added this week. The lexicons are code, unversioned, and invisible to the
ontology that they are about. Growing `research.yaml` does not grow
`review.py`, and nothing says it should.

## What comparable systems do

Four of the five strata have a well-worn answer somewhere.

**Annotation belongs in one layer, and analysers are plugged into it.**
UIMA and GATE are the two long-standing text-analytics architectures, and
both are built the same way: a document has a single standoff annotation
store (UIMA's [Common Analysis
Structure](https://erickow.com/posts/anno-models-uima.html)), annotators
read and write typed feature structures against character offsets, and
the pipeline is a declared sequence of them. prax already has the store —
the `chunks` table is standoff annotation: a kind, a character range and
page, a heading path, a `data` blob. What it lacks is the type system
beside it and one place where the format is declared, so the annotators
stop re-deriving it.

**Rules are better as votes than as the decision.** Snorkel's contention
is that rule-based classifiers are brittle because [the rules *are* the
classifier](https://arxiv.org/abs/1711.10160) and generalize to nothing
they were not written for; its labelling functions instead vote, and what
is learned from them is the classifier. prax's `review.py` is the rule
version of a typing model, and prax already holds the missing half: a
review queue of decisions a person made, which is labelled data nobody
reads back.

**Reference parsing by rule has a known ceiling.** GROBID parses
bibliography with a [cascade of sequence
models](https://grobid.readthedocs.io/en/latest/Principles/) and reaches
F1 0.87 where [rule- and regex-based parsers score
substantially lower](https://arxiv.org/pdf/1802.01168). `references.py`
is the regex version. That is a fair choice for a Pi-class target — it is
worth knowing it is a choice, and what it costs.

**Multilingual naming is solved, and prax half-implements the solution.**
[SKOS](https://www.w3.org/TR/skos-reference/) gives a concept labels per
language: one `prefLabel` per language, any number of `altLabel`s, and
the concept itself has no name. `entity_labels` (migration 20) is that
table — `kind` is already `pref | alt`, with a `lang`. Two gaps: SKOS
requires exactly one preferred label per language and prax enforces
nothing, and the vocabulary pass this week added `kind='was'`, which is
not a label kind at all but a record of an undo.

**Entity resolution has a vocabulary prax reinvented.** The tiers in
`resolution.py` — sure, initials, subtype folds, twins, likely, then an
adjudicator — are the [Fellegi–Sunter](https://arxiv.org/pdf/2008.04443)
shape: block, compare, then link / possibly-link / not-link with the
middle band going to clerical review. Splink and its relatives calibrate
the two thresholds from the data instead of choosing them by hand. prax
chooses them by hand.

## The plan

Five steps, each one green on its own, in this order. None of them
changes what the store holds; they change who owns a pattern.

### 1. `prax.markup` — the artifact's grammar, written once ✅

One module that both *writes* and *matches* every mark prax puts in a
text: the page mark, a figure line and its reference, `*Read by …*`, a
formula block and its number, the `## Comments` and `## Figures`
headings, the ask block's markers, a document link, a Markdown heading, a
table separator. Writers call it instead of formatting a string;
`chunking`, `figures`, `furniture`, `ingredients`, `blocks`, `titles`,
`references` and `store.pages` import it instead of writing the pattern
again.

The test is the one the current code cannot pass: for each mark, what the
writer emits is what the matcher matches. Today nothing checks that, and
the page mark exists in seven places.

*Done 2026-09-24.* `prax.markup` holds the page mark, the figure line
and its inlined form, the reading line, the Markdown heading, the table
separator, the display formula and its number, the section headings, and
the document link — each as a matcher *and* a writer. Eleven definitions
across six modules became references to one. The page mark is the measure
of it: written in four places in `prax.parsers` and matched by three
copies in two other modules, one file holding two of them; there is now
one source string and three compiled forms from it (a line, a whole text,
unanchored), and no `re.compile` outside `markup` mentions it.

The test is the one the old arrangement could not have: for every mark,
what the writer emits is what the matcher matches. `markup` imports
nothing of prax, which a test asserts — it is the bottom of the stack.

One thing measuring caught: `titles.head` matched the page mark
*anywhere* while the shared pattern is anchored to its own line. That is
a behaviour difference, so the unanchored form is kept as its own name
rather than quietly tightened inside a refactor.

### 2. `prax.answers` — what a model said, cleaned ✅

One `clean()` that takes the preamble, the fence, the quotation marks,
the trailing full stop, a reflected label and a `<tool_call>` tail off an
answer, and one `first_line()`. `summaries`, `vocabulary`, `titles`,
`typing_pass`, `lineformat`, `surf` and `ask` call it. The per-module
`acceptable()` checks stay where they are: *what a good answer looks like*
is the caller's business, *what the model wrapped it in* is not.

The test is every failure any of the nine has met, in one file. There are
about fifteen, and they are already written down across seven test files.

*Done 2026-09-24.* `prax.answers` holds `unwrap`, `first_line`,
`strip_tokens` and `is_label_line`, and 27 cases in one file — every
failure the seven modules had met separately, plus two the pooling found:
`Here's` has no space before the `'s`, and a label can end at a quote
instead of a colon (`The English name is "olive oil".`). `summaries`,
`vocabulary`, `titles` and `lineformat` call it; what a *good* answer
looks like stays with each of them, because that is about the answer and
this is about the packaging.

### 3. The lexicons move next to the ontology

`review.py`'s organization words, person shape, type words and noise
names, `ingredients.py`'s units, `references.py`'s venue words and
`furniture.py`'s sponsor vocabulary become YAML beside `ontology/`, keyed
by the type they are about:

```yaml
organization:
  cues: [universit, institut, laborator, gmbh, …]
tool:
  cues: [software, library, plugin, …]
```

`prax.ontology` loads them the way it loads types, and they carry the
module's version, so growing a lexicon is a version bump like growing a
type. `review.py` keeps the *logic* — which cue wins, what to do when
none does — and stops holding the knowledge.

This is also what makes the next step possible: a lexicon that is data
can be *measured* against the review queue's decisions, and a lexicon
that is code cannot.

*Cost*: two days. *Risk*: moderate, because the cues currently decide
types on 22,089 edges. Do it with the queue as the test set (below).

### 4. Calibrate instead of choosing ✅

Two places pick a number by hand: the resolution tiers' similarity
thresholds and `review.py`'s cue precedence. Both have ground truth
sitting unused — 2,181 open review items and everything already resolved,
which is a labelled set of exactly the decisions these rules make.

Measure first, change nothing: run the current rules over the resolved
items and report precision and recall per cue. That is an afternoon and
it answers a question nobody has asked — *are the cues any good?* Only
then decide whether a cue is dropped, a threshold moves, or the whole
thing becomes a small model trained on the queue (the Snorkel shape:
rules vote, the queue labels, something else decides).

*Done 2026-09-24*, and the first answer was that the question could not
be asked: no edge recorded *which* rule wrote it, and the pass's 3.3%
retirement rate is two passes withdrawn wholesale, not a judgement. A
rule signs its work now (`typing-rules/<rule>`).

What could be measured is the rules against the typing model where both
spoke about the same pair of names: the rules have no opinion on 93% of
those items, and where both speak they split 265 to 266. Per rule it is
not a coin flip at all — `funded_by` agrees 94%, `developed_by` 92%,
`affiliation` 86%, while `affiliated_with->written_at` differs on 54 of
56 and `cites->uses` on 27 of 30. Four rules decide 76 edges between them
and agree with the model on 2; they are candidates for deletion rather
than repair. The table and the caveats are
`docs/eval/typing-rules-2026-09-24.md`.

### 5. Normalization, properly

This is where the strata pay for themselves, and it is the part with a
design decision in it.

The vocabulary pass makes English the canonical language of an entity's
name: it renames `Olivenöl` to `olive oil` and keeps the German as a
label. SKOS and Wikidata do the opposite — the concept has *no* name, and
every language has a preferred label. prax is one step from that already:
`entity_labels` holds language, kind, producer and run, and
`entities_by_label` finds an entity by any of its names.

The stratified design:

- **`entities.name` becomes the display label in the default language**,
  not the identity. The identity is the row. Everything that looks an
  entity up by name (`link`, the importers, `resolution`) goes through
  the label table, which already has the index for it.
- **One `pref` per language, enforced**, as SKOS requires. A German
  search shows the German name, an English search the English one, and
  the graph is one node either way.
- **`kind='was'` goes.** It is an undo record wearing a label's clothes.
  The run already identifies what a pass did; the old name is an `alt`
  like any other, and `unmerge_run` reads the run, not the kind.
- **The candidate net stays the library.** Asking whether a name occurs
  in an English document beat a regex of German endings 102 to 24 on a
  sample of 400, needs no rule per language, and generalizes to the
  Spanish and French the library also holds. It is the one piece of this
  week's work that was built the right way round, and it belongs in the
  plan as the pattern to copy: *ask the corpus, not the author*.
- **Then the type clashes.** `Olivenöl` is an `ingredient` where `olive
  oil` is a `concept`; `compilerbau` and `Taylorismus` met the same wall.
  These are the polysemy backlog (about 3,700) arriving by a second road,
  and they want the calibration of step 4 before anything folds them.

*Cost*: the label-identity change is the largest single item here, three
or four days, and it touches `link`, which is the busiest function in the
store. *Risk*: high enough to want the chunk-fingerprint and
review-queue tests of steps 1 and 4 in place first. That is why it is
last.

## What this does not propose

- No new dependency. UIMA, GATE, Snorkel, Splink and GROBID are read
  here for their shape, not their code; every one of them is heavier than
  the Pi-class target allows (invariant 7).
- No rewrite of `references.py`. Its ceiling is known and measured
  elsewhere; replacing it is a separate decision with its own eval, and
  the rule version is the right choice until a reference pass is
  actually failing.
- No change to what the store holds, until step 5.
