# prax build plan

What is open, and in what order. What was done, with the reasoning and
the measurements under each night, is `docs/log.md`; the finished items
this file used to carry are there too, under "Moved from the plan".

Work one stage per Claude Code session. Each stage ends green: tests
pass, `ruff` clean, and the stage's checklist fully ticked before moving
on. Decisions: `docs/rationale.md`. Source details: `docs/sources.md`.

## Where things stand (2026-10-20)

This replaces the order of 2026-10-05. That order is in `docs/log.md`
under "Moved from the plan"; its items 1 to 5 are done, and 6 to 8 are
below.

### The state

- **The library.** 18,387 documents, every one held in the archive
  (`documents.held` = 1). The database is at migration 49. No `places`
  rows yet.
- **The code.** The suite has 1,549 tests. The full run of 2026-10-19
  gave one failure, a swap test that read the machine's memory. It is
  fixed (a5aac19); the full suite has not run since.
- **What runs.** The door and the worker run on the desktop under `prax
  up`. The card is off until its hardware is fixed: llama-server is
  held, so the local-model steps and the readings that need the card
  (marker, the OCR readers, the figure readings) wait. Do not start
  card work before then.
- **Reviews.** The safety review and the quality review of 2026-10-18
  are closed. All 20 defects and all 23 findings were fixed by
  2026-10-19 (below). The next review is due after some weeks of
  commits, over the range since 2026-10-18.

### Done since 2026-10-05

About 115 commits; `docs/log.md` has each night.

- The 2026-10-05 order's items 1 to 5: N3's rest, AL "as Utopia does
  it", the standard-names and world-dates measurements, the wire
  checks, AJ's reader contract and AK's split as code.
- The `worlddates` step on (2026-10-07), G2 and the spacing twins
  applied (2026-10-09).
- A PDF gets the reader that suits it (2026-10-17). AO steps 1 to 5:
  the text checks, `meta.quality`, the counts over the library, the
  document page's line and the Admin tab. The fixes the checks ask for
  are queued for when the card runs.
- The UI: pages as a tree, the graph view filtered and steady, the web
  files cached and compressed, craft alone or with what builds on it,
  a module's browse list without the unassigned.
- The door's time for a worker's hand-outs. `store.graph.context` split
  into `lookups`, `context` and `views`.
- AQ designed (`docs/media-by-reference.md`) and its stage 1 built:
  `places`, `places.roots`, `store.original_file`.

### Next, in order

1. (done 2026-10-22; its first run on the NAS is the user's) **AQ stage 2: the crawl and `where`.** This stage needs no mount and
   no card. It adds `POST /places` and `prax_send.py --places <root>`,
   run on the NAS: every file hashed in full and posted in batches,
   unseen rows marked `gone`. Then `GET /places` (by doc, hash, or root
   and path), `GET /places/single`, `GET /places/duplicates`, and the
   MCP `where` tool behind the wall. The first run over 5 TB takes about
   14 hours at the NAS's hashing speed.
2. (answered 2026-10-23) **The user's answer on what is referenced.**
   Photos, audio and video are; everything else is held
   (`docs/media-by-reference.md`, the question at the end).
3. (done 2026-10-23; its first run on the NAS is the user's, with the
   share mounted on the door's host) **AQ stage 3: references.** `POST
   /ingest/reference`, `--reference`, range serving from a place, the
   backup's `referenced` count, heal's `unreachable-originals`, and the
   document page's places.
4. **Without the card, in any order.** AS, versions of a work (steps 1
   to 4 need no model). AP's design note (global ids beside the integer
   keys). The engineering leftovers: typed shapes (a
   search hit first) and the remaining complex functions. The measured
   improvements that need no model ("Retrieval and ask", "The graph").
5. **Once the card runs.** AO's queued fixes: marker for the 586
   documents with maths as text and the 353 scans read from their OCR
   layer, and the crops. Then the latex2html re-import and its formulas.
   Then AJ's trial on marker and its routing rules, AI's measurement
   from `run/swaps.jsonl`, and AK. AQ stage 4 (a transcription step)
   comes last.

**Waiting on the user:**

- the list below, whose numbers are of 2026-10-06;
- AE, the distilled surfer;
- AH, the Rust spike, which waits until the schema stops moving
  (migration 49 landed on 2026-10-19);
- the move to the board (not now, 2026-10-12);
- AA's labeller, retrained as genres are labelled.

### For the user (the list of 2026-10-06)

What waits on a person, with the numbers of that evening. Nothing else
in the plan is blocked on these, except where an item says so.

**Judging** (the Review page, or a file):

- [ ] **The faithfulness gold set**: 150 sentences in
      `C:/prax-data/eval/faithfulness-2026-10-06/to-label.md`, a
      `label:` line each (yes, partly, no), about an hour. Then
      `scripts/eval_faithfulness.py labels` and `report` on that folder.
      Decides whether the 27B is right or too strict as a checker.
- [ ] **"Same thing?"**: 797 likely pairs nobody has decided (`GET
      /graph/candidates`; 241 tools, 239 papers, 156 concepts, 111
      methods). Each decision also grows the judge's calibration set.
- [ ] **Split names**: 2,615 names held by things of unrelated types
      (`GET /graph/split-names`).
- [ ] **Genres**: 194 documents labelled; the labeller's labels on
      12,551 are open to check. Corrections go back into its training
      (AA).
- [ ] **Personal?**: 1 suspected document (Admin page).

**A yes or no**, each a dry run shown and applied on the word:

- [x] (2026-10-09, the user's word) **G2**, one fact under two ontology
      versions: `prax heal --check duplicate-facts` applied, 89 facts left
      by then, 162 later edges ended, the oldest kept.
- [x] (2026-10-09, the user's word) **Spacing twins**: `prax heal --check
      spacing-twins` applied, 759 groups, 774 entities folded in one run.
- [x] (on 2026-10-07, the user's word) **The `worlddates` step on**: `steps.worlddates.model` in
      prax.yaml (howto 3e). Some 500 dated facts over a night or two,
      18 of 18 right on the samples (`docs/eval/world-dates-2026-10-06.md`).
- [x] **Push**: done 2026-10-06.

**On the notebook** (noted 2026-10-04): `git pull`, `/mcp` reconnect
(it brings the `changes` and `connect` tools), the project re-synced
(the acceptance test of AL steps 3 to 5), and the browser extension
reloaded for its `alarms` permission; then O1 and O7 re-checked.

**Decisions for later**: AE (the distilled surfer), AH (the stack), the
move to the board (not now, 2026-10-12), the rest from the NAS (later),
and the rerank, which is measured only when it is meant to be on. The
zoetrope disk is done (drive R:, 2026-10-12).

Not for a hand: the review queue's 23,964 misfit triples. They are
the evidence a module grows from, read in bulk, not one by one.

### A review of the last three days (done 2026-10-05, `docs/log.md`)

The safety review is done: 27 findings confirmed by a workflow of ten
agents, every one fixed with a test that fails before the fix. The
quality review with the project's skill (`.claude/skills/code-review`,
full mode) over the same range followed: 24 findings, planned below.

### The quality review's fixes (agreed 2026-10-05)

The quality review's 24 findings (`docs/eval/code-review-2026-10-05.md`,
numbered there) in the order the user agreed: one step a session.

- [x] (2026-10-05; the judge's re-measure with the new sameness wording, `steps.adjudicate.platt`, done the same afternoon, `docs/log.md`) **Step 1, now: the two of high cost, the quick performance fixes, and the fraction in the general parser.**
      - [x] **1.** Two definitions of when a venue edition is 'the same thing': sameness.yaml says merge, the venue tier says keep apart and link part_of (an hour plus a re-measure of the judge)
      - [x] **2.** cited_but_missing reads every reference chunk in the library because the planner picks idx_chunks_kind over the doc_id IN list (an hour)
      - [x] **10.** The ASCII-fraction fix went into schema.org's ingredient_name only; the general ingredient parser still misreads '1/2 cup' (an hour plus a rechunk of recipes)
      - [x] **12.** connect_entities scans the whole edges table on every call, under _HELD_LOCK, just to read the index stamp (an hour)
      - [x] **13.** connect with as_of builds the whole path index on every request: about 1 s and a 148 MB peak each, with no bound on concurrent builds (an hour)
      - [x] **14.** cite_link's 100 ms budget is checked only between steps, and one step can take far longer: folding a book, or a library-wide FTS phrase query (an hour)
      - [x] **24.** A named token's hidden set is rebuilt by a documents scan each time it is needed, and staleness adds a second build to every search (an hour)
- [x] (2026-10-05) **Step 2: one rule in two places** (the project sync first: it can fork a project's documents).
      - [x] **3.** Two ways to sync a project, keyed differently, with the title and key rules written twice (an hour or two)
      - [x] **4.** The heal check for functional conflicts recomputes what rules.find_conflicts keeps, by a different rule (an hour)
      - [x] **16.** A second JSON-LD reader in text/dates, which misses what schemaorg.nodes handles (an hour)
      - [x] **17.** Two patterns for a display formula's equation number, which disagree (an hour plus a rechunk)
      - [x] **18.** The stale states and the status-to-relation map defined in three places (under an hour)
      - [x] **23.** Functional conflicts are computed twice, by heal and by the conflicts pass, with different rules for which edges count (an hour)
- [x] (2026-10-05) **Step 3: the words in data, and the code independent of this library** (with an eval script for the venue tier).
      - [x] **6.** Venue tier merges at confidence 1.0 every night, with English/German rules tuned on this library and no script that re-measures them (a stage (eval script plus lexicon move))
      - [x] **7.** Status reader marks a project note stale when an ordinary English sentence near its top starts with 'Archived', 'Wrong', 'Replaced', 'Void'… (an hour)
      - [x] **8.** Recipe markup turns French, Italian or Spanish ingredient lines into wrong EXTRACTED ingredient entities; the kitchen word lists are English/German code literals outside the kitchen pack (an hour to half a stage)
      - [x] **9.** venues.py keeps its own lists of venue words, publishers and legal forms beside the lexicon that already holds them (a few hours)
      - [x] **11.** store.document_node is the new single answer to 'a document as a graph node', but pages and the references pass still hard-code 'paper' (an hour or two, plus a heal or resolve round for the twins)
      - [x] **15.** Venue rules keep the words that say what a name is as code sets, partly copied from the lexicon (a stage (small))
      - [x] **19.** Path hop strength is a Python list of relation names from the packs, and it has already drifted from the ontology (an hour or two plus an eval run)
      - [x] **21.** Path costs class relations as strong or weak by a hard-coded list of research and studio relation names (an hour)
      - [x] **22.** Which schema.org types prax knows is listed twice: OWN_TYPES in code, same_as in genres.yaml, and the two disagree (an hour)
- [x] **Step 3's two leftovers** (2026-10-05):
      - [x] (applied 2026-10-05, run `heal:document-twins:20261005T130208Z`:
            450 entities folded, the dry run's count; on 2026-10-06 the
            check found one new twin, a capture since) **Document twins.** 733 titles have entities of two or more
            document types (146 article and paper, 108 build and paper, 94
            manual and paper, 53 paper and recipe…), mostly from page links
            and citations that typed every document a paper. New links
            type it as `store.document_node` does; the old twins want a
            heal check that folds each into the type `document_node`
            gives, dry run first.
      - [x] (2026-10-05, no change on this library's plan) **Roman editions.** "Atti del XX Colloquio" reads no edition:
            a roman numeral meets real acronyms (CHI, MIX, VR). Only where
            a venue word follows it, measured with `eval_venues.py`.
- [x] (2026-10-05, placed in AI and AL) **Step 4, for the plan's own sections.**
      - [x] **5.** Repairs that end an edge and write its correction do not record which edge corrects which (an hour for the record in the two repairs; a migration only if a new column is chosen over restated_as)
      - [x] **20.** The card plan's swaps are recorded only as log prose, so AI's last step has no data to measure (an hour)

### The quality review of 2026-10-18

The safety review of the range since 2026-10-04 first (20 defects,
fixed), then the quality review in full mode over it: 23 findings
(`docs/eval/code-review-2026-10-18.md`, numbered there). The user
agreed on 2026-10-18 to fix the costly ones and the cheap language
ones at once, and to plan the rest here.

- [x] (2026-10-18, `docs/log.md`) **Fixed together.** 1 (a repair's
      edges ended one commit per edge), 2 and 8 (five arXiv-id grammars),
      3, 12, 16 and 21 (a fix decoded from a word, a scan rule of the
      reader choice's own, the LaTeX readers listed three times), 5 (the
      name repair knew research's types only), 6 and 14 (world dates
      guarded in English only), 7 (latex2html's formulas never asked
      for), 9 (the page poll scanned the edges table), 20 (a reference
      list's biographies told by English verbs).
- [x] (2026-10-19, `docs/log.md`) **The other ten.** 4 (the PDF profile
      kept by the original's hash, one `quality.assess`), 10 (the library's
      citers counted only where they order the list, within a budget), 11
      (`ontology.SELF_KINDS` in the five places), 13 (the LaTeX conversion
      in `prax.parsers.latex`), 15 (one slug grammar, `prax.text.slugs`, and
      the front-matter pattern from `prax.text.status`), 17 (mdwiki's
      layout an option, what it leaves out counted), 18
      (`scripts/eval_quality.py`; `SAME_PAPER` left a constant, in its
      measured gap), 19 (`host.commit_reserve_mb`), 22 (heal's thin-texts
      leaves the checks' scans to them), 23 (`changes` counts a side once).

## Stages

- [ ] **AO. A text says what is wrong with it.** The user, 2026-10-17:
      "the most fluent workflow atm for me is to drop it into prax and
      tell you 'to fix it'. a bit annoying". Every fix of that week was a
      pattern prax could have seen itself: a scan's OCR layer read as the
      text, a paper's maths as a scatter of symbols, 438 of 465 figures a
      caption alone, a latex2html comment kept as text (`MATH $…$ $…$`),
      `*f**s*` for a subscript.

      Steps 1 to 3 done 2026-10-18 (`docs/eval/quality-checks-2026-10-18.md`:
      586 documents with maths as text, 353 scans read from their OCR
      layer, 1,320 with bare captions, 5 short; a sample of 12 of the
      maths all real after one tightening).

      1. The checks (`prax.parsers.quality`): a text, its stamp and the
         original's profile (`parsers.pdf.profile`) in, findings out,
         each with its count, up to three pieces of evidence and the
         reading that would put it right, if one would. No model, no
         store. First checks: `ocr-layer`, `maths-as-text`,
         `bare-figures`, `short-text`, `leftovers`.
      2. The record: `meta.quality`, the findings with the checks'
         version and the text's hash, written by the door when a text
         lands, so a re-read makes it stale rather than wrong.
      3. Measured over the library before anything acts on it: how many
         documents each check flags, a sample of each read by hand
         (`docs/eval/`). A check that flags what is fine is changed or
         dropped before it can queue a reading.
      4. (done 2026-10-18) Shown: the document page's line of findings
         ("checks: maths as text 152 → marker", the evidence on hover)
         and the Admin page's "text checks" tab, its counts, a check's
         documents and a button asking for its fix over them. The HTML
         pymupdf4llm writes on purpose (`<sup>`, `<br>`; 7,426 documents)
         already shows as what it means: the page's sanitizer lets it
         through and a table's cell splits on `<br>`.
      5. (done 2026-10-18, the backlog's fixes that need the card not
         asked for until the bigger power supply is in) Acting:
         `pipeline.follow_ups` asks for the fix a finding names
         instead of its own conditions (the reader choice and the crops
         of 2026-10-17 are the first two). A check never ends or
         replaces anything; a finding with no fix only flags.

- [ ] **AI. A plan for the card: what waits, what a swap costs, and
      "do it now".** The user, 2026-10-01: "shouldn't the whole resource
      management be more aware of all the functionality and plan/batch?"
      and "if the user just wants it done there is no point in waiting
      for a maintenance run".

      *Why now.* Two marker readings waited two days on 2026-10-01.
      Marker is `on_demand` and stays paused until a person starts it.
      Its deferral also leased the whole document, which kept two
      vision-pages readings of the same paper out of every batch; that
      half is fixed (1c7fec1: only the reading waits). `swap: auto`
      exists in `prax up`, but it swaps as soon as anything waits. It
      would have taken the card from llama-server in the middle of the
      maths eval to read two PDFs. The plan of 2026-09-25, "What holds the
      card" (now in `docs/log.md`), said "not a general resource manager". What
      changed since: four kinds of work now compete for one card
      (llama-server's steps, marker, the labeller's training, the
      eval and ask traffic), and a person asks for some of it to be
      done now.

      *The model.* The door groups what is waiting by the role that
      must hold the card and by action. On 2026-10-01 that was marker
      with 2 readings, and llama-server with 2 vision-pages readings
      beside the extraction backlog. Each group gets a cost:
      - the swap: the measured load time of the role that would take
        the card (the 35B takes about three minutes) and of the role
        that gets it back;
      - the work: items times the group's own rate (`work.demand`
        already measures `rate` and `hours_left`);
      - the wait: how long the oldest item has waited, weighted by who
        asked. A person's request outweighs a backlog pass, and "do it
        now" outweighs everything but an ask in flight.

      The plan is the cheapest order of holders over the next hours,
      as a small graph: a node is "this role holds the card", an edge
      is a swap with its cost. Greedy with hysteresis comes first. A
      group that is small and not asked for waits for the nightly
      window (`worker.nightly`) unless it grows past a size or a person
      fast-forwards it.

      *Who does what.* The door computes the plan, because it holds the
      queues (`GET /work/plan`, beside `/work/demand`). `prax up`
      carries it out, because it owns the processes. No second
      supervisor (invariant 4). An ask in flight, or one in the last few
      minutes, keeps llama-server: a swap never interrupts a streaming
      answer.

      *The steps.*
      - [x] (2026-10-02, 2a04f4c) **A lent card holds.** An idle server
            whose card is lent waits for it and comes back with it; before,
            its waiting work reloaded it beside the borrower, and marker and
            the 35B together ran the machine out of memory.
      - [x] (2026-10-02) **Do it now.** The jobs view groups what waits
            by role and action, each with its count, rate and whether it
            needs the card, and a "do it now" per group (`POST
            /work/now`). The door keeps the request in its demand
            (`now`); `prax up` swaps at its next look, unless an ask is
            in flight or ended in the last five minutes (`ask_holds`).
            The supervisor acts; the door supervises nothing. The load
            time per swap is the next step's number.
      - [x] (2026-10-02) **Load times.** `prax up` notes each role's
            load time, from start to its first health answer, keeps the
            last five in `run/loads.json`, and puts their median in the
            status (`load_s`). The jobs view shows it beside "needs the
            card". The plan's swap costs are those numbers, not guesses.
      - [x] (2026-10-02) **`GET /work/plan`** (`prax.host.plan`): each
            group with its swap cost (the load times of the role taking
            the card and of the one getting it back), its work (items over
            rate) and its wait (the oldest request, `store.waiting_since`),
            and a decision: being served, next, or waits. A person's
            request goes next after waiting three times its swap, the
            door's after twenty, a group of 50 at once and a "do it now"
            straight away. The jobs view shows the decision and its
            reason on each group. Nothing acts on it yet: that is the
            next step.
      - [x] (2026-10-02) **`prax up` follows the plan.** It asks
            `/work/demand?plan=true` on its look every 20 s and gives the
            card to the first group the plan puts next (`_follow_plan`).
            That replaces the old `swap: auto` rule (swap as soon as
            anything waits): `auto` now means "follows the plan", and a
            role without it moves only for a person's "do it now". The
            plan never takes the card from a role still serving its own
            readings, and puts a role a person asked for before the
            others, so the card does not flip back. *For the user:*
            marker on the desktop is still `swap: ask`; `swap: auto` on
            its `run:` line is what lets it start without a hand.
      - [ ] Measured: how long readings wait before and after, and how
            many swaps a day the plan makes. Fewer swaps for the same
            waits is the point. First a record to measure from (the
            quality review's finding 20): a swap is only a line of prose
            in `logs/up.log` today, which breaks as soon as its wording
            does. `_swap` and `_unswap` append one JSON line per swap
            (at, group, from, to, why, the plan's decision) under `run/`,
            bounded like `loads.json`, and a small count pairs the swaps
            of a day with that day's reading waits. Every day without it
            is a day of the plan's own baseline lost. (2026-10-05: the
            record is `run/swaps.jsonl` and the count
            `scripts/eval_swaps.py`; what is left is the measurement,
            after some days of swaps.)

      *Steps folded in on 2026-10-02* (from "What holds the card", 2026-09-25):
      - [x] (done before 2026-10-02: `hostinfo.holders`, "On the card" in
            the jobs view) **See who holds the card.** `prax.host.hostinfo` reads `nvidia-smi`,
            which gives totals: prax could say the card was at 23.7 of
            24.5 GB and not say by whom. Under WDDM per-process VRAM is a
            performance counter, `\GPU Process Memory(*)\Dedicated Usage`,
            and it names llama-server 20.8, the parse worker's Docling 2.25,
            the compositor 0.8. This is the item that pays: the embedder ran
            at **9 chunks/s instead of 331** for most of a day and nothing
            said why, which turned a one-hour job into a thirty-four-hour
            estimate. A few dozen lines in `hostinfo`, shown by `prax status`
            and the jobs view.
      - [x] (done in stage E, 0f8760b: `models.trim_measured` asks
            `/tokenize` and halves until it fits) **Ask the runtime what a
            text costs, rather than guessing.**
            `models.trim_guessed` bounds a prompt by characters *and* UTF-8 bytes
            because it has no way to ask how many tokens that is — a
            heuristic that works and is still a guess. llama.cpp serves
            `/tokenize`; a `runtime.measure(text)` would make it a fact. This
            is the class of bug that cost 71 failures and three documents that
            could not be read at all on 2026-09-25, and a wrong token estimate
            is a hard failure where a wrong batch size is only slow.

      *What would make it moot* (2026-09-25).

      The architecture already allows the model work to run elsewhere,
      and that is what invariant 4 is for: a worker never opens the
      database or the archive, it fetches work and originals over HTTP
      and posts results back (`door.get_bytes`), so `--door` pointed at
      another machine is all it takes. There are two axes and they are
      independent — the **worker** elsewhere, or just **llama-server**
      elsewhere, since `server-35b` is an `openai` model with a
      `base_url`.

      Moving llama-server is one line of prax.yaml and dissolves the
      contention above completely: Docling and the embedder get the card
      to themselves and the 35B answers over the private network.
      Whether there is a second machine to put it on is hardware, not
      design.

      What does **not** pay is distributing `embed`. It is round-trip
      bound already — 331 chunks/s of embedder delivering 98 on loopback
      — so a second GPU would hit the door's ceiling before it gained
      anything. The model steps (extract, titles, summaries, sections,
      vocabulary, typing) distribute cleanly, since they fetch text and
      post small JSON; `parse` distributes acceptably, being heavy
      compute for a megabyte down and kilobytes up.

- [ ] **AD2, what is left.** Steps 1 to 5 and the review round are done
      and measured (`docs/symbolic-maths.md`, `docs/log.md`, 2026-10-02).
      - [x] (2026-10-02) *The tidy-up* (the user: "tests for maths stuff
            should be bundled with the pack"; the review's D2). The pack's
            tests are in `src/prax/packs/maths/tests/`, with the shared
            fixtures in the root `conftest.py`. The maths pieces of the
            core went behind `Pack.chunk_marks`: the generic
            `store.chunks_to_mark` / `set_chunk_marks` pair, the marks in
            `equations_near`, and the pack's note in `nearby_line`.
      - [ ] *Only if the steps above leave a gap:* a question that is a
            calculation (values and a formula) goes a fixed path: the
            formula found, evaluated with the values, the model asked
            only to explain the result.

- [ ] **AL step 9, the client's second page** (doc 13470, checked from
      the laptop on 2026-10-04 against door `1886abb`):
      - [x] (2026-10-05) **N1** `sync_project` for a named token: a dry run, or a sync
            of projects the administrator registered.
      - [x] (2026-10-05) **N2** `cite` words that skip boilerplate (an ACM copyright
            notice, "in order to test"): the matched snippet's words, or
            the chunk's rarest n-gram.
      - [x] (2026-10-05, with the review's finding 3) **N3** `references`
            in the numbers' order; the author biographies appended to an
            entry and ditto authors are still to do.
      - [x] (2026-10-05; the stored entries need a rechunk) **N4** `cited_but_missing`: authors split into first and last
            names, a year read as 1917, no `links`, generic references
            ranked beside topic papers (`min_count`, or a down-rank).
      - [x] (2026-10-05) **G1** a citation the references pass matched with score 1.00
            costs as a strong hop: three clean 3-hop chains came out weak
            (6.45). A sure title match now costs as a stated fact; the
            chains cost 6.1, over the line by their two middle papers'
            hub costs.
      - [x] (2026-10-05, the user: yes; raised to 7 after reading the
            seven random pairs between, five of them real) **G1, the line.** `paths.SOUND` = 6 was read off
            `eval_paths.py`: at 7, 126 of 150 citation pairs are sound
            and 13 of 300 random pairs (5 at 6). Moving it means reading
            those random paths by hand first; the user's call.
      - [x] (2026-10-09, applied) **G2** one fact live under two ontology versions (edges 7402
            and 47184): the older one ended, or kept as evidence. The
            `duplicate-facts` check of `prax heal` (2026-10-05) finds
            1,711 such facts, 2,018 later edges; applying it waits for the
            user's word.
      - [x] (2026-10-05) **G3** `changes(domain=…)` swamped by the night's `part_of`
            corrections: a `producer`/`run` filter or a `corrected` side.
      - [x] (2026-10-05) **G4** the worker not back after a door restart: `health` says
            "worker not reattached", or `prax up` restarts both.
      - [x] (2026-10-05; the stored lists need a rechunk) **N3's rest**: the author biographies appended to an entry,
            and ditto authors (`~~,~~`) taken from the entry before.
      - [ ] **O1, O7** re-checked from the laptop after its `git pull` and
            the extension's reload (both were fixed on 2026-10-04).
- [ ] **AL. The first client's feedback.** Steps 1 to 4 and 6 to 8, and
      most of step 5, were done 2026-10-03 and -04 (`docs/log.md`; the
      full stage is under "Moved from the plan"). Left of step 5:
      - [x] (2026-10-03: the columns and the record time done: migration
            36's `world_from`/`world_to` with their precision, written by
            `store.link` and shown by `traverse` only where present;
            `store.held_at` and `traverse(as_of=)` on the door and the
            MCP tool; invariant 8 reworded. 2026-10-05: a document's
            lifecycle in `get`; the general extraction asked for world
            dates gave none on five documents that state them (the
            eval note of that day). 2026-10-06: **the pass of its own**,
            the `worlddates` step, built and measured
            (`docs/eval/world-dates-2026-10-06.md`: 18 of 18 kept facts
            right at the judge's 0.9); watched and off until the user
            names a model for it) **The world's time beside prax's.** Invariant 8 calls the
            edges bi-temporal, but `valid_from` is set when `store.link`
            writes an edge and `valid_to` when it is retired: both are
            when prax held the fact (transaction time), with
            `ingested_at` beside them. Nothing records when a fact holds
            in the world by its source (valid time), and "retired
            2026-10-02" or "superseded by …" in a document is that. Two
            nullable columns on edges (a migration), filled only where a
            source states a date, and the same on a document's
            lifecycle (`meta`); today's columns documented as what they
            are, and invariant 8's wording in CLAUDE.md corrected with
            it (found 2026-10-03, talking about prax as an agent's
            memory).
      - [x] (2026-10-05: the precision tied to its date by migration
            47's triggers, "ended, date unknown" was migration 36's
            `unknown`, an undated fact `stated` at its document's date in
            `traverse`, and a document's lifecycle in `get`: `published`,
            `status`, `stale`, `retired`) **As Utopia does it** (its code, the research note, section
            10): each world date with a precision (year, month, day),
            and "ended, date unknown" as a state of its own (the end
            null, its precision `unknown`), with CHECK constraints that
            tie a precision to its date; an undated fact anchored to
            its document's date (above); `supersedes` on an edge that
            corrects another, so "corrected" and "rejected" are a query
            (the quality review's finding 5: the `backwards-part-of` and
            `not-venues` repairs end an edge and write its correction, and
            drop `link`'s id, so which edge corrects which is a guess.
            The record belongs where the ending is recorded:
            `edge_endings` gets a column `corrected_by` beside
            `restated_as`, which `restore_run` already reads as "not
            restored yet" and cannot carry a second meaning, and the
            repairs pass the new edge's id. That is the edge-level
            `supersedes` too, rather than a fifth side table beside
            premises, conflicts and endings, and it gives G3 its
            `corrected` side; done 2026-10-05: `edge_endings.corrected_by`,
            migration 46, and `why` shows it);
            and **one module that builds "held at T"** for every graph
            (2026-10-03: `store.held_at` is that module and the walk
            uses it; `test_a_moment_in_record_time_is_built_in_one_place`
            holds every `as_of` read to it and the past-moment form to
            `edges.py`. The 48 live-edge conditions of the graph part
            and 23 elsewhere in the store stay as written: they mean
            "now", and rewriting them would change nothing)
            read (record time: written at or before T, not retired
            before it; no T: not retired), with a test in
            `test_invariants.py` that no read builds it itself. prax's
            `valid_from`/`valid_to` are record time and are renamed or
            documented so.

- [ ] **AN. Rules over the graph.** The ontology annotated and linted,
      the rule pass nightly with its premises checked, the `part_of`
      direction check, functional conflicts kept (`edge_conflicts`), the
      schema.org markup pages carry, and the genres in schema.org's
      words were done 2026-10-04 (`docs/log.md`). Left:
      - [ ] **More meaning from what the library holds, in standard
            words** (the user, 2026-10-03: "the standardized ontologies
            also could make sense to extract some more meaning from our
            current chunks/docs"):
            - (2026-10-05: measured, no gain, the switch stays off;
              `docs/eval/extraction-standard-names-and-dates-2026-10-05.md`)
              the **extraction prompt given the standard names** beside
              prax's (a relation's `same_as`), which may help a local
              model place a relation; a module's version bump re-reads
              what it touches, as any bump does, so it is measured on a
              sample before a pass over the library.

- [ ] **AJ. Readers under prax's hand: every model process a role, the
      OCR readers measured against each other.** The user, 2026-10-02:
      "should we vendor the marker/surya/ocr pipeline in a way that we can
      control it via prax properly... it also begs the question what we
      do with rapid ocr and other ocr subsystems".

      *Why.* On the night of 2026-10-02, with marker holding the card for
      the 29 books, free RAM fell to 3.5 of 31 GB and a background job was
      stopped for memory. `prax up --status` showed llama-server paused.
      The memory was marker 2.0's: surya-ocr-2 is a GGUF vision model,
      and surya's llama.cpp backend (`surya/inference/backends/
      llamacpp.py`) starts a llama-server of its own. It found prax's
      binary on the `PATH` and ran it with `--parallel 8 --ctx-size 98304`
      and llama.cpp's default `--cache-ram` of 8192 MiB. It held 10.3 GB
      of RAM (13.3 GB of commit) for a 1.4 GB model that sat on the card
      in 3.4 GB of VRAM. Its log had 27,589 lines of "making room for
      prompt cache entry": the cache sat at its cap. For OCR it buys
      almost nothing, since every page is a different image. Marker's
      own server held 3.4 GB more, and its layout and OCR-error helpers
      1.8 GB. The log is opened for appending and never rotated (55 MB
      after one night). Prax's own llama-server roles have the same
      8 GB cap today.

      *Not vendoring.* Marker 2.0 has just replaced its OCR stack, and a
      vendored copy makes prax the maintainer of a fast-moving pipeline.
      Marker's code is GPL-3.0 and Datalab's weights have a licence of
      their own (to be read before anything is copied). What prax needs
      to control is already a setting: `SURYA_INFERENCE_URL` (an outside
      server, no spawn), `DETECTOR_SERVER_URL`, `OCR_ERROR_SERVER_URL`,
      `FAST_LAYOUT_SERVER_URL`, and `LLAMA_CPP_EXTRA_ARGS`. Prax owns
      the boundary instead: every process, its flags and its resources.

      *The steps.*
      - [x] (2026-10-03, code; on the desktop at the next start of marker)
            **The memory first.** Every llama-server role passes
            `--cache-ram` (`serve.cache_ram_mb`, 2048 for a chat server,
            0 for a reranker), and the marker role tells surya's server
            `--cache-ram 0` through `LLAMA_CPP_EXTRA_ARGS`
            (`ocr_cache_ram_mb`; `ocr_parallel` for its slots). Surya's
            server stays surya's: it runs inside marker's process tree,
            so it stops with marker. A role of its own (`ocr-server`,
            `SURYA_INFERENCE_URL`) was the plan, but in a card group a
            swap to marker pauses every other member, its OCR server
            included. It needs a companion in groups (marker brings its
            server along), which is step 2's. *Still to measure:* peak
            RAM and commit, VRAM and time per page on the same few books,
            before and after.
      - [x] (2026-10-06, code: `prax.host.readers`, the `ocr-server`
            role, `prax up --readers` and `--lock`; live on the desktop and
            measured 2026-10-06: the OCR server 1.4 GB resident against
            10.3, 1.67 s a page, `docs/eval/ocr-readers-2026-10-06.md`) **The reader contract.** A reader declares in its manifest the
            processes it needs (roles of `prax up`), their resources
            (VRAM, RAM, load time) and the settings prax passes. It runs
            in a pinned environment: marker's venv from a lock file,
            upgraded on purpose and measured. This is the rule the packs
            follow, applied to the extractors. Nothing on the card is
            started behind `prax up`'s back. A role may name companions
            that move with it in a swap (marker and its OCR server, then
            `SURYA_INFERENCE_URL`), and prax trims a reader's own logs
            (surya's grew 55 MB in one night).
      - [x] (2026-10-06, measured on 20 pages made from born-digital
            PDFs, `docs/eval/ocr-readers-2026-10-06.md`: none retired;
            RapidOCR first, marker for two columns, German and maths,
            vision-pages for what neither reads; real scans and
            handwriting not in the set yet) **The OCR readers measured against each other.** Prax has at
            least three ways to read a page that is not text: RapidOCR
            through pymupdf4llm (in the worker, CPU), marker with surya
            (its venv, the card), and vision-pages (llama-server with a
            vision model). They grew one at a time. On a fixed set of
            scans, with a hand-checked sample of their text: text
            quality, time per page, peak RAM and VRAM. The result becomes
            routing rules: which reader a document gets first, and when
            it is read again. A reader that never wins is retired.
            Surya's model and vision-pages are both a vision model behind
            llama.cpp, so after step 1 they share one served-model
            mechanism.
      - [ ] **The routing as rules** (from the measurement above): a
            two-column page or a German one goes to marker after
            RapidOCR's first reading, where a host runs marker. Not built:
            the evidence is one German page and one two-column page, so it
            waits for real scans in the set. (2026-10-06, done: the maths
            density no longer takes a manual's numbered steps for
            equations; `--maths` never selects a document labelled a
            manual or a datasheet, `_numbered_steps`. The *Voron Cascade
            Assembly Manual* had scored 18.)
      - [x] (2026-10-06: each role's peak commit read off its job object,
            kept in `run/commit.json`; the check leaves `COMMIT_RESERVE_MB`
            free on Windows; the manifest says marker's and the OCR
            server's until measured) **The fit check knows commit.** A server's commit on Windows is
            its card and its RAM, and the check counts the card and RAM
            only: the 27B fit beside marker on the card and left 628 MB of
            commit (2026-10-06). Each role's commit measured at its load,
            kept beside its load times, and the check holding a reserve.

- [ ] (2026-10-03: measured, `docs/log.md`: the 3.8 27B wins the maths with the tool and valid triples at a third of the speed; the abliterated build loses nothing; nobody declined a personal document; next: the split and its swap cost, the user's questions by hand) **AK. Another model: a dense Qwen 27B, and an uncensored build of
      it, against the 35B.** The user, 2026-10-03: "qwen 27b is deemed
      to be better at agentic workflows than 35b (probably used less
      memory) and I'd like to evaluate an uncensored version, too.
      curiousity mainly (it is also on my private data)."

      *What is compared.* The host's model now is
      `qwen3.6-35b-a3b-ud-q4ks` (`server-35b`): a mixture of experts,
      3B of its 35B parameters active per token, about 20 GB at Q4. A
      dense 27B uses all of its parameters for every token: more
      reasoning per token on paper, about 16–17 GB at Q4, and several
      times slower per token on the 4090. "Uncensored" builds (the
      refusal direction removed, "abliterated") answer what the
      original declines, and often lose a little reasoning for it. Both
      claims are other people's; prax measures them on its own work.
      The files come from a known quantizer, and their names and hashes
      go in the log.

      *How.* Each candidate is a model entry of its own in `prax.yaml`
      (`server-27b`, `server-27b-u`), served by `prax up` in turn on the
      card. Nothing switches the steps until the numbers say so. On the
      same sets as the 35B, read-only:
      - the surf, which is the agentic part: `scripts/eval_ask.py` on
        the equations questions and `scripts/eval_maths.py` in its four
        ways (tools on and off, grounded and open), scored by
        `scripts/score_maths.py`;
      - extraction: `scripts/bench_extractor.py` on a fixed set of
        documents, triples and how many the ontology accepts;
      - speed and memory: tokens per second reading and writing, the
        load time `prax up` records, VRAM and RAM at its peak;
      - the private documents, counts only: how many summaries and
        extractions of personal documents each model declines or
        hedges. That is where an uncensored build could matter; the
        documents stay on this machine either way.

      *What would follow.* Models are chosen per step (`steps:` in
      `prax.yaml`), so the answer may be a split: the 27B for `ask` and
      the 35B for the bulk passes, or one model for all. Only the
      winner stays on disk.

      - [x] (2026-10-06, code) **The split, as configuration.** A further
            chat server is the role `llama-server-<name>`; a reading
            waits for the role of its step's model (`work.role_work`);
            an ask is a person's "do it now" for its role (`ask_role` in
            the demand), and the ask hold keeps the card with that role
            without stopping the swap to it (howto 4b).
      - [ ] (2026-10-06, the user: "right now I think we can stay with
            27b for most tasks"; the split stays configuration, unused, and the
            35B's file is deleted: a split would download it again first)
            **Its swap cost, measured on the card** (the user's word
            first: the host runs the 27B for everything since
            2026-10-03, and the 35B's file is still on disk). A day with
            `llama-server: server-35b` for the bulk steps and
            `llama-server-ask: server-27b-u` on demand: how often an ask
            swaps, what the first answer waits, what the bulk passes
            lose (`run/swaps.jsonl`, `scripts/eval_swaps.py`). Then one
            model or two.

- [ ] **AA. Close Z.** The re-extraction against the new domains, the
      `computing` v2 review, the `society` rules and the relabel are done
      (`docs/log.md`, 2026-09-30 to 2026-10-01). Left:
      - the labeller's mistakes, corrected on the Review page's genre
        tab, go back into its training (`scripts/train_labeller.py`), as
        the user labels;
      - [x] (2026-10-03) the genre words in the document field: the
        genres a person or the labeller gave, `p` 0.5 or more, no level,
        beside "PDF document" (`meta._genre_words`). On a copy of the
        store, the 62 queries of `queries-library.yaml` scored the same
        before and after (hybrid MRR 0.90, hit@1 0.87; one query from
        rank 8 to 9). The set has no query that names a kind of
        document, so the gain is unmeasured: queries like "… datasheet"
        join the set from the person's labelled documents. Live with
        the next door restart and the nightly `fields` pass.

- [ ] (2026-09-30: a pilot of 50 questions, the teacher cited the expected document in 45; the numbers are with the plan, outside the repository) **AE. The distilled surfer.** A small local model trained on the
      large model's search-and-read trails. Its plan is kept outside the
      repository. It needs a question set and a training run of a few
      hours on the card.

- [ ] **AH. The stack for universal deployment, after the prototype
      settles** (the user, 2026-10-01: "a tight prototype, early version
      before figuring out the tech stack"). Python stays for what it is
      now. The worker side (parsing, OCR, models, training, the packs'
      libraries) stays Python for good. The read path of the door
      (search, get, traverse, the wall) is the part a `prax-core` in
      Rust could take over and make embeddable in other projects through
      a C interface and PyO3. Zig was weighed: good C interop, but no
      tokenizer library and not at 1.0. Not before the schema and the
      store's interface stop moving (three migrations in two days at the
      time of writing). The first step is a spike of about a week: a
      crate that opens a data directory read-only and answers `search`,
      measured against the Python store on the retrieval eval set for the
      same answers and its speed. Only then a decision on going further,
      one store module at a time behind the same `store.<name>`
      interface, with today's tests and golden runs as the proof.

## Retrieval and ask

- [x] (measured 2026-09-25, the same file's "The vector half": hit@10
      0.210 to 0.300, MRR 0.121 to 0.166, brute-force cosine over a fixed
      pool; the plan was not ticked then) **The sections pass on the vector side.**
      `docs/eval/sections-2026-09-25.md` measured the keyword half —
      hit@10 0.087 to 0.130, MRR 0.048 to 0.077 — and says nothing about
      `document_embeddings`, where a query that paraphrases a chapter
      rather than quoting it should do better. The same A/B, the vector
      arm. (The backlog itself is done: 2,050 documents.)
- [x] (2026-10-06: nothing the noise does not also make;
      `docs/eval/sections-ask-2026-10-06.md`, 30 questions about the middle
      of long documents, `tests/eval/questions-sections.yaml`; a book's
      middle is found through its chunks) **What sections are worth to `ask`**, which was the second reason
      for the pass: a cheaper way into a long book than its chunks.
      `scripts/eval_ask.py` is the instrument.
- [ ] **Document-aware rerank input** (title + heading path + chunk) as
      a measured experiment (`docs/log.md`, Stage 2).

- [ ] (2026-10-05: measured on the 62 questions, no gain, not shipped;
      `docs/eval/retrieval-facts-list-2026-10-05.md`. Again only with
      fact-shaped questions in the set) **Facts as one more search list** (2026-10-03, from Graphiti's fact
      sentence and LightRAG's relation vectors): a full-text table over
      each edge's evidence, relation and its entities' names, a list in
      the fusion like the document field, one vote per document through
      `source_doc`. Vectors for it only if the text side earns them (one
      384-d vector per edge; size it first). Measured with
      `scripts/eval_retrieval.py` before and after.
- [ ] (2026-10-06: measured on a follow-up set of 30, no gain, not
      shipped; `docs/eval/followups-focus-vote-2026-10-06.md`, the code in
      the `.diff` beside it. The two cases it lost were papers the graph
      never linked to the method they build on, docs 9778 and 9782: again
      when it does) **A focus entity as a vote** (Graphiti's node distance): in an `ask`
      follow-up, the documents whose edges touch the entity in focus
      and its first hop get one vote in the fusion, a preference like
      `DOMAIN_PRIOR`, never a re-sort.
- [ ] (2026-10-06, the user: a proper analysis first, before anything is
      downloaded or run: what the model is and how it loads (its own
      model code, `trust_remote_code`), its licence, what it was trained
      and tested on, the languages, the length of passage it reads, what
      it costs on the CPU, the alternatives, and how prax would measure
      it; written: `docs/research-faithfulness.md`, three questions for the
      user at its end. The user's answers, the same day: a non-commercial
      licence is fine for now; downloads under 1 GB each are acceptable,
      which leaves MiniCheck-7B out (some 4.5 GB as a GGUF); the gold set
      will be labelled later, so the planted errors and paraphrases are
      measured first. Measured the same night,
      `docs/eval/faithfulness-2026-10-06.md`: of errors planted in a
      sentence it accepted, HHEM caught 41%, LettuceDetect base 19%, the
      27B 97%, but the 27B accepts a third of the originals; the gold
      labels decide whether it is right) **A faithfulness score for `ask`**: an open model run locally
      (Vectara's HHEM-2.1-Open) scores an answer against its passages;
      first as a measurement on the eval sets, then perhaps as a mark on
      the answer.
- [ ] **Reading activity as a signal** (Glean's personal graph): what was
      opened, and when, as a measured vote in ranking; it is also the
      first of the "outcome signals" of the private research note.

## The graph

- [ ] **Compress what repeats in an edge list.** The cap
      (`docs/log.md`, 2026-09-25) bounded `traverse` at 70-78 KB, and
      left the repetition untouched: provenance is 30% of a fact list
      across 268 distinct tuples, and `src` is the entity's own name
      once per edge. A dictionary would take a bounded 70 KB to a
      bounded 40 without dropping anything. Worth doing when something
      needs the room, not before.
- [ ] **Entity ailments worth adding to `prax heal`** when they show up:
      entities that differ only by case or punctuation, and the like
      (`docs/log.md`, "The heal pass"). (2026-10-06: case and punctuation
      are the sure tier's already; what showed up is spacing.
      `prax heal --check spacing-twins` finds 783 groups, 793 entities to
      fold, on a copy of the store; applying it waits for the user's word.)
- [ ] **Only then, `confidence` as a number.** A column beside the
      three words rather than instead of them, written where a pass
      measured it and left null where nothing did — an edge whose
      confidence nobody measured should say so rather than claim a
      number. A migration, and not one to start before the numbers
      above exist.
- [ ] *Optional:* **Leiden instead of Louvain.** Louvain can leave a
      community internally disconnected (two pieces held together by a
      node that has since moved away); Leiden guarantees connected
      communities and usually finds a slightly better split. It needs
      `igraph` and `leidenalg`, two compiled packages, where Louvain comes
      with networkx. The change is `communities.partition`. Worth it only
      if a region turns out to be two unrelated things, or if the
      packages prove easy on the Pi-class host.

- [x] (2026-10-06, measured, not adopted: `docs/eval/judge-precedents-2026-10-06.md`;
      `eval_confidence.py --precedents K`. The calibration set had 56
      pairs of the `split-names` check in it, now left out; the fit on the
      240 pairs of entity types settles 32% at 74/76 against 11% at 34/34,
      in use since 2026-10-06, the user's word) **Precedents for the judge of a pair** (Utopia's adjudication): the
      person's earlier decisions on the Review page, with the reason they
      wrote, given to the model as examples, and its verdicts cached by
      pair and model. Utopia's identity rules (a version is not its
      family; a qualifier trimmed from the front is the same thing, a
      suffix added at the end another; a list is not its members; a
      phrase containing a name is not the name; a parent and its
      subsidiary are two) read against `ontology/sameness.yaml`; a
      change to it is measured again (`steps.adjudicate.platt`).
- [x] (2026-10-06: migration 48, `text.quotes.place`, extraction records
      it, `store.evidence_place` in `why`, the `places` pass for the edges
      before; on a copy the first pass took 197 s and found 38,576 of
      118,729 quotes in their text) **Evidence that knows where it stood**: the quote's character
      offsets in the text artifact and the `text_hash` it was read from,
      on an edge, so a re-parse shows which evidence moved (Utopia keeps
      offsets and the document's version).
- [x] (2026-10-06: `meta.extraction.drops` per document, by reason with a
      count and the first example; `store.extraction_drops` and `GET
      /review/drops` over the library; the UI shows nothing of it yet) **Counted drops**: what extraction skips without queuing it (a
      reason, a count, an example, per document), beside the review
      queue (Utopia's `extraction_drops`).

## The UI and the agent

- [x] (done 2026-09-27, stage L, 8a7ed72: the document page's "graph"
      link, `#graph?doc=N`; ticked 2026-10-06) **A document's own neighbourhood** — a link from a document to
      what it is connected to (`niggles.txt`).
- [x] (2026-10-06: `prax sync --if-auto`, the hook's, refreshes it where
      a project keeps one, written only when more than `exported_at`
      changed) **The plugin writes `.prax/graph.jsonl`** at session end, beside
      `.prax-project`, with `prax export --project` (the last part of
      T's design).
- [ ] **A procedural graph for the surfer** (later; the reference is in
      `docs/log.md`, "After the mathematics").
- [ ] **If the UI still feels slow from the MacBook** — the next suspect
      is named in `docs/log.md`, "The night of 2026-09-20".

## The backfill

- [x] **The old zoetrope disk** — the hash inventory and the import that
      follows it (`docs/log.md`, Stage 1). Done: it was drive R: (the
      user, 2026-10-12).
      On 2026-10-02 the 37 PDFs whose archived copies are cut short
      (Zotero's own copies are the same bytes) were listed in
      `refetch-later.txt`, untracked: they are probably on the NAS.

## Deployment shape: the board holds the store, the desktop does the model work

The work protocol and the worker are built (2026-09-12, `docs/log.md`).
Left:

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

## The engineering pass of 2026-09-30: what it left

The pass itself is in `docs/log.md` (2026-09-30). These were found and
not done.

- [x] (2026-10-19: `store.graph.context` at 1,167 lines is three parts, `lookups`, `context` and `views`) (2026-10-04: `store.repair` done, four parts at 1,890 lines; `store.graph.context` at 954 is not yet past the line) **`store.repair` and `store.graph.context` in parts.** 1,697 and 954
      lines. Their tests reach private state (`repair._glyphs_seen`),
      which would move with the part that holds it.
- [ ] **Typed shapes.** 869 `dict[str, Any]` annotations and no
      `TypedDict`. The first ones worth writing are a search hit (read by
      six modules), `get_document`, a job row and the meta keys.
      (2026-10-06: the search hit is `store.SearchHit`, in `store.base`
      so `brief_hit` in `documents` reaches it; `search` returns it, and
      `ask`, `surf`, `questions`, the door and `evaluation` are checked
      against it. mypy caught a hit's `time` declared a float where the
      chunker writes whole seconds. A test fails when a hit carries a key
      the shape does not declare. `get_document` returns `store.Document`,
      the row and its text window; a test holds it to the table's
      columns. A job row is `store.JobRow` (`get_job`, `last_job`,
      `list_jobs` as a `JobList`), held to its table the same way. The
      door's routes keep plain dicts: a route's annotation is FastAPI's
      response model. 2026-10-06: the meta keys are `store.DocumentMeta`,
      72 of them with their shapes and writers, the reference
      `docs/meta.md`; a write of an undeclared key fails in the tests and
      is logged on a host. Every one of the live store's 63 keys is
      declared. `get_meta` returns `DocumentMeta` and the writers take
      `store.MetaLike`: 56 type errors at first, 39 of them the writers'
      signatures, and one a real misfit, `extraction_error` an object
      where the declaration said a string. A nested object stays
      `dict[str, Any]` until one earns a shape.)
- [ ] **The remaining complex functions.** None is over 21 (the ruff
      ceiling). Worth splitting when next touched: `parsers.video.parse`
      (21), `answering.questions.briefing` (20), `graph.extraction.build_input`
      (19), `capture.routes.routes_for` (19). (2026-10-06: those four
      split, each now 10 or under, the same output before and after; the
      top of the list since is `changes` (20), `sync` and `plan` (19).
      Those three split the same day, the same output before and after
      (`changes` on nine argument sets on a copy, `venues.plan` on its
      2,704 venues); the top is now 18: `refresh_blocks`, `read`,
      `request_of`, `cite_link`; then 17: `apply` (resolution),
      `_command`, `hand_out`, `_elements`. Split when touched, as the
      item says.)
- [x] **Smaller duplication.** The UI's `esc` and `fmtTime` are copied in
      the extension. The summaries and sections helpers that the store
      calls up to could move into `prax.text`. (2026-10-03: the six tests
      that polled a job with their own loop use `tests.conftest.wait_job`,
      which fails with the job's row instead of running on.)
      (2026-10-06: the extension's popup and options page use
      `lib.escapeHtml`, which `lib.js` now exports, instead of a copy each;
      the door's UI and the extension stay two packages, so `esc` and
      `fmtTime` are one copy on each side. Filing a summary in `meta` is
      the store's own (`store.keep_summary`, was `summaries.keep`). What
      the store still calls up to is the sections pass's thresholds and
      `clean_heading`, and a summary's `parse` and `acceptable` in a
      repair: a judge of a model's answer stays with the caller that asked
      (CLAUDE.md), so they stay in `prax.writing`.)

## Two architectural stages (the user, 2026-10-18; not yet ordered)

- [ ] **AP. Ids that mean the same in another library.** "the document
      ids should probably be derived from stable hashes, so i can merge a
      friend's library in, or networking etc and also share a subgraph".
      A document has one already: its original's sha256 (invariant 2);
      the integer id is this store's alone. What has none is an entity
      (its id and its name are local) and an edge. The shape to weigh:
      the integer keys stay (every table and index is built on them),
      and beside them a global id: a document's hash, an entity's minted
      once (a ULID, kept through merges, its merged twins' ids as
      aliases), an edge's the hash of its two ends' global ids, its
      relation and its source document's hash. An export and an import
      speak only global ids; a friend's entity meets this library's
      through resolution (a likely pair, a person's decision), never by
      an equal number. The wall decides what an export may hold: no
      document a token could not see, no edge whose evidence is one.
- [ ] **AQ. An original that stays where it is.** The user, 2026-10-18:
      "on my nas LARGE amounts of video or audio data, i dont want this
      to be duplicated … there may be duplicates all over the place,
      too". The design is `docs/media-by-reference.md` (2026-10-19, with
      the user's answers): whether prax holds an original
      (`documents.held`) is kept apart from where its copies lie (the
      `places` table, a root named in `prax.yaml` and a path in it, every
      file with its full hash). Places are provenance, not graph edges.
      - [x] (2026-10-19) **Stage 1**: migration 49's `places` and
            `documents.held`, `places.roots`, `store.original_file` the
            one reader of an original, `store.record_places`.
      - [x] (2026-10-22) **Stage 2**: `POST /places` with
            `/places/start` and `/places/done`, `prax_send.py --places`
            (and `--under` for one folder), `GET /places`,
            `/places/single`, `/places/duplicates`, the MCP `where` tool;
            the administrator's alone. Its first run on the NAS is the
            user's.
      - [x] (2026-10-23) **The user's answer**: photos, audio and video
            are referenced, everything else held; no size line.
      - [x] (2026-10-23) **Stage 3**: `POST /ingest/reference` (a
            crawl's batch), `prax_send.py --places NAME --reference`,
            `places.reference` (audio, video, images), a Live Photo's
            clip kept a place, `held` set again when the bytes come,
            range serving from a place and "out of reach" with its
            roots, `places` in `get`, the backup's `referenced` count,
            heal's `unreachable-originals`, the document page's places
            and player. Left for later: the Admin page's duplicates
            list; a recording's tags and a photo's EXIF (stage 4 and
            AR's photo stage).
      - [x] (2026-10-23) **Private by default**: every referenced
            original `suspected` at registration (`private.referenced`),
            set aside from the "personal?" list and counted there; the
            document page's "offline for now" with "try again", and no
            broken picture while offline.
      - [ ] **A media view**, the user's idea of 2026-10-23: the
            referenced photos and recordings as a library of their own
            (by folder, by taken date once AR reads EXIF, by album),
            with a group released or marked personal in one decision.
            Not as opinionated as a photo program: it is the wall's tool
            for media first. `GET /documents/suspected?referenced=true`
            is its list today.
      - [ ] **Stage 4**, once the card runs: a transcription step.

- [ ] (2026-10-20: a design study in `docs/biography.md`, five stages, two questions) **AR. A life in the library.** The user, 2026-10-20: a family
      tree, and with it personal photos, friends, their own projects,
      artworks and releases, and a CV's events. A `biography` pack on
      core, kinship included, with genealogy's sources, sameness cases,
      GEDCOM importer and kinship paths in it; a birth as an event
      entity rather than a literal end; a date's uncertainty (about,
      before, between) as a qualifier beside its precision; the weight of
      a source by its genre; facts a person states with a page as their
      source; photos as a core capability (EXIF and XMP before any
      model, `depicts` in core, after AQ stage 3); what is personal left
      to the wall's rules, not implied by the pack.

- [ ] **AS. Versions of a work.** The user, 2026-10-23: "some of my old
      papers are in prax in different versions … not sure if they should
      be either a version or a dep graph". Three kinds of "the same
      paper" are kept apart:

      | kind | example | mechanism |
      |---|---|---|
      | same bytes | one PDF in two folders | one document, several places (AQ) |
      | same text, other bytes | a PDF downloaded twice | a copy: heal's `twin-documents` retires it into the keeper (Jaccard of chunk hashes at least `DUPLICATE_THRESHOLD`, 0.9) |
      | other text, one work | preprint, camera-ready, journal version | a version: nothing yet |

      A version is not a dependency. A dependency is another work: a
      journal paper that extends a conference paper, a thesis built from
      papers, a citation. Those are research's `extends` (alias
      `builds_on`) and the references pass's `cites`, and two works are
      never folded.

      Measured on 2026-10-23, read-only on the live store: 681 titles
      are shared by more than one live document (1,877 documents). Of
      the pairs in groups of at most six, 21 overlap at 0.9 or more
      (copies, heal's), 155 at 0.5 to 0.9, 270 at 0.1 to 0.5 and 623
      under 0.1 (mostly different texts with one title, "Introduction").
      28 groups are larger. The overlap is of exact chunk hashes, so one
      changed word changes a chunk and versions are undercounted. 337
      documents are retired as a duplicate so far, and 9 `supersedes`
      edges are live.

      - [ ] **Step 1, the relation.** A document is a version of a
            `work` (core's type: "a book as a work rather than as the
            document at hand"): a relation document -> work, `same_as:
            schema:exampleOfWork`. `version_of` is kitchen's alias for
            `variant_of`, so the name is another (`example_of`). The
            versions are ordered by `supersedes`, which search's
            staleness already reads. Core 5, additive: the extraction
            stamps move by migration.
      - [ ] **Step 2, the candidates.** Pairs of live documents that are
            likely versions: one title key (heal's) or one arXiv id with
            another `vN`, or one DOI's preprint and its record; a shared
            author surname; and an overlap under the twin line. A
            measure that survives an edit (shingles of words, not chunk
            hashes) ranks them. Kept as candidates like
            `entity_candidates`, not written as edges.
      - [ ] **Step 3, the decision.** A tab of the Review page shows a
            pair side by side (title, date, length, a diff of their
            headings) and takes "version", "copy" or "different". A
            version writes the work (or joins the one either already
            has), both `example_of` edges and `supersedes` from the
            later to the earlier by `meta.published`, `producer: human`.
            A copy retires the twin as heal does. "Different" is kept,
            so the pair is not asked again.
      - [ ] **Step 4, the reads.** Search shows a work's newest live
            version and folds the others under it ("2 other versions",
            with their ids). `get` names the work and its versions.
            `traverse` from the work reaches every version's facts. The
            owner's own papers are the first test set.

- [ ] **AT. Faces.** The user, 2026-10-23: "automatic face recognition
      where i can identify the faces, so everyone is tagged". After AR's
      photo stage, because the taken date and the place make a review
      fast ("every face from one trip"). A person names a group once,
      and the model tags what is clearly theirs.
      - [ ] **Step 1, detection.** A worker step `faces` reads each
            picture through the door (`/doc/N/original`, so a picture on
            an unmounted share waits). A detector finds each face's box
            and landmarks: YuNet (OpenCV, Apache 2.0, ONNX) on
            onnxruntime. A face is a region of the picture: a `face`
            chunk kind with the box as its locator and the detector's
            score in `data` (R13: a kind and a locator shape, no table).
            The detector's name and version are stamped on it.
      - [ ] **Step 2, the vectors.** A recognition model (SFace, Apache
            2.0; 128 or 512 dimensions by model) gives each face a
            vector. It goes into an index of its own,
            `vectors-face-<model>.usearch` beside the text ones
            (invariant 1's pattern: a main file, a delta, a merge).
            InsightFace's models are more accurate, and Immich uses
            them, but their weights are licensed for non-commercial use
            only, so prax does not ship them. A host may name one in
            `prax.yaml` (`faces.model`).
      - [ ] **Step 3, the groups.** The faces are clustered (HDBSCAN
            over cosine distance) into groups that are probably one
            person. A group is a candidate, kept like
            `entity_candidates`. Nothing is named yet.
      - [ ] **Step 4, naming.** A tab of the Review page shows a group
            as a strip of faces and takes a person: a new `person`
            entity or an existing one, so the face and the name in a
            letter are one entity. It can also split a group, merge
            two, mark a face "not a face" or "nobody to name". A name
            writes `depicts` edges (picture -> person, core's, AR) with
            `producer: human`. A face XMP already names (MWG regions)
            and the people a Mac's Photos library names (`osxphotos`,
            an importer) seed the groups.
      - [ ] **Step 5, the new pictures.** A face close to a named
            person's faces gets their name as an INFERRED `depicts` with
            the distance as its confidence and the run as its producer.
            A face between two people, or near none, goes to the tab.
            A wrong tag is ended, never deleted (invariant 8).
      - **The wall.** A face vector is biometric data. Faces, their
        vectors and their names never leave the host: no paid model, no
        vision API. A `face` chunk and a `depicts` edge to a person are
        hidden from a named token without `--personal`, and a picture
        with a named face is `suspected` at least. A referenced picture
        or recording is `suspected` from its registration already
        (`private.referenced`, the user's choice of 2026-10-23).
      - **Measured before step 5 is on by default:** on a sample the
        owner names in step 4, how many faces are tagged at a distance
        line and how many of those are wrong. The line is
        `faces.match` in `prax.yaml`, measured again for another
        library, never fixed in code.

## Later / maybe

From the comparison of 2026-10-07 (`docs/compared.md`, "Ideas worth
taking"): `at` for a world date on `traverse` and `connect`, once more
edges carry world time (the `worlddates` step on); a person's decision
on a conflict (`edge_conflicts`) and the reason with a pair's decision
(`entity_candidates`); sub-property rules; an RDF export with PROV-O and
SKOS terms. Literal values (a fact whose end is a number or a date) are
a change to the edge's shape, and wait for a need.

From the research of 2026-10-03 (the private research note, sections 6
to 8; the library's `agent-memory-landscape` page):

- Synonymy edges as a softer tier below a merge (HippoRAG): a "similar
  name" link the walk may cross.
- A scheduled contradiction report in the questions pass's briefing
  (atomic).
- Exports: SKOS for labels, PROV-O for provenance (producer and run are
  `prov:wasGeneratedBy` and a `prov:Activity`), Google's Open Knowledge
  Format for pages.
- OAuth, or an allow-list of MCP clients (Notion and the vendors).
- Federated sources, asked live and not indexed (Microsoft, Google).
- docling-graph, watched: schema-first extraction from prax's parser
  family, each node with its chunk and page.
- The memory layer: outcome signals, `remember` and `revise`, the
  person's preferences moved into prax behind the wall (the research
  note, sections 1 and 2).

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
