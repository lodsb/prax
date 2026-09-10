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

## R6. Hybrid retrieval: FTS5 + a usearch index fused with RRF, rerank optional

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

*Measured (2026-09-07).* Embedding cost is dominated by sequence length:
bge-small (33M parameters) needs about 20 GFLOP for a 330-token chunk, so
a 12-core desktop CPU manages 20 chunks/s with the int8 export and the
GTX 1070 through DirectML 53 chunks/s with fp32 (batch 64; batch 128 is
slower). The library's 855 K chunks (median 180 tokens, 3% truncated at
512) are therefore a 4-5 hour one-off batch. fastembed was dropped: its
default bge-small file is fp16, which onnxruntime emulates on CPU at
3 chunks/s. The store talks to onnxruntime and ``tokenizers`` directly,
with files from the Hugging Face hub. The vec0 table carries ``kind`` as a
metadata column so a KNN query can be filtered to tables or figures
without a post-filter.

*Measured on the library (2026-09-08, `docs/eval/retrieval-library-*`).*
Fusion has to happen per document, not per chunk: chunk-level RRF scored
below FTS alone (MRR 0.80 vs 0.82); document-level RRF with each side's
best chunk rank gives hit@1 0.77 and MRR 0.83 on 62 queries against the
full store, above FTS (0.82) and vectors alone (0.81). Depth 100 per side.
Latency is the binding constraint: sqlite-vec scans every vector, and at
855 K vectors a KNN query takes 4.2 s warm (int8 1.8 s, binary with fp32
rescoring 2.7 s), so hybrid search took 5 s end to end against 0.3 s for
FTS. A memory-mapped usearch HNSW index over the same vectors answers in
46 ms at recall@10 0.98 (f16, 784 MB file) or 18 ms at 0.93 (int8,
456 MB). The threshold below was set at 1 M vectors; the wall arrived at
0.86 M.

*Decision (2026-09-08).* The vector store is a usearch HNSW file per model
next to `prax.db` (`prax.vectors`), memory-mapped by the serving path
(the process holds only the pages it touches), rebuilt and appended by the
batch job, saved atomically. SQLite keeps the bookkeeping
(`chunk_embeddings`) and nothing else about vectors, which also removes
the last blob from the database. Deleted chunks leave stale keys that
queries filter and the job compacts. usearch over LanceDB: one file, no
Arrow stack, aarch64 wheels, 46 ms per query. sqlite-vec is gone from the
dependencies; `init_db` drops the legacy table when it can.

*Rerank (2026-09-08).* The optional cross-encoder was benchmarked rather
than assumed: MiniLM-L6 (MS MARCO) and bge-reranker-base over the top 10
and 30 hybrid hits. No configuration beat the fused list by more than
noise (best: MiniLM at depth 10, MRR 0.84 vs 0.83), and depth 30 lowered
MRR to 0.81 and 0.75. Web-passage cross-encoders do not transfer to raw
technical chunks scored without their document context. Reranking stays
off; the plumbing stays for a document-aware variant.

**Revisit when.** Vectors exceed about 1M. Then move only the vector layer
to LanceDB.

*Document field (2026-09-10).* Chunk scoring, lexical or vector, rewards
documents that mention a term often, so "schematic" ranked a CAD manual
(126 chunks about schematics) first and the library's one schematic
outside the top 30; "1176 schematic" landed at rank 17 behind page
numbers. A document's identity lives in one short sentence at the top of
its description, and chunk scoring treats it like any paragraph. The fix
is a document-level field (title, kind words, creators, venue, extraction
summary, an image description's opening paragraph; migration 0005),
indexed for BM25 and embedded once per document into its own usearch
file, and fused as two more rank lists in the same document-level RRF. A
match in a short field is strong under BM25, so a query naming what a
document is finds it, while documents about the term keep their chunk
ranks. The field BM25 list is weighted 2 for queries of up to three words
and fades to 1 by seven: a short query names a thing, a long paraphrase is
about content, and the field's incidental word matches misled the long
ones at a flat weight. On the library set hybrid went from MRR 0.79 to
0.89 (`docs/eval/retrieval-field-2026-09-10.md`). The field also carries
the kind words that back the `doctype` filter.

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

*Extraction (2026-09-09).* The extractor's prompt and output schema are
generated from `ontology.yaml`, so the validator at the door and the
instructions the model sees cannot drift apart. Every extracted edge
carries a quoted `evidence` string (migration 0004) rather than a chunk
id, because chunks are disposable; triples outside the ontology land in
`review_queue` for a person, never in the graph. Runs are incremental per
ontology version and budgeted; the Message Batches API halves the cost of
a backfill. Model choice is a per-run setting because the cost spread is
five to one between tiers on a 8,448-document library.

*Provenance (2026-09-11).* Which model or importer wrote an edge is a
column pair on the edge (`producer`, `run`), not a `produced_by` edge to a
model entity. The graph would otherwise gain 50,000 bookkeeping edges and
a few hub nodes of degree in the tens of thousands that traversal, the
overview and the context column would have to step around; what one does
with provenance is select, retire and upgrade, all WHERE clauses. The
document keeps every extraction stamp it ever received
(`meta.extraction_history`), so re-reading a document with another model
does not erase who read it before.

## R8. Embeddings and parsing are batch jobs on the bigger box

**Decision.** bge-small-class, 384-dim, INT8 ONNX for embeddings.
pymupdf4llm for PDF (plain pymupdf as fallback), trafilatura for HTML,
through a pluggable extractor registry (`prax.parsers`). OCR and Docling
exist as explicit-only extractors. All of it runs as scheduled batch work
on the desktop; the SBC serves.

**Why.** Every latency figure in the survey came from x86; the serving
board will be slower. Keeping ML dependencies out of the serving path
keeps resident RAM under 1 GB. Model2Vec is the fallback if even bge-small
is too slow.

*PDF extractor (2026-09-07).* The survey favoured Docling on published
table-accuracy figures. Measured on this library instead
(`docs/eval/extractors-2026-09-07.md`: twenty table-heavy PDFs, datasheets
and papers): Docling and pymupdf4llm recover the same table rows with
identical cells on every document with real tables (201 vs 195, 184 vs
185, 105 vs 105 rows), the same character volume, and the same failures
on a broken font. Docling missed a grid pymupdf4llm found and took 420 s
against 86 s (plain pymupdf: under a second, no structure). No measurable
gain at five times the cost and a 3 GB PyTorch dependency, so pymupdf4llm
is the default and Docling stays available for a hand-picked document
through `--extractor docling`.

*OCR.* pymupdf4llm 1.28 runs RapidOCR on pages without a text layer by
default. The cache-less backlog is largely scans, one of them a 412-page
book, so OCR is a separate explicit extractor with a page budget and the
default refuses documents whose first pages have no text layer (they are
left pending and reported as empty).

*Local models (2026-09-08).* The same batch host can run a 7B model in
process through `llama-cpp-python` (`docs/eval/local-llm-2026-09-08.md`):
Qwen2.5-7B at Q4 fits the 8 GB GTX 1070 and produces valid, grammar
constrained extractions, but at 80 s per document the 8,448-document
backlog is a week of GPU time against a $25-125 Claude batch job, so the
API is the bulk path and the local model the trickle path (new documents,
private material, the UI's "ask"). The runtime is an optional extra behind
`prax.local_llm`; the serving host never loads it (invariant 7).

**Cost.** The 384 dimension is baked into `chunks_vec`; changing models is
a migration. Every extractor stamps `meta.text_source` with its name and
version, so a future re-extraction pass is a queue selection, not a
migration.

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

**Hosts (2026-09).** Development and every batch job (import, parsing,
embedding, enrichment) run on the Windows desktop, where the full scratch
store lives at `C:\prax-data`. The serving host is a Pi-class SBC; an
8 GB Radxa Dragon Q6A (Qualcomm QCS6490, eight Arm cores) is on hand and
is the first candidate, with a dedicated small box as a later upgrade.
Sizing rule: the serving path must fit the 8 GB board with room to spare;
Docling and anything else heavy stays on the desktop. The Q6A's NPU is
not part of the plan. Moving the service is a copy of the data directory
onto an SSD attached to the board.

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

## R15. Pages are documents: a wiki inside the store, with revisions and edges

Notes on a document, ongoing projects and topic write-ups (2026-09-10)
are documents rows with `source = wiki` and Markdown text, not a second
system: chunks, the retrieval field, embeddings, extraction and the
context column apply to them unchanged, and the MCP door can read and
write them. Identity is the slug (`pages`); every save is a new
content-addressed text artifact plus an append-only `page_revisions` row
with its author, so the history is a query and invariant 2 holds for
living text (the archived original is the first revision with an
identity line). Relationships are edges, never columns: `page
--annotates--> paper` with the page as source document, `paper --part_of
--> project` for reading lists (ontology v3). Two rules keep an agentic
wiki honest: agent text carries the chunk or document ids it read, and an
agent revision never overwrites a human one (`write_page` refuses it;
`append_page` adds a section). The graph stays the structured record;
pages are prose with citations into it.

## R16. Ask is retrieval plus a swappable model, and the bundle is the contract

A question (2026-09-11) is answered from what search already finds:
the bundle is one passage per document from the hybrid ranking plus
the graph's facts about those documents, bounded to about 3,000
tokens. Bounding it that small is a choice against long-context
prompting: it fits a 7B model on the desktop's 8 GB card with room
for the answer, it keeps a Claude call cheap, and it makes the model's
job reading, not searching, which is what a small model does well.
The graph facts are there because the passages are chunks and a chunk
rarely says what a paper proposes; the facts do, in the canonical
names the graph uses. Who answers is a per-host setting, not a
design: the desktop runs the local model in the door's process, the
serving board runs nothing and returns the bundle (invariant 7), and
the MCP tool returns the bundle by default because its client is
Claude and a second model's answer would only be re-read. Citations
are passage numbers resolved back to chunk and document ids, the same
rule pages follow (R15): an answer worth keeping is appended to a page
as the agent, with its sources listed and `annotates` edges to the
documents, so the prose stays traceable into the store. What this is
not: a chat with memory, or an agent that searches iteratively; both
would be built on the MCP side, where the model can call `search`,
`get_chunk` and `ask` itself.

## R14. The web UI is a client of the door: static files, no framework

**Decision.** The browser UI is a directory of static files (one page,
plain JavaScript and CSS, a vendored Markdown renderer) served by the
FastAPI process that already runs on the serving host, mounted at `/ui/`.
It talks to the same JSON endpoints the agent uses plus a few read-only
browsing endpoints (document lists, the archived original, the text
artifact, the chunk outline, entity lookup). Rendering happens in the
browser. Writes from the UI (a `link` from the graph view) go through the
existing API, so `prax.store` stays the one door.

**Why.** Everything the UI needs is a read over data the door already
serves; adding a second server, a template engine or a JavaScript build
would add a process, a toolchain and a deployment step for no capability.
Static files behind the existing door cost nothing at rest, survive a
future change of serving stack unchanged (R6 sketch of a Rust binary), and
keep the agent-shaped endpoints intact because browsing endpoints are
additions. The document view renders the document as its chunks, which
makes "show me the hit in context" a scroll to an element and shows the
structure the chunker found.

**Cost.** A vendored Markdown renderer (about 40 KB) and, for the graph
view, a force-layout library; both pinned files in the repo, no package
manager. Streaming originals from the archive puts file serving on the
door, which is fine behind Tailscale and a bearer token.

**Revisit when.** The UI wants state of its own (saved searches,
annotations); then that state is a table behind the door, still not a
second server.

## R13. A chunk is an addressable region with a searchable rendering

**Decision.** Chunks carry ``kind`` (text, table, figure, code; media kinds
later), a ``locator`` (for text artifacts a character range plus page, with
the invariant ``chunk.text == artifact[start:end]``), the section
``heading`` path, and for tables a parsed grid in ``data``. The chunker
(`prax.chunking`) parses the Markdown artifact into elements and groups
paragraphs by section up to a target size; tables, figure captions and
code blocks are chunks of their own; only over-long paragraphs fall back
to fixed windows. ``search`` reports kind, heading and page and filters by
kind; ``get_chunk`` returns one chunk with its grid.

**Why.** Fixed 1000-character windows cut tables mid-row and duplicated
rows across the overlap, and gave Claude no way to say "the table in
section 4.1". Structure-aware chunks keep tables whole, let search be
filtered to tables or figures, and let a hit be fetched exactly through
its locator instead of a guessed offset. The locator is the seam for
other media: an audio segment is a time range with its transcript as the
rendering, an image region a box with its caption; each gets its own
embedding space when it arrives, fused by the same rank fusion. Doing this
before Stage 2 means embeddings and the eval harness are built on final
chunk boundaries, and re-chunking is a script because chunks are
disposable (R3).

**Cost.** Chunk boundaries now depend on the extractor's Markdown; a
plain-text artifact (the Zotero cache) yields paragraph chunks without
headings or tables, which is why the upgrade pass over cache-derived
documents is worth running. Legacy rows keep NULL structure until
`scripts/rechunk.py` runs.
