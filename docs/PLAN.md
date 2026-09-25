# prax build plan

What is next. The record of what was done, with the reasoning and the
measurements under each night, is `docs/log.md`.

Work one stage per Claude Code session. Each stage ends green: tests
pass, `ruff` clean, and the stage's checklist fully ticked before moving
on. Decisions: `docs/rationale.md`. Source details: `docs/sources.md`.

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

- [ ] **A multilingual embedder of the same 384 dimensions** — the one
      step of the multilingual study that was not taken, and the only
      one left. `multilingual-e5-small` is already in
      `prax.embeddings.MODELS`, so it is `embeddings.model` in
      `prax.yaml` plus a re-embed into a new `vectors-<model>.usearch`;
      the old file keeps serving until the new one is complete, which
      makes it reversible. The cost is 1,115,976 chunk vectors at three
      to four times bge-small's compute — measure it on the desktop
      first. Why it was deferred: `docs/log.md`, "the ledger, the queue,
      the embedder, the labels".
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

