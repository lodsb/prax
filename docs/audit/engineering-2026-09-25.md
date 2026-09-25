# Engineering pass, 2026-09-25

A review after a week that added the language passes, the identity
change, the traverse repair and the embedder switch. The shape follows
`engineering-2026-09-22.md`: what was checked, what was found, and either
the commit that changed it or why it was left.

Nothing here is urgent. The house is tidy — **5 TODO/FIXME-class markers
in about 25,000 lines**, 73 test files and 21,090 lines of tests — so the
findings are structural rather than rot.

## What holds

| | Checked | Result |
|---|---|---|
| invariant 3 | the store's module order, mechanically | no violation |
| invariant 5 | what `prax.mcp_server` imports | `prax.client`, nothing else |
| invariant 8 | edges invalidated, never deleted | the container repair ended 851 and deleted none |

## Interfaces and abstractions

| # | Where | Finding | Status |
|---|---|---|---|
| 1 | `prax.work`, `prax.worker` | **The work protocol is three parallel dispatchers.** `work.hand_out` is 363 lines, `work.take_in` 295, `worker.run_once` 267 — each a long `if step == …` chain over the same step names, in two modules. Adding `sections` this week meant editing all three, in the same order, with the same shape; the `sections` batch cap lives in one, its lease in another, its result handling in a third. This is the largest structural finding and the one that will keep costing | left, named. The shape is a `Step` with `hand_out`, `take_in` and `do`, registered once, so a new pass is one object rather than three edits. It touches every step, so it is a stage of its own rather than a corner of another change |
| 2 | `prax.store` | **The store's modules are now bigger than `api` was when the last pass split it.** `documents.py` 2,739 lines, `graph.py` 2,044, `repair.py` 1,460, `retrieval.py` 1,387 — against the 2,000 that made `api` a package on 2026-09-22. `graph.py` grew ~150 lines this week | left. The same medicine applies (`graph` splits along entities / edges / labels / traversal), but the module order is an invariant and a split has to keep it, so it wants doing deliberately |
| 3 | reads outside the store | **26 on 2026-09-22, 38 now**, in 13 modules — the item the last pass left as "worth doing when a module's query breaks on a schema change". It is growing rather than shrinking, which is the signal that the answer was wrong | left, re-noted with the number, so the next pass can see the trend rather than the count |
| 4 | `CLAUDE.md`, invariant 4 | The declared deviations name five scripts that open the database. There are **six**: `eval_references.py` joined them without the file noticing | a one-line fix to the invariant, below |
| 5 | `prax.models`, `prax.sections` | **Three names for one idea, added this week.** `models.fits` (characters and bytes), `models.fits_for` (the same, measured against a server), `sections.read_for` (a two-line wrapper holding the step's defaults). The wrapper earns its place — the defaults belong to the pass, not to `models` — but the naming does not say so | left, named. `read_for` would read better as `sections.budget()` |
| 6 | `prax.hostinfo` | `room(need_mb)` was written as the general answer to "can this run" and demoted to the GGUF case within the same day, once it was clear that Claude wants a key and money and a server wants a slot. It is in the right place now (`models.ready` asks it) but the name still promises generality | left. It is the local-card answer and could say so |

## Runtime

Measured against the live store mid-re-embed (1.1 M chunk vectors,
3.5 M chunks).

| # | Where | Finding |
|---|---|---|
| 1 | `traverse` | **fixed this week.** 3,439 KB → 76 on a well-connected entity at two hops, 319 → 71 at one. Bounded at 70–78 KB whatever it is asked about, which is the property invariant 6 wants: a bounded worst case rather than a small average (`docs/eval/traverse-neighbourhood-2026-09-25.md`) |
| 2 | `store.pending_embeddings` | **Three full passes over `chunks` per hand-out.** `_all_chunks_embedded` does two `COUNT(*)`s over a `LEFT JOIN`, then the hand-out's own query plans as `SCAN c`. Measured at the halfway mark of the re-embed: **759 ms walking 1,198,689 already-embedded rows** to find the next 2,000, and worse the further it gets |
| 3 | `work.take_in` | **Every batch rewrites the whole delta index.** `store.save_vectors(model)` runs after *each* POST: 31 MB at the halfway mark, headed for about 800 MB before a merge, so a 200-vector batch rewrites the entire file. This is the 10–17 s the door logs as a slow POST, and it is the dominant term |
| 4 | the same shape, elsewhere | `pending_document_embeddings` is the identical `LEFT JOIN … WHERE model != ? ORDER BY rowid DESC`. Same quadratic, 10,000 rows rather than 3.5 M, so it does not bite — but it is the same bug waiting for a bigger library |
| 5 | the marshalling | **Not the problem, though it looked like it.** A 384-float vector is 8.4 KB of JSON, forty times the text that produced it — and `[float(x) for x in v]`, `json.dumps`, `json.loads` and the rebuild together cost 106 ms against 1,442 ms of embedding. Seven per cent, which is the whole gap between 355 chunks/s raw and 331 |

Items 2 and 3 are the same mistake in two places: **work proportional to
what is already finished, repeated per batch.** That is why the observed
rate falls as a run proceeds (331 chunks/s of capacity delivering 98
early and 67 at the halfway mark) rather than sitting at a bad constant,
and it is how the two were told apart from a latency cost, which would
have been flat.

## What this pass did not look at

The UI's `app.js` (2,700 lines, one script, every view) — carried over
unexamined from 2026-09-22, where it was left for the same reason: the
change touches every view and the headless checks.
