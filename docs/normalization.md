# Normalizing the graph: what is one thing, and how prax decides

A design note, 2026-09-23, written after the multilingual study
(`docs/research-multilingual-2026-09-23.md`) asked the wider question:
what should prax do about one thing arriving under several names? The
measurements here are of the live store on that day.

> **What became of it, 2026-09-24.** The language row was built and run
> over the library (1,435 names decided; `docs/PLAN.md` and
> `prax.vocabulary`), and building it settled two of the guesses below
> the other way round. **The library-as-dictionary proposal was wrong in
> the form written here** and right in another: a miner of
> "Knollensellerie (celeriac)" glosses yields almost nothing, but asking
> whether a name occurs in an *English document at all* is a far better
> test than any rule per language. And the prevention rule was inverted:
> naming an entity in the document's own words costs a re-extraction of
> the library, while the model already writes English 95% of the time,
> so the prompt says English and the stragglers are a pass. Both are
> marked in place below. What is now open is `docs/stratification.md`
> step 5, the identity change.

The short answer is that normalization is not string munging. It is
identity resolution, it produces claims, and a claim in prax carries who
made it, when, and how sure they were. Invariant 8 says an edge is
evidence; a merge is evidence too, and must be as reversible.

## Five kinds of duplicate, five mechanisms

They look alike in a listing and have nothing else in common. Keeping
them apart is most of the design.

| kind | example | mechanism | state |
|---|---|---|---|
| surface | `Short-Time Fourier Transform` / `short-time fourier transform` | a deterministic key | done (`resolution.normalize`, tier 1a) |
| subtype | `Ann Author` as `person` and as `author` | the ontology's hierarchy | done 2026-09-23 (tier 1c) |
| morphological | `audio unit` / `audio units`, `taco` / `tacos` | a stemmer, per language | half done |
| language | `Olivenöl` / `olive oil` | the library as the dictionary, then a model | done 2026-09-24 (`prax.vocabulary`) |
| polysemy | the paper *Variational Mode Decomposition* and the method | **not a merge** | open, an ontology question |

### What the live graph held

125,048 live entities, and before today's pass:

- **375 groups** of one key and one type — surface duplicates the
resolve pass had not caught up with (it runs in the worker's default
steps, so this is a day's arrivals, not a backlog).
- **6,018 names typed two ways** with edges on both sides. The largest
groups were `author`/`person` (1,654), `method`/`tool` (657),
`document`/`paper` (586), `method`/`paper` (495), `paper`/`tool` (450).
- **48 singular/plural pairs** left after `normalize`'s own folding.
- Of 21 probed German-English pairs, **11 had both halves present** as
separate entities: `Olivenöl` (10 edges) beside `olive oil` (8),
`Knoblauch` (9) beside `garlic` (6), and so on.

After the subtype tier ran (439 sure merges and 3,219 folds), the type
clashes fell to 3,723 names and the aliases rose from 18,931 to 22,589.
What remains is the interesting part, and it is not a merge problem.

## The pipeline

Five stages, cheapest first, and every candidate that survives one is
handed to the next. This is the shape the field settled on (xMEN's
candidate generator and ranker; the same split as prax's references
pass).

**1. Key.** A deterministic function of a name, used for blocking and
never stored: NFKC, case folding, punctuation and spacing away, name
suffixes dropped, a trailing plural folded for the types where that is
safe. The surface form stays as it is — it is what the document said,
and the document is the evidence.

**2. Block.** Candidates without comparing everything with
everything. Equal keys. An author's initials form. The ontology's
subtype relation over one key. Nearest neighbours by name embedding,
which the worker's `resolve` step computes and `entity_candidates`
keeps. And a translation of the key, where a dictionary exists.

**3. Score.** Features, not a single number. String similarity, name
embedding cosine, type compatibility through the ontology, and the
evidence each side carries in edges and documents. Whether both names
appear in one document, which counts *against* a merge for a paper and
its subject and *for* one where a text writes "Knollensellerie
(celeriac)". And a dictionary hit, where there is a dictionary.

**4. Decide.** Three bands. High: merge, recorded as sure. Middle: the
adjudicator — a model with the evidence in front of it, as today, or a
person in the review view. Low: leave apart, and remember the decision
so the pair is not asked about again (`store.decide_candidates`).

**5. Record.** A merge is a pointer (`entities.canonical_id`), nothing
is deleted, and `traverse` follows it. What is missing today and should
follow the labels work below: which producer and which run made the
merge, so a bad pass can be retired the way a bad extraction is.

## What each mechanism needs

### Subtype folds (done)

The ontology already knew: `author` is a `person`, `paper` a `document`,
`venue` an `organization`, `device` and `component` are `tool`s,
`ingredient` is `material`. The extractor reaches for the general word
whenever a document does not make the specific one plain. So one name
under a type and its subtype is one thing, and the specific side
survives, because it says more.

Two guards. `page` and `project` are the store's own kinds, and a note I
wrote is never the extractor's guess, so they never take part. And the
fold is by key *and* subtype relation, never by name alone.

What it caught: 2,049 `person`→`author`, 739 `document`→`paper`, 255
`organization`→`venue`, and a tail of recipes, devices, components,
manufacturers, features, standards, articles and ingredients.

### Language (open)

The two halves of a pair are strangers to every mechanism above: no key,
no ontology relation and no string similarity connects `Olivenöl` to
`olive oil`. Three ways in, in the order prax should try them.

**The shared vector space.** Embed entity names with a multilingual
model and the pair becomes a neighbour like any other, handed to the
existing likely tier and its adjudicator. This is the literature's own
answer for exactly this task. Over Wikidata labels in ten languages,
sentence embeddings beat statistical alignment and string similarity by
up to 20 points of F1, even across scripts. In prax it is a consequence
of changing the embedder, not new machinery. It is step 2 of the
multilingual study and the single highest-value change here.

**The library as its own dictionary.** ~~A bilingual library defines
its own terms in passing: "Knollensellerie (celeriac)", "Faltung
(convolution)". `prax.acronyms` already mines "phrase (ACRONYM)" out of
every text; the same miner with a different shape yields translation
pairs with a document behind each one.~~

**Measured 2026-09-24, and it does not.** Over 300 German documents the
gloss miner found 2,348 distinct parentheticals and almost none were
translations: "Raum (Datenstruktur)", "Groß (Jane)", "Archéologie
(Istanbul)". The shape that makes acronyms work does not carry over.

The library *is* the dictionary, but for a different question. Not
"what is the English for this?" — "is this English at all?" A name that
occurs in the text of an English document is an English name; one that
occurs nowhere in the English half is a candidate. One FTS match, 7 ms a
name, no rule per language, and it generalizes for free: it caught
`psycho-acoustique` with no French anywhere in it. Measured against a
regex of German endings on 400 entities, the regex found 24 and the
library 102, and what only the library found was real — `compilerbau`,
`erwartungswert`, `wahrscheinlichkeitsraum`. It is wrong in the cheap
direction: a rare English term nobody else wrote down becomes a
candidate, and the model hands it back unchanged.

**A hand-made list, where the domain is closed.** The kitchen is 65
documents and perhaps 300 ingredients; a file of German-English pairs
would settle it in an afternoon and never need a model. Wikidata's
labels are the general fallback and belong in a `prax import` of their
own, not in the serving path.

### Polysemy (open, and not a merge)

`method`/`tool` (653 names), `method`/`paper` (495), `paper`/`tool`
(450), `concept`/`paper` (353): mostly a paper named after its subject.
The paper *Variational Mode Decomposition* is not the method of that
name, and merging them would be a lie that costs both. Two things to do
instead, neither of them resolution:

- relate them. The ontology has `proposes` for exactly this, and the
typing rules already retype a source that names the document itself. A
paper and a method of one name, from one document, is a `proposes` edge
waiting to be written.
- stop making some of them. `method`/`tool` is the extractor choosing
differently on different days; the prompt can say which word wins (the
v1 prompt did this for `concept`/`method` and the twins tier still
cleans up after it).

### The labels a canonical entity carries

The representation the field settled on is one entity with language-
tagged labels: a preferred label per language, alternatives for the
rest. prax had half of it, in 22,589 aliases. The half it lacked was
that an alias carried no language, no source and no confidence.

**Built 2026-09-24** (migration 20):

    entity_labels(entity_id, label, lang, kind[pref|alt], from_entity,
                  source_doc, producer, run, confidence, at)

A merge writes the folded name as a label of the survivor, with the
language `prax.language` reads off it and the producer and run that
decided the merge. Two things hold now that did not. A German name
reaches the English entity it belongs to (`entities_by_label`), which is
where the cross-language work will land its findings. And a round of
merging can be taken back whole (`unmerge_run`), the counterpart of
`retire_run` for edges; without it no new rule could be tried on the
live graph.

What a merge still cannot do is choose the canonical name per language.
The `pref` kind is there and nothing writes it yet, because that waits
for labels in languages worth choosing between, which waits for the
embedder.

## Prevention: what the extractor should be told

Half of this is made at extraction time. A German recipe whose
ingredient list reads *Sellerieknollen, Olivenöl, Salz* produced the
entities *celeriac, olive oil, salt*. The model translated, silently and
only sometimes.

~~The rule should be that an entity is named **in the document's own
words**.~~ **Inverted 2026-09-24, on the measurement.** Of 42,068 live
entities of a common type only 372 are named in German: the model
already writes English about 95% of the time, so asking for the
document's own words would mean re-extracting the library to *undo* what
it mostly gets right. The prompt asks for English instead — and says
which names that applies to, from the ontology's `naming:` key rather
than a list beside it, so a common noun translates and `Niklas Klügel`
never does. The document's own word is kept as a label in its own
language, so nothing is lost and a German search still reaches the
entity. The stragglers were a pass, not a re-extraction.

## Measuring it

No block should ship without a pair eval, the way the references pass
was calibrated against Crossref's links. The set is easy to build here:
a few hundred pairs judged by hand, drawn from what each block proposes
(the ingredient pairs are obvious, the author pairs nearly so), and kept
in `docs/eval/` like the others. Precision matters more than recall
here. An unmerged pair is a small loss; a wrong merge is two things that
can never be told apart again. So the thresholds are set where precision
holds, and the rest goes to the adjudicator.

## What not to do

- Do not normalize the stored name. The surface form is what a document
said; the key is derived, used, and thrown away.
- Do not merge across types without the ontology. A subtype relation is
a reason; a shared name is not.
- Do not send every pair to a model. It is the adjudicator of the
middle band, not the mechanism; the cheap blocks decide the rest, and
the ledger records what the middle band costs.
- Do not let a merge be silent. Every one needs a producer and a run,
or a bad pass cannot be undone, and this is the part still missing.

## Sources

- [Statistical and Neural Methods for Cross-lingual Entity Label Mapping in Knowledge Graphs](https://arxiv.org/abs/2206.08709)
- [xMEN: A Modular Toolkit for Cross-Lingual Medical Entity Normalization](https://arxiv.org/pdf/2310.11275)
- [Entity Linking with Wikidata: A Systematic Literature Review](https://dl.acm.org/doi/10.1145/3795134)
- `docs/research-multilingual-2026-09-23.md`, and `docs/rationale.md` R11 for why resolution is tiered at all
