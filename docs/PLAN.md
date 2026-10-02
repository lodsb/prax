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

1. **AI. A plan for the card** (below). Its first piece is in: an idle
   server whose card is lent waits for it (2a04f4c). Next the jobs view
   with "do it now", the load times, `GET /work/plan`, and `prax up`
   following the plan. "See who holds the card" and "ask the runtime what
   a text costs" join it as steps.
2. **AD2, the tidy-up** (below): the maths pack's tests beside it, and
   the maths pieces left in the core behind pack hooks.
3. **The library's backlogs, running** (2026-10-02, `docs/log.md`, "what
   was never extracted"). A backlog worker extracts the graph of about
   1,050 documents. Marker reads the 29 books on the night of
   2026-10-02, then the ~190 weaker maths candidates. The `equations`
   step checks every formula, and a broken formula that is an
   extraction slip is read again. Watched, not built.
4. **AA. Close Z** (below): the labeller's corrections, and the genre
   words in the document field, measured.
5. **The zoetrope disk and the NAS** (below): the 37 cut-short PDFs of
   `refetch-later.txt` (untracked) with the import of the old disk, when
   the NAS is reachable.
6. **Measured improvements** (below, "Retrieval and ask", "The graph"):
   the sections pass's vector arm and its worth to `ask`, document-aware
   rerank input, a compressed edge list, `confidence` as a number.
7. **The engineering leftovers** (below): `store.retrieval` and
   `store.repair` in parts, typed shapes, the next strict mypy batch.

Waiting on the user: **AE** (the distilled surfer), **AH** (the stack,
after the prototype settles), the move to the board ("Deployment shape"),
and pushing what is committed.

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

- [ ] **AA. Close Z.** The re-extraction against the new domains, the
      `computing` v2 review, the `society` rules and the relabel are done
      (`docs/log.md`, 2026-09-30 to 2026-10-01). Left:
      - the labeller's mistakes, corrected on the Review page's genre
        tab, go back into its training (`scripts/train_labeller.py`), as
        the user labels;
      - the genre words in the document field (`document_field`, beside
        "PDF document"), with `scripts/eval_retrieval.py` before and
        after, since it changes what search ranks.

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
- [ ] **The next strict batch of mypy.** `prax.text`, `writing`, `wall`,
      `ml`, `config`, `worker`, `host.schedule` and `steps.base` are
      strict. `prax.store` needs `__all__` or explicit re-exports first:
      926 of strict mode's 1,008 errors on 2026-09-30 were the store's
      re-exports.
- [ ] **The remaining complex functions.** None is over 21 (the ruff
      ceiling). Worth splitting when next touched: `parsers.video.parse`
      (21), `answering.questions.briefing` (20), `graph.extraction.build_input`
      (19), `capture.routes.routes_for` (19).
- [ ] **Smaller duplication.** The UI's `esc` and `fmtTime` are copied in
      the extension. Seven tests poll a job with their own loop. The
      summaries and sections helpers that the store calls up to could
      move into `prax.text`.
- **`text.compounds` asks the store** for the forms a word takes, and
  only the store uses it. It is retrieval, not text; it waits for
  `store/retrieval.py` (1,916 lines) to split at 2,000 as invariant 3
  says, and goes there.
- **Large modules:** `store/repair.py` 1,625, `host/up.py` 1,435. Neither
  is tangled. Both are long lists of the same shape (ailments, roles).

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
