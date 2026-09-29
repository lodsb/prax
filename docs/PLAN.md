# prax build plan

What is next. The record of what was done, with the reasoning and the
measurements under each night, is `docs/log.md`.

Work one stage per Claude Code session. Each stage ends green: tests
pass, `ruff` clean, and the stage's checklist fully ticked before moving
on. Decisions: `docs/rationale.md`. Source details: `docs/sources.md`.

## The order, agreed 2026-09-27, second half (from niggles.txt)

F–J below are done, and two niggles with them (taco/tacos is I, the
maintenance workflow and its banner are J). What is left of niggles.txt,
with the graph's correctness and the two held-back features, in this
order: a bug a user sees first, then the graph views, then what the graph
reads against, then the machine, then new ways in, then the features;
the review of the lists a person decides goes last.

- [x] (2026-09-27: a module holds the modules built on it; `unassigned`;
      the select filters on change) **K. Browse loses documents when only the module changes**, and
      "unassigned" shows only a label (niggles.txt). A bug in the view or
      in `GET /documents`' domain filter; reproduce, test, fix.
- [x] (2026-09-27: `/graph/overview?domain=`, `/graph/document/{id}`,
      a module select and a `graph` link on the document page)
      **L. The graph by module, and a document's own graph.** The graph
      view filtered to one domain's entities, and a link from a document
      page into the graph seeded with that document's entities.
- [x] (2026-09-28: counted: every extracted document is read against its modules' current versions, 8,878 of them `core3+research9`; the 1,114 without one wait for their first extraction) **M. What the graph reads against.** (2026-09-27: v9 done,
      `docs/ontology-v9.md`; electronics 1 and studio 5 done,
      `docs/ontology-electronics.md`; the full re-read after stage H's
      re-extractions, as the user decided.) Ontology v9 (the relations a
      document takes name `paper` where any `document` should do), then
      the decision on the documents read against an older version (a
      full re-run or a delta pass; the 1,021 of 2026-09-10 are 6,412
      stamped `core1+research5` now, none of them captures, so the
      standing worker never draws them: about 28 hours of the local
      model at 230 an hour), then an `electronics` module for
      datasheets (pins, packages, electrical characteristics), grown from
      what the review queue holds for them, as studio was. A module for
      code waits for documents that need it.
- [x] (2026-09-27: `idle_minutes` and `trim` in `prax up`, deferrals as
      demand, the holders in the Jobs view; howto 4b)
      **N. The card and the memory when nothing runs.** Unload the GPU
      models after an idle while and load them when a queue asks (`prax
      up`'s swap exists; the plan's "let a queue ask for the card"), see
      who holds the card (the plan's "See who holds the card"), and the
      16.6 GB of mapped model pages a fresh load left in RAM on
      2026-09-27.
- [x] (2026-09-27: `clients/send/prax_send.py`, `POST /known`,
      `meta.origin`; `docs/media-by-reference.md`)
      **O. A portable sender for other machines.** One file, no
      dependencies beyond what an old system has, that walks a tree, asks
      the door by hash whether it holds each file, and sends what it does
      not (PDFs and text first). Audio and video by reference rather than
      by copy is its own design question, written down, not built.
- [x] (2026-09-27: `docs/communities.md`; search and ask as ways in
      are still open) **P. Communities** (below, "A partition, and a way to keep it"):
      the hub decision it waited on is H.
- [x] **Q. A confidence that was measured** (below; `confidence` as a number on edges is still open there). (2026-09-27: the
      tooling built, a pilot run, the full run queued behind the re-read;
      the finding so far is that the labels need a gold sample.
      2026-09-28: the adjudicator answers by pair number; with one rule
      for sameness the local model settles 60% of the pairs at 97%
      agreement with Opus; 77 wrong merges split. Open: the gold sample,
      the user's Review round under the rule, then the thresholds.)
- [x] (2026-09-28: `ontology/sameness.yaml`, `ontology.sameness()`, `GET /graph/sameness`, the Review page shows it and each pair's number) **Q2. The rule for "the same thing" as data**, after the Review
      round. `resolution.SAME_RULE` (the models' question) and the Review
      guide's rules of thumb say the same in two copies. Move it to
      `ontology/sameness.yaml` beside `lexicon.yaml`: the cases that are
      the same and those that are different, each with examples, and a
      module's own examples in its words (kitchen: a vegan variant is
      another dish; studio: a synth and its manual). `prax.graph.ontology`
      loads it, the question is built from it with the examples of the
      pair's modules, the Review page shows it from the door, and
      `docs/review.md` points to it. Like the lexicon it stays out of the
      version string: it changes how a pair is judged, not what exists.
      Not `prax.yaml`: every host serving a library judges alike.
- [x] (2026-09-27: the three tabs of the Review page, `decide_pair`,
      `unmerge_entity`, `undecide_pair`; howto "Entity resolution")
      **R. The lists a person decides, last:** a review page for the 741
      likely merges and the 1,632 split names, with the merge and the
      unmerge the door already has. Each decision is kept as a label, so
      the page also collects Q's gold sample, and it shows the merges Q
      found suspect.
- [x] (2026-09-27: not reproduced natively; two unlocked index paths
      and a third that saved over a mapped file found and closed,
      `scripts/stress_vectors.py`) **S. The door's `STATUS_BAD_STACK`
      exit** (below): a reproduction before any fix, so open-ended.
- [x] (2026-09-27: `prax export`, `prax import graph`,
      `docs/graph-files.md`; the plugin's session-end sync still open)
      **T. Sub-graph export / import**, as designed below: a feature,
      not a repair.
- [x] (2026-09-28: `prax token`, migration 0029, `tests/test_wall.py`)
      **U. Tokens and the wall** (niggles.txt, "closed domains";
      2026-09-27). The library keeps everything, and a client sees only
      what its token may. `PRAX_TOKEN` stays the administrator. Named
      tokens (`prax token add NAME [--domains …] [--personal]`) are kept
      as a hash in a `tokens` table; the secret is printed once. A token
      says which modules it sees (every one by default) and whether it sees
      personal documents (no by default). A document carries a
      `sensitivity` column: `suspected`, `personal`, or none. The filter
      sits in the store, keyed by the request's viewer, so `ask`, the
      surfer and every read inherit it: a hidden document is absent from
      search, get, chunks, lists, pages, context and the graph (an edge from
      it is not there, nor an entity only it names). A restricted token
      may call only the routes the MCP tools use; every other route answers
      403, so no unfiltered route can be reached. A test walks every
      allowed route with a hidden document.
- [x] (2026-09-28: `prax.wall.private`, `store.suspect`, the `private` pass, the "personal?" tab; 238 of 12,974 suspected in the dry run) **V. What is personal** (niggles.txt, "approximate domains"). Rules
      in `prax.yaml` (`private:`, outside the repository: the owner's name
      and other hints live there only): strong cues that mark a document
      `suspected` alone (an IBAN, a bank statement, a rental contract, a
      tax assessment), weak ones that count only together (a name with an
      invoice word), paths. The rules run when a text is indexed, so a NAS
      send closes personal files as they land, and as a `maintain` pass
      over the library. They only ever suspect: a person confirms or
      releases on the Review page's "personal?" tab, and a person's
      decision is never overturned by a rule. The Zotero library holds
      bank statements and contracts filed as research (2026-09-27: 15
      "Kontoauszug", 8 "Mietvertrag", 71 with an IBAN), which is why the
      mark is apart from the domain.
- [x] (2026-09-28: `prax.text.clutter`, `store.retire_set` / `restore_set`, the Review page's "clean up" tab; measured: 238 system and clutter documents, 55 copies, 896 help files kept) **X. Cleaning up in bulk**, after V, from the NAS dump of
      2026-09-28. A set of documents chosen by rule, shown before anything
      happens, retired in one go and restored in one go. The rules to try
      first, counted read-only on what arrived before any is built: the
      origin path (`meta.origin`, what the sender records), no text or a
      few lines of it, the usual clutter (licences, readmes, driver and
      installer notes, generated reports), near-duplicates the checksum
      does not catch (one title, nearly one text). The personal documents
      of V are confirmed in the same view. Retiring stays what it is: the
      original stays in the archive.
- [x] (2026-09-28: the Admin page, tabs tokens / personal? / clean up; `GET /private/rules`; a token's last use kept hourly; the rules shown, not edited: prax.yaml stays the one place) **W. The administrative side**, after U and V: the tokens, the
      rules and the personal documents managed from the web interface.
- [x] (2026-09-28: `parsers.Partial`, both OCR readers windowed, `queue.continue_windows`, `store.note_ocr_progress`) **Y. OCR in windows**, after W (the user's order, 2026-09-28). A
      scan past `parse.ocr_max_pages` (60) is refused today and read only
      by a one-off run with a higher budget: 71 PDFs were, and four DjVu
      books (190 to 746 pages) still have no text. Instead each pass reads
      what has text (the text layer, and the pages OCR'd before, which the
      page marks show) and OCRs the next window of pages without it. The
      door stores and indexes the text so far, so a book is searchable as
      it goes, and queues the next window while pages remain. The
      document says how far it is (`meta`, "OCR: 180 of 746 pages"). Both
      PDF and DjVu, which share the OCR engine. The parse protocol grows a
      "pages still to go" in a result. Then the 1,663 PDFs with under
      2,000 characters of text are worth a look for scans nobody asked to
      read.
- [ ] **Z. What a document is, and what it is about** (below, planned
      2026-09-29): a genre for every document, several where it is
      several, with a confidence measured against the user's own labels;
      the topics it belongs to from the graph; and the domains of the
      2,748 documents without one derived from both by rules. The
      research is in the library (docs 13315–13337).

## What a document is, and what it is about (planned, 2026-09-29)

**The problem.** A domain is the set of ontology modules a document is
read against, and the one rule that sets it is `source: zotero →
research`. The 2,748 documents without a domain are mostly the NAS dump of 2026-09-28. Among them are datasheets and
electronics books that belong in `electronics`, and political,
sociological and opinion pieces that fit no module. What the extraction already says about them is thin. It types the document itself as one of a module's `self_types`. For
1,377 of them that is the fallback `document`, for 590 there is none,
and the rest are `paper` (248), `build` (219), `article` (142) and
`manual` (102), counted read-only on 2026-09-28. The self type answers "what may
this document be in the ontology", which is not the question.

**Two axes, both multi-label.** Genre and topic are separate questions.
Pang et al. (doc 2194) treat genre and subjectivity apart from topic.
Doc 1459 finds that experts, crowds and machines agree on some genres
and not others, and concludes that several facets beat one taxonomy. A document may be several
of each: a blog post that is a how-to and an opinion, a book on
electronics and on music. In the formal sense of doc 879, a document
gets a set of labels Y ⊆ L, each with its own probability.

**The genre vocabulary** is `ontology/genres.yaml`, beside the lexicon and the sameness rule. Like them it stays out of the
version string, because it says what a document is, not what exists in
the graph. Two levels,
after the web-register taxonomy of CORE (docs 13320, 13321: eight main
registers, sub-registers under them, a document that fits no sub-register
keeps the main one). The main levels, with their genres:

- informational: paper, thesis, book, lecture notes, reference entry,
  news report;
- instructional: manual, datasheet, schematic, recipe, tutorial;
- opinion: essay, column, review ("blog" was dropped in version 3: a
  venue, not a form);
- persuasion: advertisement, product page;
- narrative: story, biography, report of events;
- interactive: forum thread, Q&A, comments;
- personal and administrative: letter, invoice, contract, form;
- code.

Each genre carries one line of description: a label
described in context classifies better than a bare name (Gen-Z, doc
13336).

**Gold before model.** A "genre" tab on the Review page shows a
document (title, summary, source, the opening of its text) and takes
the user's genres. Before any model runs, the user labels about 150,
drawn across sources (NAS, Zotero, captures, the extension). Genre is
ambiguous even for people: CORE needed two of four annotators to agree,
and hybrid texts are where classifiers fail most (doc 13320). So the
gold sample is the measure, and the confidence rests on it, as for the
local adjudicator (stage Q).

**The genres step** is a worker step on the local model, like titles and
summaries. It reads the title, the summary, the section summaries when
there are any, the source and host, and the first 2,000 characters.
TnT-LLM (doc 13322) summarises before labelling because a summary
normalises length and variety, and prax has the summaries already. But
genre is a matter of form as much as content, which a summary may
lose, hence the opening. Summary-only against summary-plus-opening is
the first measurement. Three ways of getting several labels, measured
on the gold sample, the best kept:

1. a list the grammar allows, first genre first;
2. the same list with the token probabilities of every generation
   step, each genre scored by its highest probability over the steps.
   An LLM suppresses all but one label at each step, and the first
   step's distribution does not predict the final set. The maximum over
   steps improves F1 and the fit to people's answers at no extra cost
   (doc 13331);
3. a yes or no per genre, over the five nearest by vector.

**A side-quest: the decision models.** Jev (TypeSafe, a closed API)
answers several questions about one text as probabilities read from the
model rather than written by it (doc 10307 reverse-engineers it). Two
open models take the same request format (`state`, `questions`, each a
`choice`, a `noul` yes/no or a `score`). Jeff (github.com/firelex/jeff)
is Qwen3.5 0.8B and 2B and a Gemma, fine-tuned in full, one fitted
temperature. Kev (github.com/jaredpalmer/kev) is a rank-16 LoRA and a
pointer head on Qwen3.5 0.8B, 4B and 9B, or Qwen3.8 27B, one temperature
per checkpoint. A yes/no per genre over one shared reading of the
document is method 3 without a generation per genre, which is why they
are worth measuring. Measured as a fourth candidate on the same gold sample
and inputs, Jeff-2B and Kev-4B first. Jeff-2B's 4.2 GB fits on the card
beside llama-server's 20 GB; Kev-4B takes the card from it (`prax up
--swap`). Open questions the measurement answers: how they read
500–700 tokens (their benchmarks used about 200), and how far their own
temperature is from a fit on the user's labels. Both need PyTorch and
transformers, an optional extra for the worker, never the serving path.
Jev itself is not a candidate: it would send the library's text to a
company's API.

The order of the genres in the prompt is shuffled per document: the
recency and majority biases of a prompt shift a model's labels (doc
13327). For scale: ChatGPT reached micro F1 0.74 on English genre
identification, 5–7 points above a classifier fine-tuned on 1,700
labelled texts (doc 13319). Expect the local model near that, not above.

**The confidence** is a Platt fit per genre on the gold sample, as `resolution.LocalAdjudicator` does for pairs (doc 13328 on
calibration). Under a threshold a genre is kept but acts on nothing.
Stored as `meta.genres: [{genre, p}]`, with the run and the model, so
`retire_run` has something to retire. A person's genres on a document
are `by: human` and never overwritten. Confident genre words join the
document field (`document_field`, beside "PDF document"), so a search for
"datasheet for the TL072" finds the datasheet first.

**Subjects** are what a document is about in a person's words
(`ontology/subjects.yaml`: society, arts, technology, everyday, and the
subjects in each, philosophy, politics and sociology among them). The
user asked for them on 2026-09-29, when the genres had no place for
political or philosophical writing: that is a topic, not a form. They
are labelled with the genres, in the same row, and the model assigns
them the same way. A domain rule can name one (`subject: politics`).

**Topics** come from the graph, which already has them: the regions and
parts of stage P. `meta.regions: [{id, share}]` is written by the
communities pass for every document with facts, counted the way
`regions_of` counts a search's hits, and keeps each region over a
quarter. A document with no facts yet takes the regions of its nearest
documents by document vector, a Rocchio-style vote (doc 5463): a
region's profile is its documents. Region ids change when the partition
is recomputed, so nothing stores a region id as a decision.

**Domains** are derived by rules, in the one place they already live
(`domains:` in prax.yaml, `store.assign_domains`), with two new match
keys:

- `genre: datasheet` (p over the threshold): `→ [electronics]`;
- `facts: kitchen`: at least half of the document's typed facts are of
  one module's types. A document without a domain is extracted against
  every module, so its own facts say which module it reads as, and
  this outlives a recomputed partition.

Every run is a dry run first, counted per rule, and applied on the
user's word. A set a person chose (`domains_by: human`) is never
touched, as now. A document may match several rules and take their
union, since domains are a set.

**What it does not do.** No model decides a domain directly. A domain
changes what a document is read against, so a wrong one costs an
extraction. A genre or a fact share with a measured confidence is a
reason a rule can name. A `society` module (politics, economy,
opinion) is decided after this runs. The count to take: the documents whose
genre is opinion or news and whose facts are mostly core types. The
review queue's items for them say what types they ask for. The region "Civilization Collapse and
Systemic Risk" (232 documents) is where they gather now.

**Later, if the board needs it.** The board runs no model (invariant 7).
A capture landing there waits for the worker, as a title does. If that
is too slow, a logistic regression per genre over the document vectors,
trained on the model's labels, can serve on the board. Classifiers
trained on LLM labels perform comparably to those trained on people's
(doc 13329), and choosing which documents the teacher labels cuts its
cost further (doc 13330). SetFit (doc 13325) is the heavier option.
Built only when measured to be needed.

**Stages, in order:**

1. `ontology/genres.yaml`, the Review page's genre tab, and the user's
   gold sample (about 150). (2026-09-29: the file, the tab, `GET
   /genres`, `GET /documents/genre-sample`, `PUT /doc/{id}/genres`
   built; `ontology/subjects.yaml` beside it, labelled in the same row;
   the labelling is the user's.)
2. The genres step, the three methods and the two inputs measured on the
   gold sample (`scripts/eval_genres.py`, `docs/eval/genres-*.md`), and
   Jeff and Kev beside them (the side-quest above).
3. The calibration, `meta.genres`, the genres on the properties dialog
   and as a Browse filter, the document field.
4. `meta.regions` from the communities pass, and the vector vote for
   documents without facts.
5. The `genre` and `facts` match keys, a dry run over the 2,748
   documents without a domain, then the user's word.
6. The `society` decision, measured.

## The order, agreed 2026-09-27

A–E below are done. This is what follows, in this order: first what
waits only on a word, then the other half of the traverse question, then
the precondition for communities, then two things a user notices.

- [x] (2026-09-27: 2 twins retired, the promotions were already done, the
      figures a nightly slice of 150 from the door's clock) **F. What waited on a word.** The six `twin-documents` (dry run,
      then the repair), the seven pending promotions (`--spend`, capped
      at the seven, roughly $1-3), and the unread figures started in
      slices that leave the parse queue its turn.
- [x] (2026-09-27: 394 safe merges, the round on the clock, `split-names` and
      `POST /graph/merge` for the rest, merges stamped with their run) **G. One thing in pieces** (`docs/eval/fractured-names-2026-09-27.md`).
      A `heal` ailment that lists them, merges for the safe classes (the
      same type twice, a type and its subtype) under a run that
      `unmerge_run` takes back, and a rule for which unrelated type pairs
      may name one thing, measured before anything merges.
- [x] (2026-09-27, see the log) **H. The hub entities.** 164 of the 200 biggest hubs are papers,
      and the biggest is a proceedings volume at degree 1,951: decide what
      a container is and keep it out of the neighbourhoods.
      *Measured 2026-09-27: not a container.* The volume's entity is 118
      NIME papers whose title (from each PDF's metadata, and in Zotero) is
      the volume's name, merged by that name into one. 1,820 live documents
      share a title with another: course names on exercise sheets, a LaTeX
      template's sample title on 89. Most other paper hubs are long
      documents, whose degree follows their length and which neither the
      second hop nor the hub map lands on. So the fix is the titles:
      - [x] `titles.guess_title(not_title=…)`: the shared title is named to
            the model as not this one, and a guess of it is refused.
      - [x] a `shared` reason in `titles_needed` (a title three or more
            live documents carry), for the watched titles step;
      - [x] after a retitle away from a shared title, the document
            extracted again, so its facts leave the shared entity (a new
            reading retires the old);
      - [x] applied 2026-09-27 after the probe on the local model: 642
            documents retitled by the first drain, the re-extractions
            running on the standing worker.
- [x] (2026-09-27: a rare word's forms the library uses, `compounds.forms`)
      **I. taco and tacos** are different searches: a plural folded on
      the keyword side, measured on the eval set.
- [x] (2026-09-27: `GET /maintenance`, the Passes table, the banner)
      **J. One workflow for maintenance and healing**, where there are
      five commands with five shapes (`niggles.txt`).

## The order, agreed 2026-09-26

Everything below is written up somewhere in this file or in
`docs/audit/engineering-2026-09-25.md`. What was missing was an order,
and the order is load-bearing rather than a preference — each stage is
either a thing that might be broken now, a thing that makes the next
stage safe, or a thing a user would notice.

### A. Settle whether the surfer got worse — done 2026-09-26, cleared

- [x] **What the surf sees when it walks changed under it.** `prax.answering.surf`
      took `store.traverse(...)[:WALK_EDGES]`, the first N edges by id,
      and since 2026-09-25 passes `limit=WALK_EDGES` into traverse, which
      spends the cap round-robin across relations. Same count, different
      edges. The ask set's eight-step verdict fell to 42 against a
      baseline range of 43–48 on the first run that exercised it
      (`docs/eval/ask-equations-2026-09-26.md`), while every mechanical
      number rose.

      Three repeats put it at 43.7 with a range of 42-46 against the
      baseline's 45.0 (43-48), so the 42 was the bottom of a spread:
      `cited` matches the baseline to the decimal, `match` is marginally
      above, and `sources` went 90% to 100%. No regression demonstrated
      and no second arm run — `docs/eval/ask-surf-walk-2026-09-26.md`,
      which also records that 42 alone means nothing, for the next time
      the surf changes.

### B. Make the invariants able to catch things — second, because it is
### cheap and it protects everything after it

- [x] (done 2026-09-26: CLAUDE.md marks each invariant, tests/test_invariants.py,
      the `attachment` pass) The four kinds, the tests, and the measured pass: "Name what kind
      of invariant each one is", below. **Invariant 6 was breached for
      months and no test could have failed**, which is the argument for
      doing this before the refactors rather than after them: a
      structural change is exactly when a silent breach gets introduced,
      and three of the checks currently live in a scratch audit script
      instead of the suite.

### C. What a user actually notices — the two parts done 2026-09-26,
### and a design gap found under them

- [x] **German compounds** — `prax.text.compounds`, the library as its word
      list: a compound splits where both halves are terms the index holds,
      and each half goes through the graph's labels (`Olivenoel` →
      `oliven` → `olive oil`). No harm and a small gain on the 19 German
      questions (hybrid MRR 4.33 → 4.83). ("What one question in German
      found wrong", below.) 1,988 German documents, and for a compound query the
      keyword half of the hybrid contributes *nothing*. A word list, not
      a model.
- [x] **An ingredient list without a heading** — measured over the
      library with the real chunker: 20 of the 32 kitchen documents that
      had none gained one, two outside the kitchen did (a cocktail
      handbook under research, and one false alarm), none lost one.
      Applied to the kitchen domain on the live store: **36 → 56 of 68
      (53% → 82%)**, the apple recipe among them. The other two change at
      the next full rechunk.
- [x] **The list is a box, not yet edges** — it was not needed: once
      the chunk exists the model reads it, and re-extracting the 74
      kitchen documents gave 1,029 `calls_for` edges, `apple` among the
      apple recipe's sixteen (plus 50 `made_of`, which the ontology
      allows for a dish).
- [x] **The prevention dropped what the repair kept** — names are
      written as printed and translated once, by the watched
      `vocabulary` step, which keeps the printed word as a label
      (663d2df). Two faults in that step came up on the day's 94
      renames and are fixed: the document title was taken for the
      answer (`Apfel-auflauf` → "elderflower lemon bake"), and a name was
      ruled English if any English document contained it, which let out
      `Mehl`, `Zucker`, `Salz`. Now a rate
      (`docs/eval/apfelkuchen-2026-09-26.md`, "After the re-extraction").
- [x] (done 2026-09-26: `prax maintain` pass `rejudge`, 979 overturned and
      folded; a second run overturned none) **Re-judge what the old rule ruled out.** 979 of the 4,103
      entities marked `vocabulary:corpus` would be candidates under the
      rate: 59 ingredients, ~900 concepts and methods the German course
      material uses more than the English half does. Clearing the mark
      puts them back in the queue, at one local call each (the model
      hands an English word back unchanged). A data change: waits for
      the word.
- [x] **And count what uses a mechanism** — the `attachment` pass of
      `prax maintain`. The apple question shows the lesson once more:
      no German document in this library names an apple, so the route
      from `Apfelkuchen` to `apple` is built and has nothing to cross
      on. The route is only as good as the words the library holds.
- [x] **The word the other way** — the vocabulary step now also writes
      a German label for each common English name of up to three words
      the library's documents use, so a German query crosses where no
      German document printed the word. Kitchen subset dry run: 203
      names at 0.14 s each, 162 labelled, 37 the same word, 4 refused,
      about 6 wrong (blackberry jam → Himbeermarmelade, cumin → Kümmel).
      Library-wide it offers 22,389, mostly research concepts: about 50
      minutes of the local model once the door runs it.
- [x] **Layer 1 of the brand collision: a word the graph added is
      searched where its sense lives.** "Apfelkuchen" without a domain:
      the apple recipe second, after a cocktail with apple juice, and no
      Logic manual on the first page. 43 of the 44 eval questions keep
      their top ten; the German set is unchanged (9 found, 4.58).
- [x] **Layer 2: a question for one of the small domains** gets
      a soft extra vote for that domain's hits. The graph could not be
      the trigger: nothing called "apple" is the brand, and a domain of
      30 documents gives any German word a lift of 20–90 by accident.
      The trigger is the query's own candidates. "apple cake": the recipe
      goes from 4th to 2nd. "Apple Loops", "apple" and every eval question
      are unchanged.
- [x] **The brand collision** — done by the two layers above. Left: the
      manuals are still 3rd and 4th for "apple cake", which a preference
      rather than a filter accepts. Was: what stands between an English
      "apple cake" and the Logic Pro manuals. Still design work.

Brand polysemy (`apple` → Logic Pro manuals) is deliberately not here.
It is design work with no obvious shape yet, and it is the one apple
failure a user can work around by adding a word.

### D. The two quadratics — fourth, because they cost hours whenever a
### pass runs over the library and nothing else waits on them

- [x] (done 2026-09-26) The embed hand-out's cursor and the delta's save frequency ("The
      embed pass redoes its finished work twice a batch", below). Both
      small, both measured, and the re-embed that found them is done, so
      there is no rush and no reason to leave them.

### E. The structural work — last, and one stage each

- [x] **The Step object**, tests first (2026-09-27): `prax.steps` is a package, each
      step one object with `hand_out`, `take_in` and `run`; `work.py` 1,144 → 360 lines,
      `worker.py` 1,159 → 918. The worker passes of summaries, sections and
      vocabulary were pinned first, and a test keeps the dispatchers free of
      step names. Two bugs fell out: a local-model step with a paid model
      leased its batch before refusing it, and a deferred vocabulary result
      raised in the door.
- [x] (2026-09-27: `documents` and `graph`, the two past 2,000 lines, are packages of six
      parts each with an `ORDER` the invariant test reads; the largest file is now 681
      lines. `retrieval` (1,837) and `repair` (1,460) stay whole until they pass the
      same line) **The store's module split**, after the Step object, since both
      touch the largest modules and the module order is an invariant that
      B will have made testable.
- [x] (2026-09-27: 41 → 0; 30 store reads added, and a test fails on SQL
      naming a prax table outside the store; the Zotero importer's 9 read
      its source) **The reads outside the store**, which the split partly dissolves.
- [x] (2026-09-27: `models.trim_guessed`, `models.trim_measured`,
      `sections.budget`) **The three names** (`fits`, `fits_for`, `read_for`), which ride
      along with whatever touches them.

The two things already written and not yet earned — communities as nodes,
and a confidence that was measured — stay below all of this. Both are
features rather than repairs, and both read better once the surf question
is closed.

## Still open

Items the record proposed and nothing has closed, gathered from where
they had scattered. Each links to the section of `docs/log.md` that
explains it.

### Ready, and waiting only on a decision

- [x] (done 2026-09-26, 310 s, after stage D) **`prax maintain --rechunk`** — the `ad`, `comment` and
      `ingredients` regions only arrive with a re-chunk (10,261
      documents; a chunk whose text did not change keeps its id and its
      vector). An overnight job.
- [x] (F: `schedule: figures`, 150 documents a night) **The unread figures** — 55,607 left of 93,158, sliced so a
      request does not pre-empt the parse queue for days.
- [x] (F: none waiting on 2026-09-27) **The seven pending promotions** — `prax work --steps promote
      --spend -n 7`, roughly $1–3 on Sonnet 5; or the standing shape,
      `spend: true` on the worker role with `budget: {daily_usd: N}`.
- [x] (F: 2 left, retired into their keepers) **`twin-documents`** (6) — the repair retires documents.
- [ ] **The sections pass on the vector side.**
      `docs/eval/sections-2026-09-25.md` measured the keyword half —
      hit@10 0.087 to 0.130, MRR 0.048 to 0.077 — and says nothing about
      `document_embeddings`, where a query that paraphrases a chapter
      rather than quoting it should do better. The same A/B, the vector
      arm. (The backlog itself is done: 2,050 documents.)
- [ ] **What sections are worth to `ask`**, which was the second reason
      for the pass: a cheaper way into a long book than its chunks.
      `scripts/eval_ask.py` is the instrument.

### What one question in German found wrong

`docs/eval/apfelkuchen-2026-09-26.md`: "hast du ein rezept fuer
apfelkuchen?" answered no, and the same question in English found the
recipe at once. Three independent failures, each wanting a different fix.

- [x] (stage C) **German compounds are one FTS token.** `Apfelkuchen` matches
      nothing; `Apfel` and `Kuchen` each match. For a compound query the
      keyword half of the hybrid contributes nothing at all, which is why
      the German answer held a chickpea pan and an insertion sort. German
      is 1,988 documents here and compounding is how the language builds
      nouns. FTS5 takes a custom tokenizer and a splitter needs a word
      list rather than a model, so this is the cheapest of the three.
- [x] (stage C, layers 1 and 2) **A common noun loses to a brand that owns the word.** `apple`
      returns Logic Pro and Logic Express manuals — correctly, by BM25,
      since they say it far more often. Even in English the fruit is
      unreachable until "bars" disambiguates. prax handles one name
      meaning two things in the *graph* (the `proposes` pass, the review
      queue) and not at all in retrieval.
- [x] (stage C) **An ingredient list without a heading is not recognised**, so the
      cross-lingual bridge has nothing standing on it. `prax.text.ingredients`
      cuts from a "Zutaten"/"Ingredients" heading; the Guardian writes its
      quantities into the prose. The library has **38 `ingredients` chunks
      in ten thousand documents**, and the recipe in question has none —
      hence no `apple` ingredient entity, hence no German label to cross
      from. The graph's only apples are Apple Macintosh, apple loops,
      Apple Computer Inc., and `Holsapple`, `Scrapple` and `Applets`.

      This is the one to take seriously, because nothing is broken. The
      identity work of 2026-09-24 built the route and measured it
      carrying 32 of 80 German queries to new documents. Almost nothing
      is attached to it. **A mechanism that works and is unattached looks
      exactly like one that does not work**, and the way to tell them
      apart is to count what uses it — which no pass does.

### The graph

- [x] **A name is not an identity** (2026-09-27): traverse walks one of the
      things a name reaches and names the others (`senses`, `type`), in the
      door, the MCP tool, the CLI, the UI and the surfer's walk.
- [x] (stage G) **One thing in pieces** (`docs/eval/fractured-names-2026-09-27.md`):
      75 missed same-type merges (casing, ß/ss), 34 subtype pairs, ~1,700
      unrelated-type splits, mostly tool/method (640 names). A resolution
      rule for which type pairs may name one thing, with a person deciding,
      and a `heal` ailment that lists them. Apple Inc. is six entities.

- [ ] **Compress what repeats in an edge list.** The cap
      (`docs/log.md`, 2026-09-25) bounded `traverse` at 70-78 KB, and
      left the repetition untouched: provenance is 30% of a fact list
      across 268 distinct tuples, and `src` is the entity's own name
      once per edge. A dictionary would take a bounded 70 KB to a
      bounded 40 without dropping anything. Worth doing when something
      needs the room, not before.
- [x] (2026-09-27, `docs/ontology-v9.md`) **Ontology v9** — the relations a document takes name `paper`
      where they could name `document`, so a captured page has to be
      read as a paper for an edge to fit. A version bump and a restamp,
      not a rules change.
- [x] (stage H: shared titles) **The hub entities** — 164 of the 200 biggest hubs are papers, and
      the largest, `Proceedings of the International Conference…` at
      degree 1,951, is a container that should probably not be an entity
      at all. Cheap, and a precondition for communities.
- [x] (2026-09-27: the full re-read, as the user decided) **Decide full re-run versus delta pass** for the 1,021 documents
      extracted against an older ontology version
      (`docs/log.md`, Stage 3).
- [ ] **Entity ailments worth adding to `prax heal`** when they show up:
      entities that differ only by case or punctuation, and the like
      (`docs/log.md`, "The heal pass").

### Retrieval

- [x] (stage D, 2026-09-26: the hand-out walks on from where it was, 770 ms
      once a walk and then 1 ms against 755–800 every time, measured on a
      copy halfway through; the delta is written every 30 s
      (`door.vector_save_seconds`) and when the queue drains, and the rows a
      killed door had not written are forgotten at start) **The embed pass redoes its finished work twice a batch** (measured
      2026-09-25, mid-re-embed; "round-trip bound" was the wrong first
      answer — marshalling is 7%). Two quadratic costs, both the same
      shape: work proportional to what is already done, repeated per
      batch.

      **The hand-out scans past everything finished.**
      `pending_embeddings` is `LEFT JOIN chunk_embeddings ... WHERE
      model != ? ORDER BY c.id DESC`, which SQLite plans as `SCAN c`.
      Measured at the halfway mark: it walks **1,198,689 already-embedded
      rows** to find the next 2,000, 759 ms, and worse the further it
      gets. A cursor — hand out below the last id given — or an index
      that lets it skip.

      **Every batch rewrites the whole delta index.** `work.take_in`
      calls `store.save_vectors(model)` after *each* POST, and that
      writes the accumulated delta: 31 MB at the halfway mark, headed for
      about 800 MB before a merge. A 200-vector batch rewrites the entire
      file, which is the 10-17 s the door logs as a slow POST. Save every
      N batches or on a timer; `merge_vectors` already folds it.

      Together: 331 chunks/s of embedder delivering 98 early in a run and
      67 at the halfway mark, degrading as it goes. The workaround while
      it stands is a big batch and several workers
      (`-n 600 --workers 3 --interval 1`).
- [ ] **Document-aware rerank input** (title + heading path + chunk) as
      a measured experiment (`docs/log.md`, Stage 2).
- [x] (stage I) **taco and tacos are different searches** (`niggles.txt`).
- [x] (stage D: the walk skips what it has passed) **The embed hand-out after a rechunk** — `GET /work/embed` and the
      backlog it leaves (`docs/log.md`, "The night of 2026-09-20").
- [x] (F: the slice a night) **The figures backlog, and why it was not moving** — 3,692
      waiting (`docs/log.md`, 2026-09-24).

### The UI and the agent

- [ ] **A document's own neighbourhood** — a link from a document to
      what it is connected to (`niggles.txt`).
- [x] (stage J) **One workflow for maintenance and healing** (`niggles.txt`).
- [x] **Sub-graph export / import** — the answer to "what if a repo's
      notes want to travel" (`docs/log.md`, "The night of 2026-09-20").
      (Stage T, `docs/graph-files.md`.)
- [ ] **The plugin writes `.prax/graph.jsonl`** at session end, beside
      `.prax-project`, with `prax export --project` (the last part of
      T's design).
- [ ] **A procedural graph for the surfer** (later; the reference is in
      `docs/log.md`, "After the mathematics").
- [ ] **If the UI still feels slow from the MacBook** — the next suspect
      is named in `docs/log.md`, "The night of 2026-09-20".
- [ ] **Resource-aware swapping in `prax up`** — only if marker re-reads
      become routine (`docs/log.md`, "After the mathematics").
- [ ] **The extension sends a PDF viewer's tab as a snapshot.** A tab
      showing `arxiv.org/pdf/…` or IEEE's `stamp.jsp` is saved by
      SingleFile as the viewer's frame, not the PDF: four documents
      (9539, 9574, 9741, 10112) hold only the frame's markup. The
      extension should send the PDF the frame shows, as it does for a
      tab whose URL ends in `.pdf`. Since 2026-09-29 such a page no
      longer falls back to plain text, so it waits as a failed parse.

### The backfill

- [ ] **The old zoetrope disk** — the hash inventory and the import that
      follows it (`docs/log.md`, Stage 1).

## What holds the card, and what a batch may ask for (planned, 2026-09-25)

Not a new service. `prax up` already is one: roles it keeps alive,
`on_demand` for a role that cannot fit beside the others, and
`up.swap(data_dir, to, back_when="idle")`, which is the marker evening.
What the day found missing is narrower, and in this order.

- [ ] **See who holds the card.** `prax.host.hostinfo` reads `nvidia-smi`,
      which gives totals: prax could say the card was at 23.7 of
      24.5 GB and not say by whom. Under WDDM per-process VRAM is a
      performance counter, `\GPU Process Memory(*)\Dedicated Usage`,
      and it names llama-server 20.8, the parse worker's Docling 2.25,
      the compositor 0.8. This is the item that pays: the embedder ran
      at **9 chunks/s instead of 331** for most of a day and nothing
      said why, which turned a one-hour job into a thirty-four-hour
      estimate. A few dozen lines in `hostinfo`, shown by `prax status`
      and the jobs view.
- [ ] **Ask the runtime what a text costs, rather than guessing.**
      `models.trim_guessed` bounds a prompt by characters *and* UTF-8 bytes
      because it has no way to ask how many tokens that is — a
      heuristic that works and is still a guess. llama.cpp serves
      `/tokenize`; a `runtime.measure(text)` would make it a fact. This
      is the class of bug that cost 71 failures and three documents that
      could not be read at all on 2026-09-25, and a wrong token estimate
      is a hard failure where a wrong batch size is only slow.
- [ ] **Let a queue ask for the card.** `swap(back_when="idle")` exists
      and a person invokes it. "A million chunks pending and no ask
      traffic for ten minutes" is a policy the machinery could already
      execute. Last of the three, and hysteretic if it is built at all:
      the 35B takes about three minutes to load, so a policy that
      changes its mind quickly is worse than none.

**Not** a general resource manager. The serving host has no contention
to arbitrate (invariant 7), the supervisor is `prax up` and not a second
thing beneath it (invariant 4), and most of what went wrong on 2026-09-25
was not resources at all — a stale configuration, a unit error, and the
session's own memory guard.

### And the one that would make it moot

The architecture already allows the model work to run elsewhere, and that
is what invariant 4 is for: a worker never opens the database or the
archive, it fetches work and originals over HTTP and posts results back
(`door.get_bytes`), so `--door` pointed at another machine is all it
takes. There are two axes and they are independent — the **worker**
elsewhere, or just **llama-server** elsewhere, since `server-35b` is an
`openai` model with a `base_url`.

Moving llama-server is one line of prax.yaml and dissolves the
contention above completely: Docling and the embedder get the card to
themselves and the 35B answers over the private network. Whether there
is a second machine to put it on is hardware, not design.

What does **not** pay is distributing `embed`. It is round-trip bound
already — 331 chunks/s of embedder delivering 98 on loopback — so a
second GPU would hit the door's ceiling before it gained anything. The
model steps (extract, titles, summaries, sections, vocabulary, typing)
distribute cleanly, since they fetch text and post small JSON; `parse`
distributes acceptably, being heavy compute for a megabyte down and
kilobytes up.

## Deployment shape: the board holds the store, the desktop does the model work (planned)

Agreed 2026-09-12. The queue already exists implicitly: every document
carries its state (parsed or not, the ontology version it was read
under, vectors or not, a title guess tried or not), so "what needs a
model" is a selection over stamps at any moment, idempotent and
restartable. What is missing is doing that work from another machine,
since a batch pass opens the SQLite file directly today.

- [x] A work protocol on the door (2026-09-12, `prax.work`): `GET
      /work/{step}?limit&scope` hands out a leased batch, `POST
      /work/{step}` applies the results; the door stays the only writer,
      the index-release dance is gone (delta indexes), leases expire
      back into the pool.
- [x] The worker (2026-09-12, `prax.worker`, `scripts/work.py --door
      http://<board>:8000 --watch`): drains the queue whenever this
      machine is on, with its own prax.yaml and the never-spend rule;
      the door itself keeps what needs no model (the drop folder, HTML
      captures, dedupe).
- [~] On the board: everything the code needs is in place (2026-09-12) —
      the `serve` extra, `vectors: {dtype: i8}` in prax.yaml, the token,
      `prax serve --host <private address>`, the worker and the MCP proxy
      pointed at it with `--door`/`PRAX_DOOR`; and `deploy/` (later the
      same day) holds the install script, the systemd unit with a 1.5 GB
      cap, the board's `prax.yaml`, the env file for the address and the
      token, and `worker.ps1` for the desktop. What is left is the move
      itself, which needs the board: copy the store, run the install
      script, measure the door's memory there, decide `i8` or the
      desktop's `f16`.

## A level above the neighbourhood: communities as nodes (planned, 2026-09-25)

`docs/eval/traverse-neighbourhood-2026-09-25.md` measured what the second
hop returns and why it is 3.4 MB, and the repair it proposes — a path
shape and a ranking by how many documents connect an idea to the entry
point — fixes the size for one call's work. It does not give the graph
anything to say *above* the neighbourhood, which is the other half of
what a reader wants: not only "what is next to the Fourier transform"
but "what region of the library is this".

The standard answer is GraphRAG's: partition the entity graph into
communities (Leiden, hierarchically), write a summary of each with a
model, and keep the summary as a node of its own. It suits prax better
than it suits most, because the machinery is already here — the
`sections` step writes a summary per heading region with the local
model, and a community summary is the same pass over a cluster of
entities instead. Nothing paid, nothing new to install.

What it would want, in order:

- [x] **A partition, and a way to keep it.** Leiden over the live edges,
      folded to canonical entities. It is a derived index like chunks,
      so it may be rebuilt at any time; the question is what triggers a
      rebuild. Probably the `maintain` clock rather than every write.
- [x] **A summary per community**, written by the `sections` model over
      the cluster's entities and their strongest edges, stamped with
      what it read so a moved partition makes it stale rather than
      wrong — the rule `meta.sections` already follows.
- [x] (2026-09-28: traverse, the graph view and search; ask measured and left off, no gain: `docs/eval/regions-2026-09-28.md`) **A way in.** `traverse` names the community an entry point sits
      in; `search` may offer it as a hit of its own; `ask` gets a
      cheaper way in than chunks for a question about a region rather
      than a fact.
- [x] **A decision about the hubs first.** 164 of the 200 biggest hubs
      are papers, and the largest is `Proceedings of the International
      Conference…` at degree 1,951 — a container that should probably
      not be an entity. A partition computed before that is decided
      will cluster around artifacts. This is the cheap part and it
      comes first.
- [ ] *Optional:* **Leiden instead of Louvain.** Louvain can leave a
      community internally disconnected (two pieces held together by a
      node that has since moved away); Leiden guarantees connected
      communities and usually finds a slightly better split. It needs
      `igraph` and `leidenalg`, two compiled packages, where Louvain comes
      with networkx. The change is `communities.partition`. Worth it only
      if a region turns out to be two unrelated things, or if the
      packages prove easy on the Pi-class host.

Built on 2026-09-27 as stage P (`docs/communities.md`): a pass, two
tables, a staleness rule and a model step.

## A confidence that was measured, not written (planned, 2026-09-25)

Every edge in the graph carries `confidence`: EXTRACTED, INFERRED or
AMBIGUOUS. The model *writes that word* and `extraction.py` stores it —
`t.get("confidence", "AMBIGUOUS")`. Invariant 8 says edges are evidence
rather than truth and makes confidence a column on the fact, and then
fills that column with an unvalidated claim. A model's probability of
emitting the token "EXTRACTED" is not the probability that the triple is
right.

Prompted by `doc:10307` ("Jev's Architecture Unmasked", captured
2026-09-24), which is a speculative reverse-engineering of a closed API
and says so. Nothing here depends on that API being real or on adopting
it; what the piece gets right is the critique, and it lands on prax.

Four passes have exactly the shape it describes — shared state, one
question, a fixed set of allowed answers:

| pass | the question | what it costs now |
|---|---|---|
| extraction | how sure is this triple? | a generated word |
| `typing` | which of N types is this? | a generated name |
| `vocabulary` | is this name English; does it fold? | a generated answer |
| `adjudicate` | are these two entities one thing? | Opus 5, $2.59 for 8,242 pairs |

prax reads **no logprobs anywhere** today (grammars exist, for the
surfer). A single constrained token plus its logprob gives a number out
of the model already running, at no extra cost — the answer is one token
either way.

**The experiment comes before the change, and the labels already exist.**
The adjudicated round recorded 3,683 merges and 4,559 declines: a
labelled outcome set of 8,242 decisions, which is exactly what is needed
to find out whether a local logprob predicts anything.

- [x] **Ask the local model the adjudicator's question** on those 8,242
      pairs, under a grammar that allows one token, and keep the
      logprob. A few hours of llama-server, nothing spent.
      (2026-09-27: `scripts/eval_confidence.py`, `prax.graph.calibration`. Only
      6,126 of the pairs can be labelled: the 2,704 declines still
      recorded, and 3,422 merges reconstructed from the unstamped merges
      of the likely types, against Opus's 3,683. No grammar: the mass on
      "yes" against "no" among the token's top ten. The full run starts
      when the re-read ends.)
- [x] **Score it against the labels.** Reliability diagram and Brier
      score, not accuracy: the question is whether 0.8 means 0.8, not
      whether the argmax is right. Raw logprobs from an
      instruction-tuned model are famously not calibrated, which is
      exactly why the article's subject trains against outcomes — so
      expect to need Platt or isotonic scaling fitted on part of the
      pairs and measured on the rest.
- [x] (2026-09-28: `docs/eval/confidence-2026-09-28.md`; isotonic ECE 0.022, agreement 0.80)
      **Read the full run.** It starts by itself when the re-read ends
      (`confidence_after_reread.ps1` in the session scratchpad, the answers
      and `score.md` beside it). Its numbers go into
      `docs/eval/confidence-<date>.md`, with the pilot's.
- [x] (2026-09-28: 117 merges with different numbers judged; the rule then made editions one thing) **The wrong merges it points at.** Pairs labelled "same" that the
      local model rejects near 0 (preorder/postorder traversal, a transform
      and its short-time variant) are likely bad merges in the graph,
      whoever made them. They go on the review page (stage R) as merges to
      look at, and `POST /graph/unmerge` or a single split undoes one.
- [x] (2026-09-28) **The adjudicator answers by number, not by position.** The full
      run found Opus "declining" plain spelling variants (cutoff /
      cut-off, realtime / real-time): forty pairs a call answered as a
      list of booleans, a short list padded with `False`, so one missing
      answer shifts every later one. Each answer should name its pair.
- [x] (2026-09-28: 221 decisions) **A gold sample first.** The pilot (200 pairs) found the local
      model right where the label was wrong, both ways. Opus kept apart
      LDR/LDRs and Gauss-Seidel/Gauß-Seidel, and the "same" labels hold
      preorder/postorder traversal and the fractional Fourier transform
      with its short-time variant. Agreement with Opus therefore measures
      Opus's errors too. A few hundred pairs a person has labelled (the
      review page of stage R can collect them) are what both models are
      scored against before anything acts on a threshold.
- [x] (2026-09-28: the local model at 0.9, the middle to a person, no paid tier; `resolution.LocalAdjudicator`) **Then decide what it buys.** If it calibrates, the likely tier
      can act on a threshold and send only the uncertain middle to the
      paid model, which is where the money goes. If it does not, that is
      a finding worth writing down and the paid tier stays as it is.
- [ ] **Only then, `confidence` as a number.** A column beside the
      three words rather than instead of them, written where a pass
      measured it and left null where nothing did — an edge whose
      confidence nobody measured should say so rather than claim a
      number. A migration, and not one to start before the numbers
      above exist.

Deliberately after the current run of work: it is a measurement with a
possible change behind it, not a change.

## The engineering pass of 2026-09-28: packages

Asked for by the user: "a lot of separate source code files that may be
moved to (sub)packages". There were 52 modules at the top of `prax`,
beside five packages.

- [x] **45 modules into 8 packages** by what they are about: `text`,
      `graph`, `writing`, `answering`, `ml`, `capture`, `host`, `wall`
      (CLAUDE.md, "The layout"). Moved with `git mv`, the imports
      rewritten by their syntax tree, the dotted names and paths in the
      docs rewritten too, except `docs/log.md` and `docs/eval/`, which
      record what things were called when they were written. Seven
      modules stay at the top: the foundations and the protocol the
      invariants name.
- [x] **What a call costs is `prax.ml.pricing`,** not
      `prax.graph.extraction`: the price table, the cache shares,
      `cost_usd` and `supports_effort` were used by asking, surfing, the
      pipeline, resolution, the budget and the model registry, and made
      each of them import the extraction.
- [x] **`prax.text` stands on nothing of prax** at module level, and a
      test holds it.

Found and left, each its own decision:

- [x] (2026-09-28: the rule stated and held by
      `test_the_store_stands_only_on_what_is_below_it`) **The store
      reaches up, lazily.** Twelve calls from inside functions to
      `writing`, `parsers`, `models`, `wall`, and the graph beyond the
      ontology. Moving them out would fight invariant 3: a pass that must
      touch the tables and needs a step's logic lives in the store. So the
      store is the door, not the bottom layer. At the top of a module it
      imports only config, `text`, `ml` and `graph.ontology`; above that
      only inside a function. An import at the top would be a cycle.
- [x] (2026-09-28: `worker.py` 933 lines to 274) **`steps` imported
      `worker` lazily** in every family, for the `do_*` functions. They
      live in their step's module now (`steps/parse.py` has the reading
      chain and its heartbeat), as invariant 4 says a step is one object
      with both halves; `worker.py` is the loop, the drop folder and the
      give-up counter, and `steps.base.say` the line a pass writes. The
      two copies of `_paid` are `ModelSpec.paid`.
- **`text.compounds` asks the store** for the forms a word takes, and
  only the store uses it. It is retrieval, not text; it waits for
  `store/retrieval.py` (1,916 lines) to split at 2,000 as invariant 3
  says, and goes there.
- [x] (2026-09-28: `__init__.py` 1,706 lines to 437) **Code in a
      package's `__init__`:** the parsers are parts by what they read
      (`base`, `code`, `pdf`, `marker`, `web`, `readings`, `office`,
      `djvu`) with an `ORDER` a test holds; the `__init__` keeps the
      registry and re-exports every name. A test that replaces a parser's
      helper replaces it on its part now.
- **Large modules:** `store/repair.py` 1,625, `host/up.py` 1,435. Neither
  is tangled. Both are long lists of the same shape (ailments, roles).

## The engineering pass of 2026-09-25, as work

`docs/audit/engineering-2026-09-25.md` is the review. These are its
findings in the order they pay, and one that came out of writing it.

### A step is an object, not three if-chains

- [x] (stage E, 2026-09-27) **`work.hand_out` (363 lines), `work.take_in` (295) and
      `worker.run_once` (267) are the same dispatch written three times**,
      over the same step names, in two modules. Adding `sections` meant
      editing all three in the same order: its batch cap in one, its
      lease in another, its results in a third, and its worker side in
      the fourth place.

      A `Step` with `hand_out`, `take_in` and `do`, registered once, so a
      new pass is an object rather than four edits. The lease and the
      scope handling are the same for every step and belong to the
      registry; what differs is the query, the payload and the model
      call.

      It touches every step, so it is a stage of its own and wants the
      tests first — the work protocol is where a mistake costs a batch of
      real work.

### The store's modules outgrew the split that was already made

- [x] (stage E, 2026-09-27) **`store/documents.py` is 2,739 lines and `store/graph.py` 2,044**,
      against the 2,000 that made `prax.api` a package on 2026-09-22.
      `graph.py` splits along what it holds — entities, edges, labels,
      traversal — and `documents.py` along documents, chunks, the
      document field. The module order is an invariant, so a split has to
      keep it and the checker in the audit script is what proves it did.

### The reads outside the store are growing, not shrinking

- [x] (41 → 0 on 2026-09-27, stage E) **26 on 2026-09-22, 38 now**, in 13 modules. The last pass left
      them with "worth doing when a module's query breaks on a schema
      change"; the count going up is the evidence that the answer was
      wrong. A store read per question is the cleaner surface. The
      importers are the bulk (`zotero.py` alone has 10) and are also the
      least coupled to the rest, so they are the place to start or the
      place to exempt deliberately.

### Three names for one idea

- [x] (renamed 2026-09-27: `trim_guessed`, `trim_measured`, `sections.budget`)
      **`models.fits`, `models.fits_for`, `sections.read_for`**, all
      added on 2026-09-25. The wrapper earns its place — the budget's
      defaults belong to the pass rather than to `models` — but nothing
      in the name says that. `sections.budget()` reads as what it is.
      Small, and the kind of thing that is free now and confusing later.

### Name what kind of invariant each one is

- [x] (stage B, 2026-09-26) **The ten invariants are held in four different ways, and the file
      does not say which.** That matters because it decides what can
      catch a breach:

      | kind | how a breach is caught | which |
      |---|---|---|
      | **checked** | a test fails | 3 (the module order, no writes outside the store), 5 (what the proxy imports), 10 (the importers open read-only) |
      | **enforced** | the code path makes it true | 2 (register hashes the bytes), 4 (the locks), 8 (`link` stamps, nothing deletes), 9 (`check_edge`) |
      | **measured** | only a number says | 6 (response size), 7 (resident RAM in the serving path) |
      | **chosen** | a standing decision with a revisit threshold | 1 (SQLite, and when to stop) |

      The point of the table is the third row. **Invariant 6 was breached
      for months and nothing noticed** — `traverse` answered 3.4 MB and
      no test could have failed, because "keep responses small" is not a
      property of the code, it is a property of an answer to a real
      question on a real library. A measured invariant needs a recurring
      measurement or it is a wish.

      So: say the kind beside each invariant in `CLAUDE.md`, put the
      checkable ones in a test (the audit script already checks three of
      them by hand), and give the measured ones a number that a pass
      reports — the largest answer `search`, `get` and `traverse` gave
      this week, and the door's resident memory.

## Later / maybe

- A prax plugin for Obsidian (or SiYuan) as a *client*: search hits, a
  document's facts, and a note that becomes a prax page. Their editors
  are years ahead of our textarea, and the MCP tools are already the
  API. What prax does before anyone writes is what neither has: the
  archive, the staged readings, the graph as evidence (`niggles.txt`,
  "comparison to other systems", 2026-09-21)
- Streamable-HTTP MCP transport for remote access over Tailscale, and the
  MCP server proxying the HTTP door instead of importing the store
- Litestream replication of `data/prax.db`
- Kùzu migration script (only if the entity threshold is crossed)
- Complement / "blast-radius" SQL tools exposed via MCP
- Karakeep or Linkwarden as an additional capture front-end feeding the inbox
- [x] The door exited once with `STATUS_BAD_STACK` (0xC0000028, a native
      unwind, 2026-09-19 23:38) while an embed post merged the 1.2 GB
      index and a search read it; `prax up` restarted it in a second.
      usearch suspected; the merge no longer holds the index lock for
      its build. Watch `logs/up.log` for another. (2026-09-27, stage S:
      four native exits in all, none since 2026-09-20; see the log.)

