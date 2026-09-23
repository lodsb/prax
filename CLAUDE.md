# prax — personal research knowledge base

Successor to the "zoetrope" external-disk store. A self-hosted
knowledge base (PDFs and web snapshots) with hybrid search, a small
knowledge graph, and a Claude/MCP agent interface. It runs on a
Raspberry Pi or N100 home server reachable over a private network (the
LAN, any VPN; Tailscale is one example). Sources: a Zotero library,
browser tabs sent from an extension, a drop folder (`docs/sources.md`).

## Architecture invariants (do not violate without updating this file)

1. **SQLite is the canonical store.** One database file
   (`data/prax.db`). It holds FTS5 for BM25 and a plain `edges` table
   for the graph. `chunk_embeddings` is the record of which chunk has a
   vector from which model. `documents_fts` plus `document_embeddings`
   hold the document-level retrieval field (title, kind, summary).
   `pages` and `page_revisions` hold the wiki pages, which are
   documents too. WAL mode is always on. The vectors themselves live in
   two usearch HNSW files per model next to the database:
   `data/vectors-<model>.usearch` keyed by chunk id and
   `data/vectors-doc-<model>.usearch` keyed by document id. The serving
   path memory-maps them. Each has a small writable delta file beside
   it (`…delta.usearch`) that takes new vectors. A merge folds the delta
   into the main file: the merged file is built beside it outside the
   index lock and swapped in under it. Nothing else lives outside
   SQLite. No Postgres, no Neo4j, no server databases.
2. **Files are content-addressed.** Originals (PDFs, HTML snapshots)
   live at `data/archive/<sha256[:2]>/<sha256>`. The database stores
   metadata and the hash only. Never store blobs in SQLite. The document
   hash is the sha256 of the **original bytes**, never of extracted
   text. Parsed text is its own content-addressed artifact
   (`documents.text_hash`). Chunks are a disposable index derived from
   it and may be rebuilt at any time.
3. **One door.** All mutations go through `prax.store`, which the
   FastAPI app in `prax.api` uses. Capture inboxes, importers, cron jobs
   and the MCP server are all clients of that layer. No module writes to
   SQLite directly except `prax.store`. Ingest is two steps: `register`
   archives the original and inserts the row; `index_text` stores the
   text artifact, chunks it and indexes it in FTS. Parsers call
   `index_text`. `ingest_text` composes both for plain text. The store
   is a package of ten modules, in this order: `base`, `documents`,
   `retrieval`, `graph`, `pages`, `jobs`, `summary`, `repair`,
   `maintain`, `backup`. Its `__init__` re-exports every name, so a
   caller writes `store.<name>` and never imports a submodule. Inside
   the package, a module imports only from the ones before it in that
   order.
4. **Single writer.** The service process, the door, is the only
   writer. The recurring passes (parse, titles, extract, embed, and the
   likely tier of entity resolution) are done by workers. A worker
   fetches work and posts results through the door (`prax.work` hands
   out and takes in, `prax.worker` does the work, `scripts/work.py` runs
   it) and never opens the database. The door consumes its own drop
   folder and holds the vector delta indexes. Inside the door, reads
   use a connection per request thread and never take the store's lock
   (`store._reading`: a WAL snapshot and the busy-retry only). Writes
   go one at a time behind the lock (`store._serialized`). The one
   shared connection, which serves the change stamp, has a lock of its
   own. The index views and deltas are behind `_INDEX_LOCK` for the
   milliseconds a search or an add takes. The MCP server is a proxy of
   the door too (invariant 5). The maintenance passes are jobs on the
   door: `prax maintain`, `prax resolve`, `prax import citations`,
   `prax heal`, `prax backup`. The importers are clients (`prax import`,
   one request per document). *Known deviation:* the adjudicated tier
   of entity resolution (`resolve_entities.py --adjudicate`) and the
   measurement scripts (`eval_retrieval.py`, `compare_extractors.py`,
   `bench_extractor.py`, `make_zotero_fixture.py`) still open the file.
   WAL, a 30 s busy timeout and a retry with rollback in
   `store._serialized` are the safety net for those, not a mechanism to
   rely on.
5. **The MCP server is a thin proxy.** `prax.mcp_server` exposes tools
   that each make one HTTP call to the door (`prax.client`, `PRAX_DOOR`,
   `PRAX_TOKEN`). It imports no store module, opens no database and
   contains no business logic. The door's handlers are the contract.
6. **Agent-shaped endpoints.** `search` returns compact snippets and
   ids, never full documents. `get` fetches one record fully but accepts
   an offset and a character limit. `traverse` expands 1–2 hops. Keep
   responses small; Claude's context is the scarce resource.
7. **Pi-class hardware target.** No dependency that needs more than
   1 GB of resident RAM in the serving path. Parsing (Docling) and
   embedding run as batch jobs, never inline in a request.
8. **Graph edges are evidence, not truth.** Every edge carries
   `confidence` (EXTRACTED, INFERRED or AMBIGUOUS), `source_doc`,
   `ontology_version`, `producer` and `run` (which model, importer or
   person wrote it, in which batch or pass), and the bi-temporal
   columns `valid_from`, `valid_to` and `ingested_at`. Enrichment
   invalidates edges by setting `valid_to`; it never deletes them.
   Provenance is a column on the fact, never an edge in the graph.
   Upgrading a producer's work is `retire_run` plus a new pass.
9. **The ontology is small, versioned and modular.** Entity and
   relation types live in `ontology/`, one YAML module per domain.
   `core.yaml` holds the shared types (person, organization, document,
   place, event, work, concept, tool) and the relations every kind of
   document and organization shares (`authored_by`, `published_by`,
   `part_of`, `located_in`, `affiliated_with`, `developed_by`).
   `research.yaml` holds papers, methods, claims and pages.
   `studio.yaml` holds gear and its manuals, datasheets, schematics and
   articles. `craft.yaml` holds what making shares, with `kitchen.yaml`
   (recipes) and `workshop.yaml` (builds) on top of it. A family module
   comes later. `prax.ontology` loads and composes the modules. Names
   are unique across modules. A subtype passes wherever its parent is
   allowed. Aliases map what a model says to the canonical name and
   never shadow a declared one. A module's `self_types` say what the
   document being extracted may be. The composed version
   (`core3+craft1+kitchen2+research8+studio4+workshop2`) is what
   `store.link` validates against and stamps on every edge. A document
   carries its domain set in `meta.domains`, the modules it is read
   against; none means every module. It is extracted against that
   subset and stamped with the subset's version (`core1+family1`).
   Growing a module is its version bump. Renaming or removing a type is
   a data migration. Extraction emits triples only against the current
   version; misfits go to a review queue, not into the graph.
10. **Importers never write to their source.** The Zotero importer
    works on a copy of `zotero.sqlite` opened read-only. Nothing in prax
    modifies a Zotero library, a browser profile, or the old zoetrope
    disk.

## Decision thresholds (revisit design only past these)

- Vector query latency or index size on the serving host becomes a
  problem. The usearch file is memory-mapped; f16 is 784 MB at 855 K
  vectors. The answer is the int8 index first (`PRAX_VEC_DTYPE=i8`,
  half the size, recall 0.93), then LanceDB. Everything else stays.
- Entities pass about 50–100k, or recursive-CTE traversal turns slow.
  The answer is Kùzu (embedded) for the edges, not Neo4j.
- SQLite write contention across capture sources. The answer is the
  single writer queue, not a new database.

The full reasoning behind each decision is in `docs/rationale.md`. The
system as built, with the life of a document and of a query and a
"where to touch what" table, is in `docs/architecture.md`.

## Retrieval design

Search is hybrid. FTS5 (BM25) and vector search run over chunks, and
BM25 and vector search run over the document field (what a document
*is*: title, kind, summary). The lists are fused at document level with
Reciprocal Rank Fusion. `doctype` filters by document type. A query
token the library defines as an acronym is expanded to its phrase on
the keyword side; the `acronyms` table is built from "phrase (ACRONYM)"
in the texts. The query's rare acronym-shaped terms get a rank list of
their own (measured in `docs/eval/retrieval-acronyms-2026-09-12.md`).
An optional cross-encoder rerank (bge-reranker-v2-m3) runs over the
fused top-N; benchmark it on the target hardware before enabling it by
default. Graph traversal expands entry-point hits 1–2 hops. Complement
queries ("what is NOT connected") and weighted multi-hop scoring are
explicit SQL tools, never retrieval. User-supplied search strings are
never passed to FTS5 MATCH raw; `prax.store` builds the match
expression.

`ask` (`prax.ask`) is the same search plus generation. A bounded bundle
(one passage per document, plus the graph's facts about it) goes to a
model chosen per host (`PRAX_ASK`: a local GGUF, Claude, or none, in
which case the caller's model answers). Answers cite passage numbers,
which are resolved to chunk ids, and are kept on pages. A synthesis
page `synthesizes` its sources and may argue claims (ontology v4). v5
adds organizations (affiliation, funding, who built a tool) and the
weak `mentions` relation, both grown from the review queue's evidence.
With steps the model surfs before it answers (`prax.surf`): a bounded
loop of the door's own reads (search again, read on or into a document
by words, facts, walk, similar, drop) under a per-step grammar for
local models. Two budgets, steps and tokens of reading, are clamped to
the model's context. The trail is streamed and kept with the answer.
Nothing in the loop writes. What that model may and may not do is the
reference `docs/ask.md`.

## Conventions

- Python 3.11 or later, `pyproject.toml` with uv or pip, `pytest` for
  tests. The dev environment is a `.venv` in the repo root
  (`docs/howto.md`).
- Type hints everywhere; `ruff` clean.
- Schema changes are numbered migrations in `src/prax/migrations/`
  (`NNNN_name.sql`, contiguous), applied by `store.init_db` and tracked
  in `PRAGMA user_version`. Never edit an applied migration; add a new
  one. Document-level extensibility lives in `documents.meta` (JSON).
  A new source adds keys there before it earns a column.
- Chunks are addressable regions: `kind`, `locator` (character range
  plus page; `chunk.text == artifact[start:end]` always), `heading`
  path, table `data`. Chunking lives in `prax.chunking`, and chunks are
  disposable: change the chunker, then run `prax maintain --rechunk`.
  New media add a kind and a locator shape, never a new table for
  chunks (rationale R13), and only when the region is stored or located
  differently. What a figure is *of* belongs in `data`, not in a kind
  of its own.
- A figure is a line in the text, `![caption](figure:<sha256>)`, and a
  `figure` chunk with the reference, caption and readings in `data`.
  Its bytes are served out of the original by hash, never stored again.
  The one exception is a picture no object in the original holds. A
  scanned page is one (marker's crop, `figures.FILED` in its caption).
  A figure drawn with vector paths is another, and is not an image
  object at all: the `figure-crops` reading renders the region above its
  caption (`figures.add_crops`). Both are filed as their own
  content-addressed artifact. A parse inlines the picture as a data URL
  and the door files it before indexing (`figures.file_inline`).
- A display equation alone on its line is a `formula` chunk with its
  LaTeX, the number the prose refers to it by, and any readings in
  `data`. Inline maths stays in the text chunk around it.
- What a captured page carries that is not the document is set aside
  like a reference. Its comment section is one `comment` chunk, from
  the `## Comments` heading the HTML parser writes at the end of a page
  of some size. Its advertising is an `ad` chunk per run
  (`prax.furniture`: a sponsor's mark with an offer beside it, reaching
  over the pieces that name the same brand). Both stay in the artifact,
  out of the vectors, out of a search unless asked for by kind, out of
  what an extraction reads, and folded in the document view.
- A recipe's ingredient list is one `ingredients` chunk, from its
  "Zutaten" or "Ingredients" heading to the last of its lists, with the
  servings and every line's amount, unit and note in `data`
  (`prax.ingredients`). Not set aside: an ingredient is what a search
  for one should find. The line as written is always kept, so a later
  pass can answer "the same for six people".
- An entry of a reference list (under a References/Bibliography
  heading) is a `reference` chunk. Its `data` holds what it names
  (number, surnames, year, title, a printed id; `prax.references`) and,
  once the `references` pass of `prax maintain` has matched it, the
  library document it cites (`data.cited`). The links are kept on the
  document as `meta.references.links`, so a rechunk puts them back.
  Reference chunks are never embedded and stay out of a search unless
  asked for by kind (`store.ASIDE_KINDS`).
- An ask block of a page is a standing question between `<!-- prax:ask
  id=q1 "…" -->` and `<!-- /prax:ask id=q1 -->`. `prax.blocks` is the
  grammar: the tail's hash of the door's text, `held` when a hand was
  in the interior, `prax:keep` regions carried over. The block is one
  `ask` chunk from head to tail, set aside like a reference, so an
  answer is never its own evidence. The pass fills interiors by id
  (`store.fill_blocks`) and touches nothing outside the markers. A
  page's `[title](#doc/N)` links are its `annotates` edges, retired
  when the link goes.
- A video is a capture like a page (`prax.parsers.video`): the
  extension's transcript-with-frames HTML, `meta.video` for the player
  the UI shows, `meta.parser` naming the parser. The frames are
  figures, read by the vision pass like any. A re-index keeps the
  chunks whose text did not change, and their vectors.
- Embeddings are 384-dimensional (bge-small-class ONNX). Vectors are
  keyed by chunk id in the usearch file. Changing the model means
  re-embedding into a new file. Another dimension means a new file and
  `VEC_DIM`.
- What a host chooses is configuration, not code. It lives in
  `prax.yaml` in the data directory, read through `prax.models` for the
  models and steps (`resolve(step)`, `runtime(spec)`) and `prax.config`
  for the rest (`setting`, `number`, `words`, by dotted path:
  `embeddings`, `vectors`, `rerank`, `parse`, `citations`, `door`,
  `ontology`, `paths`). A module never invents an environment variable
  of its own. It names a setting, and the matching `PRAX_*` variable
  overrides it for one run. Environment-only: the data directory, the
  config path, the token, the door's address, and the per-run switches.
- What a host *runs* is configuration too. `run:` names which of
  prax's roles (llama-server, a reranker, the door, the worker) this
  host keeps alive, and `prax up` (`prax.up`) keeps them so. The
  operating system's only job is one login entry that starts `prax up`
  (`prax.autostart`). On a desktop the tray icon of `prax.tray` goes
  with it: an optional extra and a client of the supervisor's status
  and command files, never a second supervisor. The timed passes are
  the door's own clock (`schedule:`, `prax.schedule`) and the worker's
  `nightly` hour, never a cron or scheduler entry per pass. No shell
  script derives the process model a second time.
- A document says what language it is in: `meta.lang`, an ISO 639-1
  code written by `prax.language` when the text is indexed, and filled
  in for older documents by the `languages` pass of `prax maintain`.
  The detector is `py3langid` narrowed to the languages the host
  expects (`parse.languages`), with a stopword count as the fallback
  where it is not installed. It says nothing rather than guess, so a
  missing `lang` is always allowed.
- Timestamps are UTC ISO-8601 strings to the second with `Z`, from
  `store.now()` (`_NOW` in SQL). One shape, so they sort as moments.
- Tests must not touch `data/`. Use tmp_path fixtures and set
  `PRAX_DATA_DIR` before importing `prax.api`.
- Commit at the end of each green stage; do not commit failing tests.

## Visual design

The mark, the theme tokens and the UI's ornament rules are in
`docs/design/BRIEF.md` (the brief), `docs/design/assets/` (the mark in
three reductions, the static per-theme files), and `snippets/` and
`css/` (the shapes the UI copies). Themes are four custom properties,
ground, tone, key and colour, that switch the page and the inlined
logo together. Do not add a per-theme logo asset. Ornament may cost
space, never a click: nothing decorative is a control. The UI keeps the
mark inline in `src/prax/ui/index.html`: the medium file at masthead
size, the solid one as favicon. The six themes are `[data-prax-theme]`
blocks in `style.css`. KaTeX is vendored under
`src/prax/ui/vendor/katex/` (MIT), for the mathematics and nothing
else. The two faces are vendored under `src/prax/ui/vendor/fonts/`
(OFL).

## Roadmap

See `docs/PLAN.md`. Work one stage per session. Write tests before
wiring the MCP layer. Background research and rationale:
`docs/rationale.md` (decisions) and `docs/research.md` (raw survey).
