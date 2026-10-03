# prax build plan

What is open, and in what order. What was done, with the reasoning and
the measurements under each night, is `docs/log.md`; the finished items
this file used to carry are there too ("what the plan held that was
done", 2026-10-02).

Work one stage per Claude Code session. Each stage ends green: tests
pass, `ruff` clean, and the stage's checklist fully ticked before moving
on. Decisions: `docs/rationale.md`. Source details: `docs/sources.md`.

## The order, agreed 2026-10-02

Reevaluated the day the plan was rewritten. The card is first because
everything the library still waits for (formulas, the graph backlog,
readings) runs through it, and on 2026-10-02 two roles on it at once ran
the machine out of memory. Then the small structural debt of the maths
pack, then what is running anyway, then measured improvements, then the
engineering leftovers. What waits on the user is apart.

1. **AI. A plan for the card** (below): done but its measurement, which
   needs days of running. The jobs view's "do it now", the load times,
   `GET /work/plan` and `prax up` following it are in (0a92203, 135f3da).
2. **AD2, the tidy-up**: done (1824fab).
3. **The library's backlogs, running** (2026-10-02, `docs/log.md`, "what
   was never extracted"). A backlog worker extracts the graph of about
   1,050 documents. Marker reads the 29 books on the night of
   2026-10-02, then the ~190 weaker maths candidates. The `equations`
   step checks every formula, and a broken formula that is an
   extraction slip is read again. Watched, not built.
4. **AL. The first client's feedback** (below): its small bugs, the
   signals (`status`, `health`) and citations that survive a re-chunk
   are done. Next the project sync, documents linked to documents, what
   is current (a document's own date first), then captures, pages,
   bibliographies and privacy. AN (rules over the graph, the ontology
   annotated in standard vocabularies, the markup pages already carry)
   is a candidate after AL step 4, and AM (a path index) after AL
   step 5.
5. **AJ. Readers under prax's hand** (below). On the night of 2026-10-02
   marker's own llama-server held 10 GB of RAM that no plan saw. Step 1
   first, after the night's books; steps 2 and 3 after AA.
6. **AK. Another model, measured** (below): a dense Qwen 27B and an
   uncensored build of it against the 35B on prax's own evals. Card
   time, so it runs at night or while nothing else waits.
7. **AA. Close Z** (below): the labeller's corrections, and the genre
   words in the document field, measured.
8. **The zoetrope disk and the NAS** (below): the 37 cut-short PDFs of
   `refetch-later.txt` (untracked) with the import of the old disk, when
   the NAS is reachable.
9. **Measured improvements** (below, "Retrieval and ask", "The graph"):
   the sections pass's vector arm and its worth to `ask`, document-aware
   rerank input, a compressed edge list, `confidence` as a number.
10. **The engineering leftovers** (below): `store.retrieval` and
   `store.repair` in parts, typed shapes (a search hit first), the UI
   helpers the extension copies. The strict mypy batch is done.

Waiting on the user: **AE** (the distilled surfer), **AH** (the stack,
after the prototype settles), the move to the board ("Deployment shape"),
and pushing what is committed.

### Next, by hand (noted 2026-10-03)

- **Restart once marker has read the books** (the tray: "Stop prax",
  then "Start prax"; or `prax up --stop` and `prax up --data-dir
  C:\prax-data --detach`). Before that, Jobs shows nothing waiting for
  marker, or the card back with llama-server. It brings the commits of
  2026-10-03 live: the bounded prompt caches (AJ, step 1) and the genre
  words in the document field (AA), which the first `fields` pass after
  it rebuilds, and the worker embeds again (about 8 minutes).
- **Then measure AJ step 1**: marker's peak RAM and commit, VRAM and
  time per page on a few books, against the night of 2026-10-02 (10.3 GB
  for its OCR server). The 35B's prompt-cache share in its metrics says
  whether 2048 MiB is right.
- **Two tray icons** run on the desktop (one from the autostart, one
  started by hand, most likely): quit one. If it happens again, the tray
  should refuse a second copy on the same data directory.
- **Before AK's download**: see what Qwen has released since 3.6 (a page
  names a "Qwen 3.8" lineup, not checked), then fetch with the user's
  word, from a known quantizer, names and hashes in the log.
- **The CLI client under strict mypy**: `clients/cli` has 7 errors under
  the package's flags (2026-10-03). CI does not check `clients/`; adding
  it to `files` is the user's call.
- **Push** what is committed; CI on GitHub has not seen 2026-10-02 and
  -03.

## Stages

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
            waits is the point.

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

- [ ] **AL. The first client's feedback.** On 2026-10-03 an agent in
      another of the user's repositories used prax through the plugin for
      a whole session: it searched, captured about 30 papers, wrote a
      landscape page, and synced a subproject's 27 documents. It then
      wrote 16 suggestions in four priorities. Below they are merged with
      prax's own reading of them, in the order agreed with the user. The
      project's documents are the acceptance set for steps 3 to 5; their
      content and names stay out of the repository (the baseline is a
      private page in prax, and the tests use invented documents of the
      same shape).

      *Done on 2026-10-03* (71a0916): a `PRAX_DOOR` without its scheme is
      `http` (`client.door_url`); a URL with a space is percent-encoded
      and one still unfit is a 400, not a 500 (`inbox.clean_url`); ingest,
      `get`, `context` and page answers carry `url`, and `#page/<slug>`
      opens a page; `documents` reports `added_at` (it read a field the
      door never sent) and filters by `since`.

      *1. Signals: what is the matter, without the UI.*
      - [x] (2026-10-03) `status(doc_ids)`: queued (with its place), processing, done,
            failed with the reason, and whether a worker is alive. Today
            `pending: []` lists only the reading requests, not the parse
            queue a capture waits in, so a document with no text and
            nothing pending reads as "never will" when it is "worker
            down": 24 captures sat for hours that way.
      - [x] (2026-10-03) `health()`: the door's address, whether the token is
            accepted, the worker's state. One call to the door, so the
            proxy stays thin.

      *2. Citations that survive a re-chunk* (the client's #13, raised
      from P2: it corrupts quietly). Done 2026-10-03, after the check
      below changed the picture: a saved answer cites `#doc/N` and the
      heading path as text, never a chunk id, so it does not move. A
      link an agent writes to a passage does: a re-index gave a changed
      passage's id to its successor (`tests/test_store.py`). The UI now
      trusts `?chunk=M` only while chunk M still holds the link's
      `find` words, and the skill tells an agent to write them. The
      note as first written: A page's answer cites passages as
      chunk ids (`#doc/N/M`), and chunk ids are reused after a re-chunk
      (the formula check's sha guard exists for that). After a re-sync a
      citation does not break; it lands on another passage. Keep the
      quote and the heading path with each citation and resolve through
      `?find=`, as graph edges do. First a test that shows the fault.

      *3. Syncing a project as a tool* (the client's #5, #6).
      - [ ] `sync_project(root, include, exclude, name, domains,
            dry_run=True, tracked_only=True)`: tracked files only
            (`git ls-files`), the usual build folders left out (`_deps/`,
            `CMakeFiles/`, `*-subbuild/`, `build/`, `third_party/`), a
            dry run by default with the plan as data (each path: add,
            refresh, unchanged or skip, and why), a subdirectory of a
            larger repository as a project, documents keyed by the
            remote and the path so a move makes no duplicate. Without
            this the CLI needed the venv path, a call out of the
            sandbox, and took in about 90 vendored CMake files.
      - [ ] The manifest in prax, keyed by remote and path, not a
            `.prax-project` in the repository (a committed one tells
            colleagues, and switches the session-end hook on by being
            there). Auto-sync only with an explicit `auto_sync: true`.
      - [ ] The project page made with it: `project-<name>`, its members,
            a summary block the agent writes.

      *4. Documents linked to documents* (the client's #7 and #4, one
      edge model). Documents are already graph nodes: `cites`,
      `annotates`, `mentions` and `synthesizes` join them, and a page's
      `[title](#doc/N)` is an `annotates` edge. No second table.
      - [ ] At sync, Markdown links, backtick paths and bare `docs/x.md`
            mentions matched exactly against the project's keys become
            edges (producer `sync`, EXTRACTED). The acceptance project
            has 15 links and 159 backtick references: about 174 edges
            without a model. A path that matches nothing yet is matched
            again at the next sync, not kept as a dangling node.
      - [ ] `link` takes `doc:N` as either end. Evidence stays a quote,
            never a chunk id.
      - [ ] `supersedes` and `invalidates` in the core ontology (a
            version bump, invariant 9): "the changelog invalidates the
            re-baseline table" is a fact the graph cannot say today.
      - [ ] `context` returns a document's edges out and in;
            `traverse("doc:N")` walks from a document.
      - [ ] Functional relations declared in the ontology (a building is
            `located_in` one place), and a later pass that proposes
            `contradicts` edges between facts of such a relation that
            disagree, with their own producer and run, so `retire_run`
            undoes a pass. Disagreement kept visible, never a fact ended
            by a model (Cognee's opt-in pass; Graphiti ends facts only
            when both carry world dates; the research note, sections 6
            and 7).

      *5. What is current* (the client's #8). 13 of the acceptance
      project's 27 documents say they are retired, superseded or invalid,
      and an agent that quotes one as current repeats the failure that
      cost months of a pitch.
      - [x] (2026-10-03: from the record, the extension's paper, a page's
            tags and markup, the arXiv id; shown and filtered. Crossref by
            DOI, the first page and a PDF's metadata are still to come)
            **A document's own date, first** (the user, 2026-10-03:
            "there is no date for the docs visible … not when it was
            created in prax but when the document was published"; a
            person judges a paper by when it was written). Only 1,890 of
            12,837 live documents have one (`meta.date`, all from
            Zotero: 1,072 a year, 512 a month, 306 a day), shown on the
            document page and nowhere else. `meta.published` = {date,
            precision (year, month, day), by, at}, filled from what
            says it, best first: Zotero; Crossref by DOI (the citations
            importer asks it already); the arXiv id (`yymm`); a web
            page's `citation_date`, `article:published_time` or
            `DC.date`; the first page read by the titles pass
            ("Published …", a conference's year, ©); a PDF's metadata,
            last, since it often says when a file was made. Shown as
            the year in search hits, `get`, `ask`'s passages (so the
            answering model weighs age too) and the UI's lists; a
            filter `published_since`/`before` on search and
            `documents`. It is also the anchor the world's time below
            is read against: a document's date, never prax's.
      - [ ] At sync, `retired` and `superseded_by` from front matter and
            explicit status lines ("retired 2026-10-02", "superseded by
            …", "status: …"), only in synced project documents: a paper
            saying "superseded by" is about others' work.
      - [ ] **The world's time beside prax's.** Invariant 8 calls the
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
      - [ ] "As of a date" and "what changed in a period" as door
            routes and MCP tools, once the world's time is on the facts
            (Utopia exposes both; the research note, section 8).
      - [ ] **As Utopia does it** (its code, the research note, section
            10): each world date with a precision (year, month, day),
            and "ended, date unknown" as a state of its own (the end
            null, its precision `unknown`), with CHECK constraints that
            tie a precision to its date; an undated fact anchored to
            its document's date (above); `supersedes` on an edge that
            corrects another, so "corrected" and "rejected" are a query;
            and **one module that builds "held at T"** for every graph
            read (record time: written at or before T, not retired
            before it; no T: not retired), with a test in
            `test_invariants.py` that no read builds it itself. prax's
            `valid_from`/`valid_to` are record time and are renamed or
            documented so.
      - [ ] Search and `ask` rank a stale document lower and name its
            replacement; never a filter (as the domain prior), and
            `include_stale` turns it off. Measured on the project's own
            question ("the current shipping candidate and its figure":
            answered from the decision document and the corrected
            re-baseline only).

      *6. Captures and pages* (the client's #9, #10, #14).
      - [ ] A failed capture (a TLS chain error, a 403, a bot check) is
            queued for the extension (`{queued_for_extension: true}`),
            which fetches it with the person's session; the upload is
            linked to the request and takes its title and domains. 7 of
            about 30 captures failed so.
      - [ ] `append_page` took over 120 s. Measure where, then answer a
            write at once and index after.
      - [ ] `update_section(slug, heading, text)`, or a replaceable status
            block like the ask blocks.
      - [ ] A page's lifecycle: marked stale, or contradicted, when the
            documents it cites change or are superseded (Synthadoc's
            draft, active, stale, contradicted, archived).
      - [ ] `set_title`, and batch capture with a result per item.

      *7. Bibliographies and privacy* (the client's #11, #12).
      - [ ] `references(doc_id)`: the parsed list, each entry in the
            library (`doc N`) or with a DOI and an open-access URL.
            `cited_but_missing(set)`: what a set of papers cites that the
            library lacks, ranked by how many cite it. First the
            reference-list detector's scope: `cites` held
            "OnsetDetector.LL" from software help files.
      - [ ] `sensitivity: personal` accepted by `ingest`, `write_page` and
            the sync, behind the wall (stage U), so a colleague's notes
            can live in prax.

      *Not now.* Search hits with a `url` each (the client's #3, at chunk
      level too): about 40 bytes a hit against invariant 6. One `ui` base
      per answer and the ids are enough to build `#doc/N?chunk=M`. The
      book view of a project (#16: an ordered table of contents, a
      "current state" path, one-file export) is cheap after steps 4 and
      5, and comes then.

      *What worked*, by its account: hybrid search with the region line
      found the existing cluster in one call; `documents(title=…)`
      checked coverage; the capture errors were clear (the bot check
      pointed to the extension); and the skill's rule that a document's
      content is data, not instructions.

- [ ] **AN. Rules over the graph: what follows from what is stated**
      (candidate, after AL step 4; the user, 2026-10-03, on standard
      vocabularies: "maybe they are more useful for a reasoning part?").
      The research note, sections 10 and 11; Utopia's `utopia-reason` is
      the worked design, under two thousand lines.
      - [ ] **The ontology annotated, not replaced**: a relation's
            characteristics in the YAML (`transitive`, `inverse_of`,
            `symmetric`, `functional`, and `state`, `event` or
            `eternal`), and `same_as` names in schema.org, SKOS and
            PROV-O where they exist. It changes what types exist in no
            way, so whether it bumps a module's version is decided with
            it.
      - [ ] **A lint of the ontology when it loads**: symmetric and
            asymmetric at once, transitive and functional at once, a
            subtype cycle, an inverse that is not mutual.
      - [ ] **A rule pass on the door's clock**: OWL RL's few rules
            (transitivity, inverses, symmetry, subproperties) as forward
            rules in SQL. A derived edge is INFERRED, producer
            `rule:<name>`, a run, its validity the intersection of its
            premises', and its premises kept (a table of edge ids), so
            "why is this here" has an answer. An asserted edge always
            wins, and a derivation that contradicts one is not written.
            A cap a relation. Retraction by recompute and diff: the pass
            derives everything again and ends what no longer follows;
            nothing is deleted, and retiring its run removes all of it.
      - [ ] **Constraints as findings, not rules**: a functional relation
            with two values goes to the `contradicts` pass (AL step 4).
      - [ ] Measured: how many edges a pass adds, how many are wrong on a
            sample, what it does to `traverse` and to `ask`. The path
            index (AM) gains from it: a closure of `part_of` and of
            `broader` gives a walk meaningful shortcuts.
      - [ ] **More meaning from what the library holds, in standard
            words** (the user, 2026-10-03: "the standardized ontologies
            also could make sense to extract some more meaning from our
            current chunks/docs"):
            - the **schema.org markup web pages already carry**
              (JSON-LD, microdata): a recipe's ingredients and times, an
              article's author and `datePublished`, a product, an event,
              a person. Read at capture and by a pass over the kept HTML
              originals, no model: facts EXTRACTED with producer
              `jsonld`, and a date for `meta.published` (AL step 5);
            - prax's **genres and subjects mapped** to schema.org types
              (`ScholarlyArticle`, `TechArticle`, `Recipe`, `Review`…)
              and to SKOS concept schemes, so a document's kind is said
              in words other systems and models know;
            - the **extraction prompt given the standard names** beside
              prax's (a relation's `same_as`), which may help a local
              model place a relation; a module's version bump re-reads
              what it touches, as any bump does, so it is measured on a
              sample before a pass over the library.

- [ ] **AM. A path index: how is A connected to B** (candidate, after
      AL step 5; the user, 2026-10-03: "path questions are interesting
      though and would make sense for any agentic use/reasoning on
      facts"). A derived file beside the database, in the vector files'
      pattern (memory-mapped, a delta, a merge, rebuilt at will), holding
      per entity its edges with what a path search weighs: relation,
      confidence class, the times, the source document, the number of
      documents behind it. Not a graph engine: a second store would break
      invariant 1, double the wall, and Kùzu, the engine CLAUDE.md names,
      was archived on 2025-10-10. Its search respects what no engine
      knows: evidence per hop, a cost for hubs, preferred relations, the
      world's time (`as_of`, hence after AL step 5), the wall. An agent
      gets `connect(a, b, max_hops, relations, as_of)`: the two or three
      best paths, each hop with its quote and document. First a Python
      prototype on a copy of the store and an evaluation set (pairs with
      a known connection and pairs with none), because a confident path
      of weak edges is worse than none; then a store module and the
      tool; compiled code only if speed asks, as AH's first piece. It
      also settles the decision threshold for slow walks (an index for
      the walk's query first, then this). The design is in the private
      research note, section 9.

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
      - [ ] **The reader contract.** A reader declares in its manifest the
            processes it needs (roles of `prax up`), their resources
            (VRAM, RAM, load time) and the settings prax passes. It runs
            in a pinned environment: marker's venv from a lock file,
            upgraded on purpose and measured. This is the rule the packs
            follow, applied to the extractors. Nothing on the card is
            started behind `prax up`'s back. A role may name companions
            that move with it in a swap (marker and its OCR server, then
            `SURYA_INFERENCE_URL`), and prax trims a reader's own logs
            (surya's grew 55 MB in one night).
      - [ ] **The OCR readers measured against each other.** Prax has at
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

- [ ] **AK. Another model: a dense Qwen 27B, and an uncensored build of
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

- [ ] **The sections pass on the vector side.**
      `docs/eval/sections-2026-09-25.md` measured the keyword half —
      hit@10 0.087 to 0.130, MRR 0.048 to 0.077 — and says nothing about
      `document_embeddings`, where a query that paraphrases a chapter
      rather than quoting it should do better. The same A/B, the vector
      arm. (The backlog itself is done: 2,050 documents.)
- [ ] **What sections are worth to `ask`**, which was the second reason
      for the pass: a cheaper way into a long book than its chunks.
      `scripts/eval_ask.py` is the instrument.
- [ ] **Document-aware rerank input** (title + heading path + chunk) as
      a measured experiment (`docs/log.md`, Stage 2).

- [ ] **A heading is capped in a hit** (found 2026-10-03). Doc 9522, a
      patent read in two columns, has a heading of over 2,000 characters
      of repeated text, and it rides along with every search hit and
      `ask` passage of that document. Marker reads it again (queued the
      night of 2026-10-02); a cap on a hit's `heading` would keep one bad
      parse from swelling an answer (invariant 6). Not decided.

- [ ] **Facts as one more search list** (2026-10-03, from Graphiti's fact
      sentence and LightRAG's relation vectors): a full-text table over
      each edge's evidence, relation and its entities' names, a list in
      the fusion like the document field, one vote per document through
      `source_doc`. Vectors for it only if the text side earns them (one
      384-d vector per edge; size it first). Measured with
      `scripts/eval_retrieval.py` before and after.
- [ ] **A focus entity as a vote** (Graphiti's node distance): in an `ask`
      follow-up, the documents whose edges touch the entity in focus
      and its first hop get one vote in the fusion, a preference like
      `DOMAIN_PRIOR`, never a re-sort.
- [ ] **A faithfulness score for `ask`**: an open model run locally
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
      (`docs/log.md`, "The heal pass").
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

- [ ] **Support count as a signal** (2026-10-03, from Graphiti's episode
      count, LightRAG's summed weights, HippoRAG's source counts): a
      walk's first hop ordered by how many documents separately say each
      fact, and search's ties broken by it. prax writes one edge row per
      source, so the count is a GROUP BY. Nearly free.
- [ ] **A deterministic step before the model in entity resolution**
      (Graphiti's `dedup_helpers`): a normalised exact match with one
      candidate resolves; a short or low-entropy name goes to the model;
      otherwise 3-gram shingles, MinHash and a Jaccard of 0.9 or more
      resolve. About 50 lines; fewer calls of the local model. Merges
      stay recorded and undoable as now (`merged_by`, `merged_run`).

- [ ] **A gate on automatic merges by what they touch** (Utopia's
      `execution_gate`): a merge of the adjudicated tier waits on the
      Review page when it would make two values of a functional relation,
      touch an inferred edge, or touch an entity a saved page or answer
      cites. Three store queries, no table.
- [ ] **Precedents for the judge of a pair** (Utopia's adjudication): the
      person's earlier decisions on the Review page, with the reason they
      wrote, given to the model as examples, and its verdicts cached by
      pair and model. Utopia's identity rules (a version is not its
      family; a qualifier trimmed from the front is the same thing, a
      suffix added at the end another; a list is not its members; a
      phrase containing a name is not the name; a parent and its
      subsidiary are two) read against `ontology/sameness.yaml`; a
      change to it is measured again (`steps.adjudicate.platt`).
- [ ] **Evidence that knows where it stood**: the quote's character
      offsets in the text artifact and the `text_hash` it was read from,
      on an edge, so a re-parse shows which evidence moved (Utopia keeps
      offsets and the document's version).
- [ ] **Counted drops**: what extraction skips without queuing it (a
      reason, a count, an example, per document), beside the review
      queue (Utopia's `extraction_drops`).
- [ ] **Append-only by trigger** on what is a ledger already (merge
      stamps, page revisions, token uses): `BEFORE UPDATE … RAISE(ABORT)`
      in SQLite, as Utopia's audit table.

## The UI and the agent

- [ ] **A document's own neighbourhood** — a link from a document to
      what it is connected to (`niggles.txt`).
- [ ] **The plugin writes `.prax/graph.jsonl`** at session end, beside
      `.prax-project`, with `prax export --project` (the last part of
      T's design).
- [ ] **A procedural graph for the surfer** (later; the reference is in
      `docs/log.md`, "After the mathematics").
- [ ] **If the UI still feels slow from the MacBook** — the next suspect
      is named in `docs/log.md`, "The night of 2026-09-20".

## The backfill

- [ ] **The old zoetrope disk** — the hash inventory and the import that
      follows it (`docs/log.md`, Stage 1).
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

- [ ] **`store.retrieval` as a package.** It is 1,919 lines, under the
      2,000 that CLAUDE.md sets for a split. It does four jobs: query
      expansion, the search legs, fusion, and the vector files. Its tests
      switch behaviour through module flags (`retrieval.SENSES`,
      `retrieval.DOMAIN_PRIOR`, `DELTA_MERGE_AT`). Split into parts, a
      flag set on the package would no longer reach the part that reads
      it, and the tests would pass without testing. The split needs the
      flags read through one settings object first.
- [ ] **`store.repair` and `store.graph.context` in parts.** 1,697 and 954
      lines. Their tests reach private state (`repair._glyphs_seen`),
      which would move with the part that holds it.
- [ ] **Typed shapes.** 869 `dict[str, Any]` annotations and no
      `TypedDict`. The first ones worth writing are a search hit (read by
      six modules), `get_document`, a job row and the meta keys.
- [x] (2026-10-03) **The next strict batch of mypy: the whole package.**
      Under the batch's flags the package had 66 errors, not the 1,008
      feared. The 926 re-export errors come from `no_implicit_reexport`,
      which the batch never set; the store re-exports by design. The
      flags moved to `[tool.mypy]` itself (`docs/log.md`).
- [ ] **The remaining complex functions.** None is over 21 (the ruff
      ceiling). Worth splitting when next touched: `parsers.video.parse`
      (21), `answering.questions.briefing` (20), `graph.extraction.build_input`
      (19), `capture.routes.routes_for` (19).
- [ ] **Smaller duplication.** The UI's `esc` and `fmtTime` are copied in
      the extension. The summaries and sections helpers that the store
      calls up to could move into `prax.text`. (2026-10-03: the six tests
      that polled a job with their own loop use `tests.conftest.wait_job`,
      which fails with the job's row instead of running on.)
- **`text.compounds` asks the store** for the forms a word takes, and
  only the store uses it. It is retrieval, not text; it waits for
  `store/retrieval.py` (1,916 lines) to split at 2,000 as invariant 3
  says, and goes there.
- **Large modules:** `store/repair.py` 1,625, `host/up.py` 1,435. Neither
  is tangled. Both are long lists of the same shape (ailments, roles).

## Later / maybe

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
