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
   file by a merge; nothing else lives outside SQLite. No Postgres, no
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
4. **Single writer.** The service process (the door) is the only writer.
   The recurring passes (parse, titles, extract, embed) are done by
   workers that fetch work and post results through the door
   (`prax.work` hands out and takes in, `prax.worker` does the work,
   `scripts/work.py` runs it) and never open the database; the door
   consumes its own drop folder and holds the vector delta indexes.
   Inside the door, reads use a connection per request thread and
   writes go one at a time behind the store's lock. The MCP server is
   a proxy of the door too (invariant 5). *Known deviation:* the one-off
   maintenance scripts (import, backfill, acronyms, resolution, typing
   rules, replay, dedupe) still open the file; WAL, a 30 s busy timeout
   and a retry with rollback in `store._serialized` are the safety net
   for those, not a mechanism to rely on.
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
   articles; a family module later), loaded and composed by
   `prax.ontology`. Names are unique across modules; a subtype passes
   wherever its parent is allowed; aliases map what a model says to the
   canonical name and never shadow a declared one; a module's
   `self_types` say what the document being extracted may be. The
   composed version (`core1+research5+studio1`) is what
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
`mentions` relation, both grown from the review queue's evidence.

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
  change the chunker, run `scripts/rechunk.py`. New media add a kind and a
  locator shape, never a new table for chunks (rationale R13).
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
  the token, the door's address, and the per-run switches.
- Timestamps are UTC ISO-8601 strings.
- Tests must not touch `data/`; use tmp_path fixtures and set
  `PRAX_DATA_DIR` before importing `prax.api`.
- Commit at the end of each green stage; do not commit failing tests.

## Roadmap

See `docs/PLAN.md`. Work one stage per session; write tests before wiring
the MCP layer. Background research and rationale: `docs/rationale.md`
(decisions) and `docs/research.md` (raw survey).
