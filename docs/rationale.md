# Rationale — decision records

Each entry says what was decided, why, what it costs, and when to
revisit it. The raw survey they were drawn from is `research.md`. The
invariants they produce are in `CLAUDE.md`. They are numbered so other
docs can cite them (R1, R2, …).

## R1. Compose around SQLite; do not adopt a monolith

**Decision.** Build from libraries (FTS5, sqlite-vec, FastAPI, the
official `mcp` package, Docling, trafilatura) around one SQLite file.
Do not deploy Khoj, RAGFlow, R2R, Cognee or LightRAG-server.

**Why.** Every all-in-one drags in Postgres, Elasticsearch, Neo4j or
Docker sprawl. That fights three goals at once: the Pi-class power
budget, single-file durability, and one maintainer. txtai was the
closest SQLite-native option, but it still centralizes the graph and
search in its own index format.

**Cost.** Enrichment, importers and the eval harness are written here,
not inherited.

**Revisit when** maintaining the enrichment pipeline becomes the
bottleneck (R7) and a heavier dependency is acceptable. Then Cognee or
LightRAG-server give MCP plus graph out of the box.

## R2. Originals are content-addressed; the hash is of the original bytes

**Decision.** Every PDF or HTML snapshot is stored once at
`archive/<sha256[:2]>/<sha256>`. `documents.hash` is the sha256 of
those bytes. Extracted text is never the identity of a document.

**Why.** Dedupe across Zotero, the old zoetrope disk and browser
captures falls out of hashing with no bookkeeping. Parsers change over
time and the original does not, so the identity must not depend on the
parser. This matches the layout ArchiveBox already uses.

**Cost.** Two captures of the same page on different days are two
documents. The browser-capture path handles that by URL
(`sources.md`).

## R3. Parsed text is its own artifact; chunks are disposable

**Decision.** `index_text` writes the parsed text to the archive
(content-addressed, `documents.text_hash`) and derives chunks from it.
`get` reads the artifact. Chunks and FTS rows can be deleted and
rebuilt at any time.

**Why.** The Stage 0 skeleton rebuilt a document by concatenating its
overlapping chunks, and returned corrupted text for anything longer
than one chunk. Beyond that bug, re-chunking is inevitable. Docling
brings structure-aware chunks in Stage 1, and re-embedding in Stage 2
wants to change the chunk size. With the text held separately, both
are cheap batch jobs.

**Cost.** Text is stored twice, as the artifact and as FTS content. At
this scale that is megabytes.

## R4. Ingest is two steps: register, then index

**Decision.** `register(bytes, mime, …)` archives the bytes and inserts
the row with `parsed_at NULL`. `index_text(doc_id, text)` does the
rest. Parsing is a separate batch job that walks the documents with
`parsed_at IS NULL`.

**Why.** Docling on a Pi takes minutes per PDF, and the serving path
must never wait on it. Importers can therefore run on the Pi (hash and
copy) while parsing runs on the N100 or a laptop. The single SQLite
file plus the archive directory can be moved between them.

## R5. One door is `prax.store`; the MCP server imports it in-process

**Decision.** All writes go through the `prax.store` module. The
FastAPI app and the MCP server both call it directly. The MCP server
carries no logic.

**Why.** The research doc recommended having the MCP server proxy the
HTTP API. Importing the store instead removes an HTTP hop and a running
service from the local Claude Code loop, which is where most use
happens today.

**Cost.** When Claude Code spawns the stdio MCP server while uvicorn is
also running, two processes write the same file. WAL mode and Python's
default 5 s busy timeout serialize them safely at personal scale. A
process-wide lock serializes threads within each process. This is a
recorded deviation from the single-writer invariant.

**Revisit when** the service and the MCP server run permanently on the
same host. Then the MCP server should proxy the HTTP door, and the
deviation closes. (It did: R10.)

## R6. Hybrid retrieval: FTS5 + a usearch index fused with RRF, rerank optional

**Decision.** Keyword (BM25) and vector search run in parallel and are
fused by Reciprocal Rank Fusion. A cross-encoder rerank
(bge-reranker-v2-m3) over the fused top-N is optional, and off until
benchmarked on the Pi.

**Why.** RRF is rank-based, so it sidesteps incompatible score scales.
Published lifts for hybrid over either method alone are consistent.
One retail benchmark shows around 7% NDCG. A 23k-query benchmark shows
recall@5 of 0.82 against 0.59 for dense-only, with reranking.
sqlite-vec keeps vectors in the same file, so a deploy is a file copy.

**Cost.** Embedding is a batch job (R8). Search results lag ingest
until it runs.

*Measured (2026-09-07).* Embedding cost is dominated by sequence
length. bge-small (33M parameters) needs about 20 GFLOP for a 330-token
chunk. A 12-core desktop CPU manages 20 chunks/s with the int8 export;
the GTX 1070 through DirectML manages 53 chunks/s with fp32 at batch
64 (batch 128 is slower). The library's 855 K chunks (median 180
tokens, 3% truncated at 512) are therefore a 4–5 hour one-off batch.
fastembed was dropped: its default bge-small file is fp16, which
onnxruntime emulates on CPU at 3 chunks/s. The store talks to
onnxruntime and `tokenizers` directly, with files from the Hugging
Face hub. The vec0 table carried `kind` as a metadata column, so a KNN
query could be filtered to tables or figures without a post-filter.

*Measured on the library (2026-09-08, `docs/eval/retrieval-library-*`).*
Fusion has to happen per document, not per chunk. Chunk-level RRF
scored below FTS alone (MRR 0.80 against 0.82). Document-level RRF,
with each side's best chunk rank, gives hit@1 0.77 and MRR 0.83 on 62
queries against the full store, above FTS (0.82) and vectors alone
(0.81), at depth 100 per side. Latency is the binding constraint.
sqlite-vec scans every vector, and at 855 K vectors a KNN query takes
4.2 s warm (int8 1.8 s; binary with fp32 rescoring 2.7 s). Hybrid search
therefore took 5 s end to end, against 0.3 s for FTS. A memory-mapped
usearch HNSW index over the same vectors answers in 46 ms at recall@10
0.98 (f16, a 784 MB file) or 18 ms at 0.93 (int8, 456 MB). The
threshold below had been set at 1 M vectors; the wall arrived at
0.86 M.

*Decision (2026-09-08).* The vector store is a usearch HNSW file per
model next to `prax.db` (`prax.vectors`). The serving path memory-maps
it, so the process holds only the pages it touches. The batch job
rebuilds and appends it and saves it atomically. SQLite keeps the
bookkeeping (`chunk_embeddings`) and nothing else about vectors, which
also removes the last blob from the database. Deleted chunks leave
stale keys, which queries filter and the job compacts. The choice of
usearch over LanceDB had four reasons: one file, no Arrow stack,
aarch64 wheels, 46 ms per query. sqlite-vec is gone from the
dependencies; `init_db` drops the legacy table when it can.

*Rerank (2026-09-08).* The optional cross-encoder was benchmarked
instead of assumed: MiniLM-L6 (MS MARCO) and bge-reranker-base over the
top 10 and top 30 hybrid hits. No configuration beat the fused list by
more than noise. The best was MiniLM at depth 10, MRR 0.84 against
0.83. Depth 30 lowered MRR to 0.81 and 0.75. Web-passage cross-encoders
do not transfer to raw technical chunks scored without their document
context. Reranking stays off. The plumbing stays for a document-aware
variant.

**Revisit when** vectors exceed about 1M. Then move only the vector
layer to LanceDB.

*Document field (2026-09-10).* Chunk scoring, lexical or vector,
rewards documents that mention a term often. "schematic" ranked a CAD
manual (126 chunks about schematics) first and the library's one
schematic outside the top 30. "1176 schematic" landed at rank 17,
behind page numbers. A document's identity lives in one short sentence
at the top of its description, and chunk scoring treats it like any
paragraph. The fix is a document-level field: title, kind words,
creators, venue, the extraction summary, an image description's
opening paragraph (migration 0005). It is indexed for BM25, embedded
once per document into its own usearch file, and fused as two more
rank lists in the same document-level RRF. A match in a short field is
strong under BM25, so a query naming what a document is finds it, while
documents about the term keep their chunk ranks. The field BM25 list
is weighted 2 for queries of up to three words and fades to 1 by seven
words. A short query names a thing and a long paraphrase is about
content, and the field's incidental word matches misled the long ones
at a flat weight. On the library set, hybrid went from MRR 0.79 to 0.89
(`docs/eval/retrieval-field-2026-09-10.md`). The field also carries the
kind words that back the `doctype` filter.

## R7. Graph: plain edge table, bi-temporal, evidence not truth

**Decision.** Edges live in one SQLite table with `confidence`,
`source_doc`, `ontology_version`, `valid_from`, `valid_to` and
`ingested_at`. Enrichment invalidates edges by setting `valid_to`; it
never deletes. Traversal is a recursive CTE, capped at two hops. The
ontology is small and versioned. Microsoft GraphRAG, Neo4j and
Graphiti are not used.

**Why.** Recursive CTEs are adequate below roughly 50k entities.
GraphRAG recomputes community summaries on update. Graphiti has the
right temporal model but needs a graph server. The bi-temporal columns
are borrowed from Graphiti, so nightly enrichment is auditable and
reversible. Small-scale evidence shows that LLM-built graphs fail at
complement queries ("what is NOT connected") and at weighted
propagation. The graph is therefore an entry-point enhancer for
retrieval, and those queries are explicit SQL tools.

**Revisit when** entities pass 50–100k or traversal gets slow. Then
move the edges to Kùzu (embedded), not Neo4j.

*Extraction (2026-09-09).* The extractor's prompt and output schema are
generated from the ontology modules in `ontology/`, so the validator at
the door and the instructions the model sees cannot drift apart. Every
extracted edge carries a quoted `evidence` string (migration 0004), not
a chunk id, because chunks are disposable. Triples outside the ontology
land in `review_queue` for a person, never in the graph. Runs are
incremental per ontology version and budgeted. The Message Batches API
halves the cost of a backfill. Model choice is a per-run setting,
because the cost spread between tiers is five to one on an
8,448-document library.

*Provenance (2026-09-11).* Which model or importer wrote an edge is a
column pair on the edge (`producer`, `run`), not a `produced_by` edge
to a model entity. The graph would otherwise gain 50,000 bookkeeping
edges and a few hub nodes with degrees in the tens of thousands, which
traversal, the overview and the context column would have to step
around. What one does with provenance is select, retire and upgrade,
and those are WHERE clauses. The document keeps every extraction stamp
it ever received (`meta.extraction_history`), so re-reading a document
with another model does not erase who read it before.

## R8. Embeddings and parsing are batch jobs on the bigger box

**Decision.** bge-small-class, 384-dim, INT8 ONNX for embeddings.
pymupdf4llm for PDFs, with plain pymupdf as the fallback; trafilatura
for HTML; both through a pluggable extractor registry (`prax.parsers`).
OCR and Docling exist as explicit-only extractors. All of it runs as
scheduled batch work on the desktop. The SBC serves.

**Why.** Every latency figure in the survey came from x86, and the
serving board will be slower. Keeping ML dependencies out of the
serving path keeps resident RAM under 1 GB. Model2Vec is the fallback
if even bge-small is too slow.

*PDF extractor (2026-09-07).* The survey favoured Docling on published
table-accuracy figures. It was measured on this library instead
(`docs/eval/extractors-2026-09-07.md`: twenty table-heavy PDFs,
datasheets and papers). Docling and pymupdf4llm recover the same table
rows with identical cells on every document with real tables (201
against 195, 184 against 185, 105 against 105 rows), the same character
volume, and the same failures on a broken font. Docling missed a grid
that pymupdf4llm found, and took 420 s against 86 s; plain pymupdf takes
under a second, with no structure. There was no measurable gain at five
times the cost and a 3 GB PyTorch dependency. pymupdf4llm is the
default, and Docling stays available for a hand-picked document through
`--extractor docling`.

*OCR.* pymupdf4llm 1.28 runs RapidOCR on pages without a text layer by
default. The cache-less backlog is largely scans, one of them a
412-page book. OCR is therefore a separate explicit extractor with a
page budget, and the default refuses documents whose first pages have
no text layer. Those are left pending and reported as empty.

*Local models (2026-09-08).* The same batch host can run a 7B model in
process through `llama-cpp-python` (`docs/eval/local-llm-2026-09-08.md`).
Qwen2.5-7B at Q4 fits the 8 GB GTX 1070 and produces valid,
grammar-constrained extractions. But at 80 s per document the
8,448-document backlog is a week of GPU time, against a $25–125 Claude
batch job. So the API was the bulk path and the local model the
trickle path: new documents, private material, the UI's "ask". The
in-process binding was dropped on 2026-09-12, once llama-server (an
`openai` model in `prax.yaml`) did the same job from its own process.
That meant no wheel, no CUDA runtime in the venv, and no model inside
the door or the worker (invariant 7). The measurements stand.

**Cost.** The 384 dimension was baked into `chunks_vec`, so changing
models is a migration. Every extractor stamps `meta.text_source` with
its name and version, so a future re-extraction pass is a queue
selection, not a migration.

## R9. Sources: Zotero first, browser tabs second, front-ends later

**Decision.** The existing Zotero library is the primary corpus and
gets a dedicated importer. Live capture is a small browser extension
posting tabs to the HTTP door. Karakeep or Linkwarden are optional
later front-ends that would feed the same inbox.

**Why.** Zotero already holds the curated PDFs with the metadata that
seeds the graph: authors, tags, collections. A tab-capture extension is
the shortest path from "reading now" to "in the base", and it needs
nothing but the API. Both write through `prax.store`, so a front-end
can be swapped without touching search. Importers are read-only on
their source, by invariant.

## R10. The MCP server is a proxy of the door, on the official `mcp` package

**Decision.** `prax.mcp_server` depends on `mcp>=2,<3` (its `MCPServer`
over stdio). It makes one HTTP call to the door per tool, through
`prax.client`, and imports no store module.

**Why.** Until 2026-09-12 the server was a standalone FastMCP 4.x
process that imported the store, which made it a second writer next to
the door (invariant 4's known deviation). A proxy needs no store
dependencies in the process Claude Code spawns, and it works against a
door on another machine. The official package is what FastMCP itself
builds on, so the framework bought nothing the proxy uses. Kept from
the old decision: stdio only. A streamable-HTTP transport stays a
"Later" item.

## R11. Deployment: single file on an SSD, on a private network

**Decision.** `data/` (database plus archive) lives on an external SSD
on the Pi, never on the SD card. The door is bound to the private
network's interface only: the LAN, or any VPN. Tailscale is one
example, and the least setup. The door checks a bearer token. Plain
HTTP is fine there; TLS through a reverse proxy only if the door were
ever exposed. Backups are file copies (Litestream later).

**Why.** FTS and vector churn plus nightly jobs would wear an SD card.
That is the top reliability risk. The single-file store makes backup
and migration a copy.

**Hosts (2026-09).** Development and every batch job (import, parsing,
embedding, enrichment) run on the Windows desktop, where the full store
lives on a local SSD. The serving host is a Pi-class SBC. An 8 GB Radxa
Dragon Q6A (Qualcomm QCS6490, eight Arm cores) is on hand and is the
first candidate, with a dedicated small box as a later upgrade. The
sizing rule: the serving path must fit the 8 GB board with room to
spare. Docling and anything else heavy stays on the desktop. The Q6A's
NPU is not part of the plan. Moving the service is a copy of the data
directory onto an SSD attached to the board.

## R12. Incremental schema and ontology: numbered migrations, versioned types

**Decision.** The schema is a sequence of numbered SQL migrations under
`src/prax/migrations/`, applied by `store.init_db` and recorded in
SQLite's `user_version`. The ontology is versioned YAML loaded by
`prax.ontology`; `store.link` validates every edge against it and
stamps the version. Anything a source knows that has no column yet goes
into `documents.meta` as JSON.

**Why.** The store is meant to outlive its first corpus. After papers
come personal, family and music material, with different metadata and
their own entity and relation types. Three cheap mechanisms keep that
incremental. Migrations mean an old database upgrades in place instead
of being re-ingested. Versioned ontology stamps mean old edges stay
interpretable after the type set grows, and validation at the one door
keeps misfits out without a second code path. JSON `meta` means a new
source ships without a schema change. A key that turns out to be
queried often is promoted to a column, or a JSON index, by the next
migration. A Stage 0 database with `user_version = 0` is recognised and
stamped, so nothing was thrown away.

**Cost.** A schema change touches two files: the migration and the
store code that uses it. Domain and range constraints in the ontology
are optional, so the file can stay small.

**Revisit when** migrations need data transformations that Python must
drive, such as a rename of an entity type or a re-chunking. Then add a
Python hook per migration number alongside the SQL. Not before.

## R15. Pages are documents: a wiki inside the store, with revisions and edges

**Decision (2026-09-10).** Notes on a document, ongoing projects and
topic write-ups are `documents` rows with `source = wiki` and Markdown
text, not a second system. Chunks, the retrieval field, embeddings,
extraction and the context column apply to them unchanged, and the MCP
door can read and write them. The identity is the slug (`pages`).
Every save is a new content-addressed text artifact plus an append-only
`page_revisions` row with its author. The history is therefore a
query, and invariant 2 holds for living text: the archived original is
the first revision, with an identity line. Relationships are edges,
never columns: `page --annotates--> paper` with the page as the source
document, and `paper --part_of--> project` for reading lists (ontology
v3).

**Why.** Two rules keep an agentic wiki honest. Agent text carries the
chunk or document ids it read. An agent revision never overwrites a
human one: `write_page` refuses it, and `append_page` adds a section.
The graph stays the structured record. Pages are prose with citations
into it.

## R16. Ask is retrieval plus a swappable model, and the bundle is the contract

**Decision (2026-09-11).** A question is answered from what search
already finds. The bundle is one passage per document from the hybrid
ranking, plus the graph's facts about those documents, bounded to
about 3,000 tokens.

**Why.** Bounding the bundle that small is a choice against
long-context prompting. It fits a 7B model on the desktop's 8 GB card
with room for the answer. It keeps a Claude call cheap. And it makes
the model's job reading, not searching, which is what a small model
does well. The graph facts are there because the passages are chunks,
and a chunk rarely says what a paper proposes; the facts do, in the
canonical names the graph uses. Who answers is a per-host setting, not
a design. The desktop runs the local model in the door's process. The
serving board runs nothing and returns the bundle (invariant 7). The
MCP tool returns the bundle by default, because its client is Claude
and a second model's answer would only be re-read. Citations are
passage numbers resolved back to chunk and document ids, the same rule
pages follow (R15). An answer worth keeping is appended to a page as
the agent, with its sources listed and `annotates` edges to the
documents, so the prose stays traceable into the store.

What this is not: a chat with memory, or an agent that searches
iteratively. Both would be built on the MCP side, where the model can
call `search`, `get_chunk` and `ask` itself. (The surf, R16's later
step, gave the model a bounded loop of reads after all; see `ask.md`.)

## R14. The web UI is a client of the door: static files, no framework

**Decision.** The browser UI is a directory of static files: one page,
plain JavaScript and CSS, a vendored Markdown renderer. The FastAPI
process that already runs on the serving host serves it at `/ui/`. It
talks to the same JSON endpoints the agent uses, plus a few read-only
browsing endpoints: document lists, the archived original, the text
artifact, the chunk outline, entity lookup. Rendering happens in the
browser. Writes from the UI (a `link` from the graph view) go through
the existing API, so `prax.store` stays the one door.

**Why.** Everything the UI needs is a read over data the door already
serves. A second server, a template engine or a JavaScript build would
add a process, a toolchain and a deployment step for no capability.
Static files behind the existing door cost nothing at rest, survive a
future change of serving stack unchanged (R6's sketch of a Rust
binary), and keep the agent-shaped endpoints intact, because browsing
endpoints are additions. The document view renders the document as its
chunks. That makes "show me the hit in context" a scroll to an element,
and it shows the structure the chunker found.

**Cost.** A vendored Markdown renderer (about 40 KB) and, for the graph
view, a force-layout library, both pinned files in the repo with no
package manager. Streaming originals from the archive puts file serving
on the door, which is fine inside a private network and behind a bearer
token.

**Revisit when** the UI wants state of its own, such as saved searches
or annotations. Then that state is a table behind the door, still not a
second server.

## R13. A chunk is an addressable region with a searchable rendering

**Decision.** Chunks carry `kind` (text, table, figure, code; media
kinds later), a `locator` (for text artifacts a character range plus
page, with the invariant `chunk.text == artifact[start:end]`), the
section `heading` path, and for tables a parsed grid in `data`. The
chunker (`prax.chunking`) parses the Markdown artifact into elements
and groups paragraphs by section up to a target size. Tables, figure
captions and code blocks are chunks of their own. Only over-long
paragraphs fall back to fixed windows. `search` reports kind, heading
and page and filters by kind. `get_chunk` returns one chunk with its
grid.

**Why.** Fixed 1000-character windows cut tables mid-row, duplicated
rows across the overlap, and gave Claude no way to say "the table in
section 4.1". Structure-aware chunks keep tables whole, let search be
filtered to tables or figures, and let a hit be fetched exactly through
its locator instead of a guessed offset. The locator is the seam for
other media. An audio segment is a time range with its transcript as
the rendering. An image region is a box with its caption. Each gets its
own embedding space when it arrives, fused by the same rank fusion.
Doing this before Stage 2 means embeddings and the eval harness are
built on final chunk boundaries. Re-chunking is a script, because
chunks are disposable (R3).

**Cost.** Chunk boundaries now depend on the extractor's Markdown. A
plain-text artifact (the Zotero cache) yields paragraph chunks without
headings or tables, which is why the upgrade pass over cache-derived
documents is worth running. Legacy rows keep NULL structure until
`prax maintain --rechunk` runs.

## R17. A block a model keeps inside a person's page: sentinels, a hash, a fingerprint, no overwrite of hands

**Decision (2026-09-21),** after the survey in `research.md` ("Living
answers and mixed pages"). The standing question (2026-09-21,
`prax.questions`) is a page that *is* one question. Research mostly
happens on a page of one's own: notes, links to documents, and a few
questions one wants kept current. The block is that: a region of a
person's page the door keeps answered, with everything around it the
person's.

**The region is a comment pair with a hash.** The block is written by
hand as `<!-- prax:ask id=q1 "how do feedback delay networks stay
lossless" -->` … `<!-- /prax:ask id=q1 -->`. The door fills what is
between and closes it with the interior's hash and the pass in the tail
marker (`sha=`, `asked=`, `run=`). HTML comments render as nothing in
every Markdown viewer, survive every editor, and diff as two stable
lines (doctoc, markdown-magic, obsidian-second-brain). The query lives
in the marker and the result inside, as in org-babel's `#+NAME` and
`#+RESULTS[hash]`. Nothing of the pass, neither a timestamp nor a
counter, goes into the text; Foam's regenerated section is the
cautionary case. The id is the block's identity across runs, so a
person may move the block. The options a question takes (`steps=`,
`doctype=`, `mode=`) are attributes of the head.

**The door never writes over hands.** Before a rewrite, the interior is
hashed against the tail's `sha`. On a mismatch the block is *held*: the
run says so, the page's meta records why, and the UI shows "edited by
hand; the door left it" with the choice to release it. That is cog's
rule. A `<!-- prax:keep -->` … `<!-- /prax:keep -->` sub-region inside
the block is carried over verbatim (Zotero Integration's `persist`), so
an annotation to a generated answer does not fork it. Outside the pair
the service touches nothing. The rewrite is a store function that
replaces interiors by id and refuses when a marker is missing, so the
guarantee is structural, not a `force` flag. The revision it writes is
the agent's, and its note names the block and what changed.

**A block is a standing question with the same check.** Its
fingerprint sits in the page's meta per block (`meta.asks[id]`): the
documents it drew on and their text hashes, the search's top, the
library's high-water mark. The check is the same cheap one: a new
document that ranks or shares entities, or a source read again. The
clock's `questions` job covers pages with blocks. The check writes only
a status (`checked_at`, what is pending), the cheap dated channel a
living review keeps, and never the page. The rewrite is the rare
revision, and its note is the prose "what changed". A block's words are
set aside from retrieval and never embedded (a chunk kind `ask`, beside
`reference`). Generated text must not become evidence for its own
re-ask or anyone else's (atomic's kind discipline, STORM's source-bias
transfer).

**Revision, not regeneration, with the kind of change named.** The
re-ask sees the earlier answer as its own previous turn. It is asked
again with what is new named: "the library now also holds X; where it
changes the answer, replace; where it adds, add; where it disagrees,
say so". Naming the kind of conflict is what lifts a model's behaviour
on stale answers, and anchoring on the earlier text survives prompting
alone. The evidence goes newest last, and the answer may cite only this
turn's passages.

**Links a person writes are relations.** A `[title](#doc/123)` in a
page becomes `page --annotates--> document` on save (producer `page`,
run `slug@revision`), retired when the link goes. The graph already
treats an answer's sources that way, and a hand-written page should
enter it by the same door. Typed inline fields (`part_of::
[[project]]`, the Breadcrumbs form) are the natural next step and are
not in this cut.

**What is deferred, and why.** A proposal with a diff and an accept
(atomic's section operations) for a block the model owns. The survey
says overwrite is the norm for a machine-owned field, and the gate
belongs where the model touches human text, which the pair forbids. A
`mode=propose` attribute can add it later without changing the
contract. Also deferred: claim-level adjudication (KEEP / STALE /
REPLACE against new hits, outside generation) and a contradiction scan
over `argues` edges. That is the mechanism that makes a re-ask cheap
and honest, one stage further, once the block exists to hang it on.

**As built (2026-09-21).** `prax.blocks` is the grammar and the rewrite
alone, with no store and no model. The hash in the tail is of the
door's own text, with the keep regions and the edge whitespace left
out. An editor's trailing newline and a remark in a keep region are
therefore not hands; a changed word is. A held block is left and noted
in `meta.asks[id]` (`held: {at, why}`), shown as such, and released by
asking it again with `release`. A block the page no longer has (its
markers gone) is refused by `store.fill_blocks` as a structural error,
never `force`d. Saving a page with an unanswered block starts the pass
for that page at once, one job per page at a time, so the answer is
there in a moment and not at the clock's hour. The interior holds the
answer and its source list, without the trail. The sources' links make
the page's edges the way any link does, so a re-ask that drops a source
retires its edge. An edge given as `annotates` (a note made from a
document's page) is never retired by an edit that leaves the link out;
the evidence column says which edges came from links.
