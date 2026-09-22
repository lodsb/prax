# Engineering pass, 2026-09-22

A review of interfaces, abstractions and the serving path's cost, on
`main` after the security audit (`d1c4457`). Each item names the module
and the commit that changed it, or says why it was left.

## Interfaces and abstractions

| # | Where | Finding | Status |
|---|---|---|---|
| 1 | `prax.api` | 75 handlers in one module of 2,000 lines, sectioned by comment | `a142a7f`: a package. `__init__` keeps the app, its lifespan, the middleware, the open endpoints and the UI's files; seven routers beside it, one per area (`capture`, `documents`, `graph`, `pages`, `ask`, `process`, `jobs`); `_base` holds what they share. No handler changed |
| 2 | `prax.worker`, `prax.inbox` | The worker walked its drop folders by the inbox's rules through the inbox's private names (`_settled`, `_sidecar`, `_is_sidecar_name`): a second copy of the walk, coupled to a door-side module | `6a523d1`: `prax.drop` holds the rules once (settled, sidecar, `failed/`), touches no store; the door's scan and the worker's upload both walk through it |
| 3 | the store's clock | Timestamps in two shapes: `…Z` from the store's SQL and most modules, `…+00:00` from the jobs table, the inbox, three meta stamps, a backup's manifest. Both parse; the strings do not sort as the moments do across the two | `a0ee398`: `store.now()` is the one clock, the `Z` shape; every store module, the inbox, the parse queue, the questions, the extraction stamp and the citations importer write through it. The supervisor keeps its own (a client, no store import). CLAUDE.md says so |
| 4 | `prax.questions` | A standing question's sources were read one query per document, twice (hash, title) | `6a523d1`: `store.text_hashes` and `document_titles`, one query each |
| 5 | reads outside the store | 26 raw `SELECT`s in modules other than the store (questions, review, inbox, resolution, pipeline, the citations importer, the work protocol, the parse queue, evaluation, routes). The invariant forbids writes outside the store, not reads; each of these couples a module to the schema | left, except the per-document loops above. A store read per query would be the cleaner surface; the cost is a store function per caller-specific question. Worth doing when a module's query breaks on a schema change |
| 6 | the store's module order | `base, documents, retrieval, graph, pages, jobs, summary, repair, maintain, backup`; a module imports only from the ones before it | checked mechanically: no violation |
| 7 | `prax.mcp_server` | imports `prax.client` only | checked: holds |
| 8 | `src/prax/ui/app.js` | 2,700 lines in one script, every view; `lib.js` holds the pure helpers | left. One ES module per view (`search`, `doc`, `graph`, `ask`, `pages`, `jobs`, `inbox`, `review`) under the same CSP is the shape; the change touches every view and the headless checks, a stage of its own |
| 9 | `tests/test_up.py` | `test_a_dying_process_is_restarted_with_backoff_and_all_stop_in_order` failed twice in full runs with `PermissionError` under the user's temp directory, and passed alone every time (three runs of the family together, four under load) | left, noted. A child process of the test holding a file the next test's cleanup removes is the shape; the traceback did not recur when the run was made to keep it |

## Runtime

Measured on the desktop against the live store (10,221 documents,
3.4 M chunks, 142 K entities, 243 K edges), warm unless said.

| # | Where | Before | After | How |
|---|---|---|---|---|
| 1 | `store.get_document(max_chars=0)` | the whole text artifact read from disk for `text_len` alone: the work hand-out did it for every reading request on every poll, a reading or extraction request did it, the document page's info line did it | the row alone | `6a523d1`: `documents.text_len` (migration 15) written by `index_text`; the `lengths` maintain pass fills it for the texts from before |
| 2 | `config.document()` | `prax.yaml` parsed on every setting read; a search asked it three or four things (milliseconds each here, tens on the board) | parsed when its bytes change; read every call, so an edit is seen at the next pass | `6a523d1` |
| 3 | `store.search`, the keyword side | the BM25 query joined chunks and documents for every matched row before the sort: 51 ms against 25 for the ranking alone on "feedback delay network" | rank inside `chunks_fts`, join three-times-depth survivors, drop the aside kinds after; 95 → 44 ms a search in-process, the same top ten on seven library queries | `ca2ef66` |
| 4 | `GET /entities?q=` | 11.7 s: the degree count's `src = e.id OR dst = e.id` scanned the edges per matching name | 46 ms: two indexed counts | `7fe2c46` |
| 5 | edges by `source_doc` | 80 ms a document, no index: the facts beside an ask's passages, a document's context, retiring a reading, the twin-documents check (20 s over 469 title groups; `GET /heal` 35 s) | indexed | `7fe2c46`, migration 16 |
| 6 | `select_for_extraction` with a scope | the OR added for a requested reading turned the source index into a scan | two indexed queries, merged | `6a523d1` |
| 7 | `prax.routes` | each step's model resolved once per route (eighteen YAML parses a dialog); the promote flag's state found by walking every promoted document | once per call; the document's own meta | `6a523d1` |
| 8 | cold searches | 1.2–3.7 s the first time a query's posting lists and vector pages come off the disk; 0.13–0.2 s warm | as before | the disk, with llama-server's model file owning the cache (howto 3d); not code |
| 9 | `GET /heal` | 35 s: twin-documents 20 s (item 5), unmapped-glyphs 13 s (a scan of every text for glyphs), unread-figures 4 s | twin-documents falls with the index; the glyph scan stays a scan | the Jobs page calls it once and keeps it fresh for a while (`HEALTH_FRESH`); the glyph check could keep its result per `text_hash` and read only what changed |
| 10 | `GET /questions` | 1.2 s: every standing question's fingerprint checked on the way (a search each) | as before | the check is the endpoint's point; a cached answer keyed on the change stamp would serve the list at once and check behind it |

## What to run on the live store

Migrations 15 and 16 apply when the door next starts. Then, once:

    prax maintain --only lengths      # text_len for the texts indexed before

## An incident during this pass, and what to do

While measuring the `source_doc` index, a scratch script attached the
live database to a temporary one and ran `DROP TABLE IF EXISTS edges`
unqualified, meaning the temporary database's copy. SQLite resolved the
name to the attached live database and dropped the graph's `edges`
table (03:48, 2026-09-22). The main database file had last been
checkpointed at 03:33. A copy of it taken at once,
`C:\prax-data\recover\prax-main-only.db`, holds the table intact:
242,982 rows, `quick_check` ok, one edge short of the count read at
03:40. The live file has since been checkpointed. The copy is the
source. `C:\prax-data\recover\restore_edges.py` puts the table and its
four indexes back in one write transaction, every name qualified. It
was not run: writing to the live store and stopping the door are the
person's to do.

    prax up --stop door
    .venv\Scripts\python C:\prax-data\recover\restore_edges.py
    prax up --start door

Until then every read of the graph fails (`/stats`, a document's facts,
an ask's context). The rule that would have kept this from happening:
a script that touches the live store attaches nothing and drops
nothing; measurements run on a copy.

## Follow-up, the same day

Items 9 and 10 of the runtime table and items 8 and 10 of the security
audit, done in one commit: the glyph check remembers each text it read
(`repair._glyphs_seen`, keyed by `text_hash`); the unread figures are a
partial index (migration 17), so their check is an index scan; the
standing questions' listing is kept while the store's change stamp
stands; a figure is served only under a document whose text references
it; llama-server bound beyond loopback is started with the model's
`api_key_env` as its `--api-key`, or the supervisor's log says it has
none.

