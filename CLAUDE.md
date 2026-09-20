# prax — personal research knowledge base

Successor to the "zoetrope" external-disk store. A self-hosted knowledge base
(PDFs + web snapshots) with hybrid search, a small knowledge graph, and a
Claude/MCP agent interface. Runs on a Raspberry Pi / N100 home server
reachable over a private network (the LAN, any VPN; Tailscale is one
example). Sources: a Zotero library, browser tabs sent from an extension, a
drop folder (`docs/sources.md`).

## Architecture invariants (do not violate without updating this file)

1. **SQLite is the canonical store.** One database file (`data/prax.db`):
   FTS5 for BM25, a plain `edges` table for the graph, `chunk_embeddings`
   as the record of which chunk has a vector from which model, and
   `documents_fts` plus `document_embeddings` for the document-level
   retrieval field (title, kind, summary), `pages` and `page_revisions`
   for the wiki pages that are documents too. WAL mode always on. Vectors
   themselves live in two usearch HNSW files per model next to the
   database (`data/vectors-<model>.usearch` keyed by chunk id,
   `data/vectors-doc-<model>.usearch` keyed by document id), memory-mapped
   by the serving path, each with a small writable delta file beside it
   (`…delta.usearch`) that takes new vectors and is folded into the main
   file by a merge (built beside it outside the index lock, swapped in
   under it); nothing else lives outside SQLite. No Postgres, no
   Neo4j, no server databases.
2. **Files are content-addressed.** Originals (PDFs, HTML snapshots) live at
   `data/archive/<sha256[:2]>/<sha256>`. The DB stores metadata + hash only.
   Never store blobs in SQLite. The document hash is the sha256 of the
   **original bytes**, never of extracted text. Parsed text is its own
   content-addressed artifact (`documents.text_hash`); chunks are a
   disposable index derived from it and may be rebuilt at any time.
3. **One door.** All mutations go through `prax.store` (used by the FastAPI
   app in `prax.api`). Capture inboxes, importers, cron jobs, and the MCP
   server are all clients of that layer. No module writes to SQLite directly
   except `prax.store`. Ingest is two steps: `register` (archive the original,
   insert the row) and `index_text` (store the text artifact, chunk, FTS).
   Parsers call `index_text`; `ingest_text` composes both for plain text.
   The store is a package (`base`, `documents`, `retrieval`, `graph`,
   `pages`, `jobs`, `summary`, `repair`, `maintain`, `backup`) whose `__init__`
   re-exports every name, so
   a caller writes `store.<name>` and never imports a submodule; inside it,
   a module imports only from the ones before it in that order.
4. **Single writer.** The service process (the door) is the only writer.
   The recurring passes (parse, titles, extract, embed, and the likely
   tier of entity resolution) are done by
   workers that fetch work and post results through the door
   (`prax.work` hands out and takes in, `prax.worker` does the work,
   `scripts/work.py` runs it) and never open the database; the door
   consumes its own drop folder and holds the vector delta indexes.
   Inside the door, reads use a connection per request thread and
   never take the store's lock (`store._reading`: a WAL snapshot and
   the busy-retry only); writes go one at a time behind it
   (`store._serialized`). The one shared connection (the change stamp)
   has a lock of its own; the index views and deltas are behind
   `_INDEX_LOCK` for the milliseconds a search or an add takes. The MCP server is
   a proxy of the door too (invariant 5). The maintenance passes are
   jobs on the door (`prax maintain`, `prax resolve`, `prax import
   citations`, `prax heal`, `prax backup`); the importers are clients
   (`prax import`, one request per document). *Known deviation:* the
   adjudicated tier of entity resolution (`resolve_entities.py
   --adjudicate`) and the measurement scripts (`eval_retrieval.py`,
   `compare_extractors.py`, `bench_extractor.py`,
   `make_zotero_fixture.py`) still open the file;
   WAL, a 30 s busy timeout and a retry with rollback in
   `store._serialized` are the safety net for those, not a mechanism
   to rely on.
5. **The MCP server is a thin proxy.** `prax.mcp_server` exposes tools
   that each make one HTTP call to the door (`prax.client`, `PRAX_DOOR`,
   `PRAX_TOKEN`); it imports no store module, opens no database and
   contains no business logic. The door's handlers are the contract.
6. **Agent-shaped endpoints.** `search` returns compact snippets + ids, never
   full documents. `get` fetches one record fully but accepts an offset and a
   character limit. `traverse` expands 1–2 hops. Keep responses small;
   Claude's context is the scarce resource.
7. **Pi-class hardware target.** No dependency that requires >1 GB resident
   RAM in the serving path. Parsing (Docling) and embedding run as batch jobs,
   never inline in a request.
8. **Graph edges are evidence, not truth.** Every edge carries
   `confidence` (EXTRACTED | INFERRED | AMBIGUOUS), `source_doc`,
   `ontology_version`, `producer` and `run` (which model, importer or
   person wrote it, in which batch or pass), and bi-temporal columns
   (`valid_from`, `valid_to`, `ingested_at`). Enrichment invalidates edges
   (sets `valid_to`); it never deletes them. Provenance is a column on the
   fact, never an edge in the graph: upgrading a producer's work is
   `retire_run` plus a new pass.
9. **Ontology is small, versioned and modular.** Entity/relation types
   live in `ontology/`, one YAML module per domain (`core.yaml` for the
   shared types: person, organization, document, place, event, work,
   concept, tool; `research.yaml` for papers, methods, claims and pages;
   `studio.yaml` for gear and its manuals, datasheets, schematics and
   articles; `craft.yaml` for what making shares, with `kitchen.yaml`
   (recipes) and `workshop.yaml` (builds) on top of it; a family module
   later; `core.yaml` also holds the relations every kind of document
   and organization shares: `authored_by`, `published_by`, `part_of`,
   `located_in`, `affiliated_with`, `developed_by`), loaded and composed by
   `prax.ontology`. Names are unique across modules; a subtype passes
   wherever its parent is allowed; aliases map what a model says to the
   canonical name and never shadow a declared one; a module's
   `self_types` say what the document being extracted may be. The
   composed version (`core2+craft1+kitchen2+research7+studio4+workshop2`)
   is what
   `store.link` validates against and stamps on every edge. A document
   carries its domain set (`meta.domains`: which modules it is read
   against; none means every module) and is extracted against that
   subset, stamped with the subset's version (`core1+family1`). Growing a
   module is its version bump; renaming or removing a type is a data
   migration. Extraction emits triples only against the current version;
   misfits go to a review queue, not into the graph.
10. **Importers never write to their source.** The Zotero importer works on
    a copy of `zotero.sqlite` opened read-only. Nothing in prax modifies a
    Zotero library, a browser profile, or the old zoetrope disk.

## Decision thresholds (revisit design only past these)

- Vector query latency or index size on the serving host becomes a
  problem (the usearch file is memory-mapped: f16 is 784 MB at 855 K
  vectors) → int8 index (`PRAX_VEC_DTYPE=i8`, half the size, recall 0.93),
  then LanceDB; everything else stays.
- Entities > ~50–100k or slow recursive-CTE traversal → move edges to Kùzu
  (embedded); not Neo4j.
- SQLite write contention across capture sources → the answer is the single
  writer queue, not a new database.

Full reasoning behind each decision: `docs/rationale.md`. The system as
built, with the life of a document and of a query and a "where to touch
what" table: `docs/architecture.md`.

## Retrieval design

Hybrid: FTS5 (BM25) and vector search over chunks, plus BM25 and vector
search over the document field (what a document *is*: title, kind,
summary), fused at document level with Reciprocal Rank Fusion; `doctype`
filters by document type. A query token the library defines as an acronym
(`acronyms` table, built from "phrase (ACRONYM)" in the texts) is expanded
to its phrase on the keyword side, and the query's rare acronym-shaped
terms get a rank list of their own (measured in
`docs/eval/retrieval-acronyms-2026-09-12.md`). Optional cross-encoder rerank (bge-reranker-v2-m3) over fused
top-N — benchmark on target hardware before enabling by default. Graph
traversal expands entry-point hits 1–2 hops. Complement queries ("what is NOT
connected") and weighted multi-hop scoring are explicit SQL tools, never
retrieval. User-supplied search strings are never passed to FTS5 MATCH raw;
`prax.store` builds the match expression. `ask` (`prax.ask`) is the same
search plus generation: a bounded bundle (one passage per document,
the graph's facts about it) to a model chosen per host (`PRAX_ASK`:
local GGUF, Claude, or none, where the caller's model answers); answers
cite passage numbers resolved to chunk ids, and are kept on pages; a
synthesis page `synthesizes` its sources and may argue claims (ontology v4);
v5 adds organizations (affiliation, funding, who built a tool) and the weak
`mentions` relation, both grown from the review queue's evidence. With
steps the model surfs before it answers (`prax.surf`): a bounded loop of
the door's own reads — search again, read on or into a document (by
words), facts, walk, similar, drop — under a per-step grammar for local
models, two budgets (steps, tokens of reading) clamped to the model's
context, the trail streamed and kept with the answer; nothing in the
loop writes. What that model may and may not do, as a reference:
`docs/ask.md`.

## Conventions

- Python ≥3.11, `pyproject.toml` with uv/pip, `pytest` for tests. Dev
  environment is a `.venv` in the repo root (`docs/howto.md`).
- Type hints everywhere; `ruff` clean.
- Schema changes are numbered migrations in `src/prax/migrations/`
  (`NNNN_name.sql`, contiguous), applied by `store.init_db` and tracked in
  `PRAGMA user_version`. Never edit an applied migration; add a new one.
  Document-level extensibility lives in `documents.meta` (JSON): new
  sources add keys there before they earn a column.
- Chunks are addressable regions: `kind`, `locator` (character range plus
  page; `chunk.text == artifact[start:end]` always), `heading` path, table
  `data`. Chunking lives in `prax.chunking` and chunks are disposable:
  change the chunker, run `prax maintain --rechunk`. New media add a kind and a
  locator shape, never a new table for chunks (rationale R13), and only
  when the region is stored or located differently — what a figure is
  *of* belongs in `data`, not in a kind of its own. A figure
  is a line in the text, `![caption](figure:<sha256>)`, and a `figure`
  chunk with the reference, caption and readings in `data`; its bytes
  are served out of the original by hash, never stored again — except a
  picture of a scanned page, which no object in the original holds:
  marker's crop of it is filed as its own content-addressed artifact
  (`figures.FILED` in its caption; a parse inlines it as a data URL, the
  door files it before indexing, `figures.file_inline`). A display
  equation alone on its line is a `formula` chunk with its LaTeX, the
  number the prose refers to it by and any readings in `data`; inline
  maths stays in the text chunk around it. An entry of a reference list
  (under a References/Bibliography heading) is a `reference` chunk with
  what it names in `data` (number, surnames, year, title, a printed id;
  `prax.references`) and, once the `references` pass of `prax maintain`
  has matched it, the library document it cites (`data.cited`, kept on
  the document as `meta.references.links` so a rechunk puts it back);
  reference chunks are never embedded and stay out of a search unless
  asked for by kind (`store.ASIDE_KINDS`). A video is a capture like a
  page (`prax.parsers.video`: the extension's transcript-with-frames HTML,
  `meta.video` for the player the UI shows, `meta.parser` naming the
  parser; the frames are figures, read by the vision pass like any). A re-index
  keeps the chunks whose text did not change, and their vectors.
- Embeddings: 384-dim (bge-small-class ONNX). Vectors are keyed by chunk
  id in the usearch file; changing the model means re-embedding into a new
  file, another dimension means a new file and `VEC_DIM`.
- What a host chooses is configuration, not code: `prax.yaml` in the
  data directory, read through `prax.models` for the models and steps
  (`resolve(step)`, `runtime(spec)`) and `prax.config` for the rest
  (`setting`, `number`, `words`, by dotted path: `embeddings`,
  `vectors`, `rerank`, `parse`, `citations`, `door`, `ontology`,
  `paths`). A module never invents an environment variable of its own;
  it names a setting, and the matching `PRAX_*` variable overrides it
  for one run. Environment-only: the data directory, the config path,
  the token, the door's address, and the per-run switches. What a host
  *runs* is configuration too: `run:` names which of prax's roles
  (llama-server, a reranker, the door, the worker) this host keeps
  alive and `prax up` (`prax.up`) keeps them so — the operating
  system's only job is one login entry that starts `prax up`
  (`prax.autostart`); the timed passes are the door's own clock
  (`schedule:`, `prax.schedule`) and the worker's `nightly` hour, never
  a cron or scheduler entry per pass. No shell script derives the
  process model a second time.
- Timestamps are UTC ISO-8601 strings.
- Tests must not touch `data/`; use tmp_path fixtures and set
  `PRAX_DATA_DIR` before importing `prax.api`.
- Commit at the end of each green stage; do not commit failing tests.

## Visual design

The mark, the theme tokens and the UI's ornament rules: `docs/design/BRIEF.md`
(the brief), `docs/design/assets/` (the mark in three reductions, the
static per-theme files), `snippets/` and `css/` (the shapes the UI copies).
Themes are four custom properties — ground, tone, key, colour — that switch
the page and the inlined logo together; do not add a per-theme logo asset.
Ornament may cost space, never a click: nothing decorative is a control.
The UI keeps the mark inline in `src/prax/ui/index.html` (the medium file at
masthead size, the solid one as favicon), the six themes in `style.css` as
`[data-prax-theme]` blocks, KaTeX vendored under `src/prax/ui/vendor/katex/`
(MIT) for the mathematics and nothing else, and the two faces vendored under
`src/prax/ui/vendor/fonts/` (OFL).

## Roadmap

See `docs/PLAN.md`. Work one stage per session; write tests before wiring
the MCP layer. Background research and rationale: `docs/rationale.md`
(decisions) and `docs/research.md` (raw survey).
