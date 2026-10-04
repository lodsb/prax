# The five checklists

Each question names what to look for and, marked *prax:*, a case the
project has already met. A question with no hit is fine; the examples
show the kind of thing, they are not the list.

## 1. Hygiene

**Reuse**
- The same pattern, query or parse written a second time instead of
  imported. Search for the shape, not the name: `grep` the SQL fragment,
  the regular expression, the loop.
  *prax:* the cue matcher of the lexicon existed in `graph.review` before
  `ontology.cue_pattern`; the union-find over `part_of` was written in
  `store.repair` and again in `store.graph.rules` until `part_of_roots`.
- A constant defined twice (a cap, a list of types, a set of words).
- A helper that exists in the package the new code sits next to, unused.

**Modularity**
- A module past 1,500 lines is a split waiting; past 2,000 it breaks
  CLAUDE.md's rule (`store.repair` was split at 1,890).
- A module or function doing two jobs (finding and writing, reading and
  shaping an answer) where the second job has another caller in sight.
- A function past ~80 lines or ruff's complexity bound, when it is long
  because it does several things, not because it lists cases.
- In a store package of parts: a part importing from a later one, or
  private names (`_x`) reached across parts.

**Packaging** (CLAUDE.md, "The layout")
- A new module in a package whose one sentence it does not fit (`text`
  imports nothing of prax; `graph` is the ontology, extraction,
  resolution and its kin; `ml` the models and indexes…).
- An import placed inside a function to dodge the store's order where a
  module belongs elsewhere (`tests/test_invariants.py` catches some of
  it, not the reason).

**Structure**
- Words that say what a name is, written as patterns in code instead of
  `ontology/lexicon.yaml` (*prax:* venue, publisher and `part_of` cues
  went to the lexicon).
- A host's choice (a model, a path, a threshold a user may want to move)
  as a constant instead of a `prax.yaml` setting read through
  `prax.config`; an environment variable invented by a module.
- A schema change not as a numbered migration; a document-level fact as
  a column before it earned one (`documents.meta` first).
- A domain's knowledge in core code instead of its pack.

## 2. Extensibility against the plan

Read the open items of `docs/PLAN.md` first ("The order", the stages,
"The graph", "Retrieval and ask"). Then, for each abstraction the change
adds:

- Which open item builds on it next, and does it take that item without
  a rewrite? *prax:* the conflicts pass is written over every functional
  relation, not `published_in` alone; the path index is in memory, and
  CLAUDE.md names when it moves to a file.
- A hard-coded single case where the plan already names the second one
  (one relation, one language, one model, one domain).
- A data shape that a planned feature must migrate (a column that should
  have been a table, a JSON key that will need a second meaning).
- A step or pass that cannot be run on request, by a worker, or on the
  door's clock, when the plan says it will be.

## 3. Independence from the owner's library

The library this code first ran on is one person's: German and English,
music technology and DSP, one university, a few years of coursework,
private papers. Code that only works there is a defect for anyone else.

- **Tuned numbers.** A threshold, weight or cap set from a measurement on
  this library: is the measurement repeatable by someone else (an
  `eval_*` script, a test that states the expectation), and is the
  number a setting where a different library would need another value?
  *prax:* `paths.SOUND` and `HUB` are re-measured by
  `scripts/eval_paths.py`; the venue rules were tuned on this library's
  2,100 venues and have no eval yet.
- **Baked-in names.** Institutions, people, course names, publishers,
  brands, languages in code or tests (*prax:* the lexicon holds German
  and English cues — data, extendable, but a French library needs
  French words there; say where).
- **Language assumptions.** A parser or rule that only reads German or
  English (ordinals, semesters, "Proceedings of"), or assumes Latin
  script, without falling back to doing nothing.
- **Domain assumptions.** Logic that only means something for research
  papers (venues, citations) running for every document, or a domain's
  words outside its pack.
- **Private data.** A name, title, path or figure from a personal
  document in code, tests, docs or a commit message (`check_private.py`
  guards the repository; a review reads what it cannot see, such as a
  log entry quoting a personal document's facts).
- **Failure mode.** On a library unlike this one, does the code do
  nothing, do something wrong quietly, or say so? Quiet and wrong is the
  defect.

## 4. Conceptual generality

- A rule written for one kind of thing that is an instance of a general
  one (*prax:* "a venue series and its editions" is "a name with an
  edition": software versions, standards, magazine issues; worth
  generalising only when the second case is real).
- Two mechanisms for one concept (two ways to say "held at a moment",
  two caches of the same thing, two notions of "the same entity").
- A general mechanism with one user and no second in the plan: the cost
  is reading it, say so.
- A concept named in code differently from CLAUDE.md, the ontology or
  the docs (a reader cannot find it by the word the docs use).

## 5. Performance on the target

Invariant 7: under 1 GB resident in the serving path, Pi- or N100-class,
model work as batch jobs. The library grows; on 2026-10-04 it held
12,900 documents, 1.47 million chunks, 215,000 live edges and 161,000
entities.

- **Per request**: work done on every call that could be cached or done
  once (a lexicon parsed, a regular expression compiled, a graph built,
  a whole table read). *prax:* the path index is cached per database;
  the first call builds it (1.2 s).
- **Growth**: a loop over all documents, edges or chunks in a request; a
  nightly pass that reads everything when it could read what changed (a
  stamp, a hash); a quadratic step over a list that grows with the
  library (pairs of names, pairs of edges).
- **SQL**: a query on a column no index serves (`EXPLAIN QUERY PLAN`
  says `SCAN`), `json_extract` in a `WHERE` over a big table, an `OR` of
  two indexed columns, `NOT IN` a large list built in Python. *prax:*
  migration 42 added the indexes `changes` needed: a day from 1.2 s to
  0.37 s.
- **Memory**: a peak while building something (the path index's 194 MB
  while building, 31 MB held), whole files read into memory, lists of
  dicts where tuples or arrays would do, caches without a bound.
- **The lock**: a write held behind `_serialized` for long, or a pass
  that commits rarely and blocks the door's writers.

Measure a performance claim before reporting it: a timing on the copy of
the library, a query plan, a memory figure. A guess about speed is not a
finding.
