# Rationale — decision records

Each entry: what was decided, why, what it costs, and when to revisit. The
raw survey these were drawn from is `research.md`; the invariants they
produce are in `CLAUDE.md`. Numbered so other docs can cite them (R1, R2…).

## R1. Compose around SQLite; do not adopt a monolith

**Decision.** Build from libraries (FTS5, sqlite-vec, FastAPI, FastMCP,
Docling, trafilatura) around one SQLite file rather than deploying Khoj,
RAGFlow, R2R, Cognee, or LightRAG-server.

**Why.** Every all-in-one drags in Postgres, Elasticsearch, Neo4j, or Docker
sprawl that fights three goals at once: Pi-class power budget, single-file
durability, and one maintainer. txtai was the closest SQLite-native option
but still centralizes the graph and search in its own index format.

**Cost.** Enrichment, importers, and the eval harness are written here, not
inherited.

**Revisit when.** Maintaining the enrichment pipeline becomes the bottleneck
(R7) and a heavier dependency is acceptable; then Cognee or LightRAG-server
give MCP plus graph out of the box.

## R2. Originals are content-addressed; the hash is of the original bytes

**Decision.** Every PDF or HTML snapshot is stored once at
`archive/<sha256[:2]>/<sha256>`. `documents.hash` is the sha256 of those
bytes. Extracted text is never the identity of a document.

**Why.** Dedupe across Zotero, the old zoetrope disk, and browser captures
falls out of hashing with no bookkeeping. Parsers change over time; the
original does not, so the identity must not depend on the parser. This
matches the layout ArchiveBox already uses.

**Cost.** Two captures of the same page on different days are two documents.
The browser-capture path handles that by URL (see `sources.md`).

## R3. Parsed text is its own artifact; chunks are disposable

**Decision.** `index_text` writes the parsed text to the archive
(content-addressed, `documents.text_hash`) and derives chunks from it.
`get` reads the artifact. Chunks and FTS rows can be deleted and rebuilt at
any time.

**Why.** The Stage 0 skeleton rebuilt a document by concatenating its
overlapping chunks and returned corrupted text for anything longer than one
chunk. Beyond that bug, re-chunking is inevitable: Docling brings
structure-aware chunks in Stage 1, and re-embedding in Stage 2 wants to
change chunk size. With the text held separately both are cheap batch jobs.

**Cost.** Text is stored twice (artifact plus FTS content). At this scale
that is megabytes.

## R4. Ingest is two steps: register, then index

**Decision.** `register(bytes, mime, …)` archives and inserts the row with
`parsed_at NULL`. `index_text(doc_id, text)` does the rest. Parsing is a
separate batch job that walks documents with `parsed_at IS NULL`.

**Why.** Docling on a Pi is minutes per PDF; the serving path must never
wait on it. Importers can therefore run on the Pi (hash and copy) while
parsing runs on the N100 or a laptop, and the single SQLite file plus
archive directory can be moved between them.

## R5. One door is `prax.store`; the MCP server imports it in-process

**Decision.** All writes go through the `prax.store` module. The FastAPI app
and the MCP server both call it directly. The MCP server carries no logic.

**Why.** The research doc recommended having the MCP server proxy the HTTP
API. Importing the store instead removes an HTTP hop and a running service
from the local Claude Code loop, which is where most use happens today.

**Cost.** When Claude Code spawns the stdio MCP server while uvicorn is also
running, two processes write the same file. WAL mode and Python's default
5 s busy timeout serialize them safely at personal scale; a process-wide
lock serializes threads within each process. This is a recorded deviation
from the single-writer invariant.

**Revisit when.** The service and MCP server run permanently on the same
host. Then the MCP server should proxy the HTTP door and the deviation
closes.

## R6. Hybrid retrieval: FTS5 + sqlite-vec fused with RRF, rerank optional

**Decision.** Keyword (BM25) and vector search run in parallel and are fused
by Reciprocal Rank Fusion. A cross-encoder rerank (bge-reranker-v2-m3) over
the fused top-N is optional and off until benchmarked on the Pi.

**Why.** RRF is rank-based, so it sidesteps incompatible score scales.
Published lifts for hybrid over either method alone are consistent (around
7% NDCG in one retail benchmark; recall@5 of 0.82 versus 0.59 dense-only
with reranking in a 23k-query benchmark). sqlite-vec keeps vectors in the
same file, so a deploy is a file copy.

**Cost.** Embedding is a batch job (R8); search results lag ingest until it
runs.

**Revisit when.** Vectors exceed about 1M. Then move only the vector layer
to LanceDB.

## R7. Graph: plain edge table, bi-temporal, evidence not truth

**Decision.** Edges live in one SQLite table with `confidence`,
`source_doc`, `ontology_version`, `valid_from`, `valid_to`, `ingested_at`.
Enrichment invalidates edges by setting `valid_to`; it never deletes.
Traversal is a recursive CTE, capped at two hops. The ontology is small and
versioned. Microsoft GraphRAG, Neo4j, and Graphiti are not used.

**Why.** Recursive CTEs are adequate below roughly 50k entities. GraphRAG
recomputes community summaries on update; Graphiti has the right temporal
model but needs a graph server. The bi-temporal columns are borrowed from
Graphiti so nightly enrichment is auditable and reversible. Small-scale
evidence shows LLM-built graphs fail at complement queries ("what is NOT
connected") and weighted propagation, so the graph is an entry-point
enhancer for retrieval, and those queries are explicit SQL tools.

**Revisit when.** Entities pass 50–100k or traversal gets slow. Then move
edges to Kùzu (embedded), not Neo4j.

## R8. Embeddings and parsing are batch jobs on the bigger box

**Decision.** bge-small-class, 384-dim, INT8 ONNX for embeddings. Docling
for PDF, trafilatura for HTML. All of it runs as scheduled batch work,
preferably on the N100 or a laptop; the Pi serves.

**Why.** Every latency figure in the survey came from x86; the Pi will be
slower. Keeping ML dependencies out of the serving path keeps resident RAM
under 1 GB. Model2Vec is the fallback if even bge-small is too slow.

**Cost.** The 384 dimension is baked into `chunks_vec`; changing models is
a migration.

## R9. Sources: Zotero first, browser tabs second, front-ends later

**Decision.** The existing Zotero library is the primary corpus and gets a
dedicated importer. Live capture is a small browser extension posting tabs
to the HTTP door. Karakeep or Linkwarden are optional later front-ends that
would feed the same inbox.

**Why.** Zotero already holds the curated PDFs with metadata that seeds the
graph (authors, tags, collections). A tab-capture extension is the shortest
path from "reading now" to "in the base" and needs nothing but the API.
Both write through `prax.store`, so a front-end can be swapped without
touching search. Importers are read-only on their source by invariant.

## R10. FastMCP standalone, pinned to the 4.x line

**Decision.** Depend on `fastmcp>=4,<5` rather than the official `mcp`
package's server API.

**Why.** FastMCP is the de-facto standard and supports both stdio and
streamable HTTP. Its versioning moves fast, so the pin is a major-version
range and the installed version is checked in `howto.md`.

## R11. Deployment: single file on an SSD, behind Tailscale

**Decision.** `data/` (database plus archive) lives on an external SSD on
the Pi, never the SD card. The API and MCP-over-HTTP are reachable only over
Tailscale. Backups are file copies (Litestream later).

**Why.** FTS and vector churn plus nightly jobs would wear an SD card; that
is the top reliability risk. The single-file store makes backup and
migration a copy.

## R12. Incremental schema and ontology: numbered migrations, versioned types

**Decision.** The schema is a sequence of numbered SQL migrations under
`src/prax/migrations/`, applied by `store.init_db` and recorded in SQLite's
`user_version`. The ontology is a versioned YAML file loaded by
`prax.ontology`; `store.link` validates every edge against it and stamps
the version. Anything a source knows that has no column yet goes into
`documents.meta` as JSON.

**Why.** The store is meant to outlive its first corpus: after papers come
personal, family and music material with different metadata and their own
entity and relation types. Three cheap mechanisms keep that incremental.
Migrations mean an old database upgrades in place instead of being
re-ingested. Versioned ontology stamps mean old edges stay interpretable
after the type set grows, and validation at the one door keeps misfits out
without a second code path. JSON `meta` means a new source ships without a
schema change; a key that turns out to be queried often is promoted to a
column (or a JSON index) by the next migration. A Stage 0 database with
`user_version = 0` is recognised and stamped, so nothing was thrown away.

**Cost.** Two files to touch for a schema change (migration plus the store
code that uses it). Domain/range constraints in the ontology are optional
so the file can stay small.

**Revisit when.** Migrations need data transformations Python must drive
(a rename of an entity type, a re-chunking); then add a Python hook per
migration number alongside the SQL. Not before.
