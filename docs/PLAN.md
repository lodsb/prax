# prax build plan

What is next. The record of what was done, with the reasoning and the
measurements under each night, is `docs/log.md`.

Work one stage per Claude Code session. Each stage ends green: tests
pass, `ruff` clean, and the stage's checklist fully ticked before moving
on. Decisions: `docs/rationale.md`. Source details: `docs/sources.md`.

## The order, agreed 2026-09-26

Everything below is written up somewhere in this file or in
`docs/audit/engineering-2026-09-25.md`. What was missing was an order,
and the order is load-bearing rather than a preference — each stage is
either a thing that might be broken now, a thing that makes the next
stage safe, or a thing a user would notice.

### A. Settle whether the surfer got worse — done 2026-09-26, cleared

- [x] **What the surf sees when it walks changed under it.** `prax.surf`
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

- [ ] The four kinds, the tests, and the measured pass: "Name what kind
      of invariant each one is", below. **Invariant 6 was breached for
      months and no test could have failed**, which is the argument for
      doing this before the refactors rather than after them: a
      structural change is exactly when a silent breach gets introduced,
      and three of the checks currently live in a scratch audit script
      instead of the suite.

### C. What a user actually notices — the two parts done 2026-09-26,
### and a design gap found under them

- [x] **German compounds** — `prax.compounds`, the library as its word
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
- [ ] **Re-judge what the old rule ruled out.** 979 of the 4,103
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

- [ ] The embed hand-out's cursor and the delta's save frequency ("The
      embed pass redoes its finished work twice a batch", below). Both
      small, both measured, and the re-embed that found them is done, so
      there is no rush and no reason to leave them.

### E. The structural work — last, and one stage each

- [ ] **The Step object**, tests first: it is where a mistake costs a
      batch of real work.
- [ ] **The store's module split**, after the Step object, since both
      touch the largest modules and the module order is an invariant that
      B will have made testable.
- [ ] **The reads outside the store**, which the split partly dissolves.
- [ ] **The three names** (`fits`, `fits_for`, `read_for`), which ride
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

- [ ] **`prax maintain --rechunk`** — the `ad`, `comment` and
      `ingredients` regions only arrive with a re-chunk (10,261
      documents; a chunk whose text did not change keeps its id and its
      vector). An overnight job.
- [ ] **The unread figures** — 55,607 left of 93,158, sliced so a
      request does not pre-empt the parse queue for days.
- [ ] **The seven pending promotions** — `prax work --steps promote
      --spend -n 7`, roughly $1–3 on Sonnet 5; or the standing shape,
      `spend: true` on the worker role with `budget: {daily_usd: N}`.
- [ ] **`twin-documents`** (6) — the repair retires documents.
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

- [ ] **German compounds are one FTS token.** `Apfelkuchen` matches
      nothing; `Apfel` and `Kuchen` each match. For a compound query the
      keyword half of the hybrid contributes nothing at all, which is why
      the German answer held a chickpea pan and an insertion sort. German
      is 1,988 documents here and compounding is how the language builds
      nouns. FTS5 takes a custom tokenizer and a splitter needs a word
      list rather than a model, so this is the cheapest of the three.
- [ ] **A common noun loses to a brand that owns the word.** `apple`
      returns Logic Pro and Logic Express manuals — correctly, by BM25,
      since they say it far more often. Even in English the fruit is
      unreachable until "bars" disambiguates. prax handles one name
      meaning two things in the *graph* (the `proposes` pass, the review
      queue) and not at all in retrieval.
- [ ] **An ingredient list without a heading is not recognised**, so the
      cross-lingual bridge has nothing standing on it. `prax.ingredients`
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

- [ ] **Compress what repeats in an edge list.** The cap
      (`docs/log.md`, 2026-09-25) bounded `traverse` at 70-78 KB, and
      left the repetition untouched: provenance is 30% of a fact list
      across 268 distinct tuples, and `src` is the entity's own name
      once per edge. A dictionary would take a bounded 70 KB to a
      bounded 40 without dropping anything. Worth doing when something
      needs the room, not before.
- [ ] **Ontology v9** — the relations a document takes name `paper`
      where they could name `document`, so a captured page has to be
      read as a paper for an edge to fit. A version bump and a restamp,
      not a rules change.
- [ ] **The hub entities** — 164 of the 200 biggest hubs are papers, and
      the largest, `Proceedings of the International Conference…` at
      degree 1,951, is a container that should probably not be an entity
      at all. Cheap, and a precondition for communities.
- [ ] **Decide full re-run versus delta pass** for the 1,021 documents
      extracted against an older ontology version
      (`docs/log.md`, Stage 3).
- [ ] **Entity ailments worth adding to `prax heal`** when they show up:
      entities that differ only by case or punctuation, and the like
      (`docs/log.md`, "The heal pass").

### Retrieval

- [ ] **The embed pass redoes its finished work twice a batch** (measured
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
- [ ] **taco and tacos are different searches** (`niggles.txt`).
- [ ] **The embed hand-out after a rechunk** — `GET /work/embed` and the
      backlog it leaves (`docs/log.md`, "The night of 2026-09-20").
- [ ] **The figures backlog, and why it was not moving** — 3,692
      waiting (`docs/log.md`, 2026-09-24).

### The UI and the agent

- [ ] **A document's own neighbourhood** — a link from a document to
      what it is connected to (`niggles.txt`).
- [ ] **One workflow for maintenance and healing** (`niggles.txt`).
- [ ] **Sub-graph export / import** — the answer to "what if a repo's
      notes want to travel" (`docs/log.md`, "The night of 2026-09-20").
- [ ] **A procedural graph for the surfer** (later; the reference is in
      `docs/log.md`, "After the mathematics").
- [ ] **If the UI still feels slow from the MacBook** — the next suspect
      is named in `docs/log.md`, "The night of 2026-09-20".
- [ ] **Resource-aware swapping in `prax up`** — only if marker re-reads
      become routine (`docs/log.md`, "After the mathematics").

### The backfill

- [ ] **The old zoetrope disk** — the hash inventory and the import that
      follows it (`docs/log.md`, Stage 1).

## What holds the card, and what a batch may ask for (planned, 2026-09-25)

Not a new service. `prax up` already is one: roles it keeps alive,
`on_demand` for a role that cannot fit beside the others, and
`up.swap(data_dir, to, back_when="idle")`, which is the marker evening.
What the day found missing is narrower, and in this order.

- [ ] **See who holds the card.** `prax.hostinfo` reads `nvidia-smi`,
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
      `models.fits` bounds a prompt by characters *and* UTF-8 bytes
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

- [ ] **A partition, and a way to keep it.** Leiden over the live edges,
      folded to canonical entities. It is a derived index like chunks,
      so it may be rebuilt at any time; the question is what triggers a
      rebuild. Probably the `maintain` clock rather than every write.
- [ ] **A summary per community**, written by the `sections` model over
      the cluster's entities and their strongest edges, stamped with
      what it read so a moved partition makes it stale rather than
      wrong — the rule `meta.sections` already follows.
- [ ] **A way in.** `traverse` names the community an entry point sits
      in; `search` may offer it as a hit of its own; `ask` gets a
      cheaper way in than chunks for a question about a region rather
      than a fact.
- [ ] **A decision about the hubs first.** 164 of the 200 biggest hubs
      are papers, and the largest is `Proceedings of the International
      Conference…` at degree 1,951 — a container that should probably
      not be an entity. A partition computed before that is decided
      will cluster around artifacts. This is the cheap part and it
      comes first.

Not started. It is a pass, a table and a staleness rule, so it wants its
own stage rather than a corner of the traverse change.

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

- [ ] **Ask the local model the adjudicator's question** on those 8,242
      pairs, under a grammar that allows one token, and keep the
      logprob. A few hours of llama-server, nothing spent.
- [ ] **Score it against the labels.** Reliability diagram and Brier
      score, not accuracy: the question is whether 0.8 means 0.8, not
      whether the argmax is right. Raw logprobs from an
      instruction-tuned model are famously not calibrated, which is
      exactly why the article's subject trains against outcomes — so
      expect to need Platt or isotonic scaling fitted on part of the
      pairs and measured on the rest.
- [ ] **Then decide what it buys.** If it calibrates, the likely tier
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

## The engineering pass of 2026-09-25, as work

`docs/audit/engineering-2026-09-25.md` is the review. These are its
findings in the order they pay, and one that came out of writing it.

### A step is an object, not three if-chains

- [ ] **`work.hand_out` (363 lines), `work.take_in` (295) and
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

- [ ] **`store/documents.py` is 2,739 lines and `store/graph.py` 2,044**,
      against the 2,000 that made `prax.api` a package on 2026-09-22.
      `graph.py` splits along what it holds — entities, edges, labels,
      traversal — and `documents.py` along documents, chunks, the
      document field. The module order is an invariant, so a split has to
      keep it and the checker in the audit script is what proves it did.

### The reads outside the store are growing, not shrinking

- [ ] **26 on 2026-09-22, 38 now**, in 13 modules. The last pass left
      them with "worth doing when a module's query breaks on a schema
      change"; the count going up is the evidence that the answer was
      wrong. A store read per question is the cleaner surface. The
      importers are the bulk (`zotero.py` alone has 10) and are also the
      least coupled to the rest, so they are the place to start or the
      place to exempt deliberately.

### Three names for one idea

- [ ] **`models.fits`, `models.fits_for`, `sections.read_for`**, all
      added on 2026-09-25. The wrapper earns its place — the budget's
      defaults belong to the pass rather than to `models` — but nothing
      in the name says that. `sections.budget()` reads as what it is.
      Small, and the kind of thing that is free now and confusing later.

### Name what kind of invariant each one is

- [ ] **The ten invariants are held in four different ways, and the file
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
- [ ] The door exited once with `STATUS_BAD_STACK` (0xC0000028, a native
      unwind, 2026-09-19 23:38) while an embed post merged the 1.2 GB
      index and a search read it; `prax up` restarted it in a second.
      usearch suspected; the merge no longer holds the index lock for
      its build. Watch `logs/up.log` for another.

