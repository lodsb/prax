# prax architecture

The picture of the whole system as built through Stage 3 (September 2026).
Invariants are in `CLAUDE.md`, the reasoning behind each choice in
`rationale.md` (R1–R15), practical commands in `howto.md`, the web UI in
`ui.md`. This document explains how the parts fit and where to touch what.

## 1. The shape in one paragraph

prax is one SQLite file plus a content-addressed archive of original files,
wrapped in a single Python package. Everything that changes the store goes
through `prax.store` ("one door"). Sources register originals; batch jobs
turn originals into text artifacts, text into structure-aware chunks, and
chunks into a keyword index and vectors; a document-level field says what
each document is; Claude extraction and the citation importer turn
documents into an evidence-bearing graph over a small versioned ontology;
entity resolution merges names for the same thing; pages written by a
person or an agent are documents too. Two thin doors serve queries: a
FastAPI HTTP service (with a plain web UI as its client) and a FastMCP
server that gives Claude search, get, traverse, link, ingest and page
tools. The desktop runs the batch jobs; a Pi-class board is meant to
serve.

```mermaid
flowchart LR
  subgraph sources [Sources]
    Z[Zotero library<br/>read-only copy]
    CR[Crossref / OpenAlex<br/>reference lists]
    W[Pages<br/>UI, MCP]
    B[Browser extension, inbox<br/>planned]
  end
  subgraph batch [Batch jobs, desktop]
    IMP[import_zotero.py]
    PQ[parse_pending.py<br/>extractors, vision]
    EMB[embed_pending.py<br/>chunks + document fields]
    EXT[extract_graph.py<br/>Claude, batch API]
    CIT[import_citations.py]
    RES[resolve_entities.py]
  end
  subgraph store [prax.store, the one door]
    DB[(prax.db<br/>SQLite, WAL)]
    AR[(archive/<br/>sha256-addressed files)]
    VX[(vectors-*.usearch<br/>chunks, documents)]
  end
  subgraph doors [Doors]
    API[FastAPI door<br/>agent, browsing, review, pages]
    MCP[FastMCP stdio<br/>search get traverse link ingest pages]
    UI[Web UI at /ui/<br/>search, document + context, graph, review, pages]
  end
  Z --> IMP --> store
  CR --> CIT --> store
  W --> API
  B -.-> API
  PQ --> store
  EMB --> store
  EXT --> store
  RES --> store
  store --> API
  store --> MCP --> C[Claude Code]
  API --> UI --> Browser
```

## 2. Two hosts, one directory

| Where | What runs | Why |
|---|---|---|
| Windows desktop (12 cores, GTX 1070 8 GB) | development, every batch job: import, parse, OCR, vision, embed, extract, resolve, eval; the local llama.cpp path | MuPDF layout analysis, OCR, embedding and a 7B model are CPU/GPU heavy; never on the serving path (invariant 7) |
| Pi-class SBC (an 8 GB Radxa Dragon Q6A is on hand; a Mac mini or N100 box under consideration) | the HTTP door, the UI and the MCP door over Tailscale | under 1 GB resident: SQLite, FTS5, two memory-mapped usearch files and one query embedding |

The store is one directory (`PRAX_DATA_DIR`, currently `C:\prax-data`):

    prax.db, prax.db-wal, prax.db-shm     the database
    archive/<xx>/<sha256>                originals and text artifacts
    vectors-<model>.usearch              chunk vectors, keyed by chunk id
    vectors-doc-<model>.usearch          document-field vectors, keyed by document id
    batches/<id>.json                    submitted extraction batches
    zotero-import/zotero.sqlite          the importer's private copy

Moving the service is a copy of that directory (R11). On Windows the door
keeps the vector files memory-mapped, so an embedding run needs the door
stopped; the batch host and the serving host otherwise coexist under WAL
and the single-writer rule (invariant 4).

## 3. Life of a document

Every source ends up in the same steps. Each step leaves a stamp that lets
a later, better pass find its work again.

```mermaid
flowchart TD
  O[original bytes] -->|register: sha256, archive| D[documents row<br/>hash, mime, title, meta JSON]
  D -->|extractor by MIME<br/>meta.text_source = name/version| T[text artifact<br/>Markdown, archived, documents.text_hash]
  T -->|prax.chunking| CH[chunks<br/>kind, locator, heading, data]
  CH --> F[chunks_fts<br/>FTS5 BM25]
  CH -->|embed_pending.py| V[vectors-model.usearch<br/>HNSW 384-d cosine]
  D -->|title, kind, summary| DF[documents_fts + vectors-doc<br/>the document field]
  D -->|extract_graph.py, import_citations.py| G[entities + edges<br/>ontology-typed, bi-temporal, evidence]
  G -->|resolve_entities.py| G
```

1. **Register** (`store.register`). The original bytes are hashed
   (sha256), written once to `archive/`, and a `documents` row is inserted
   with MIME type, title, source URL and a `meta` JSON blob. The same bytes
   under two Zotero records are one document. Nothing is parsed here. (R2,
   R4)
2. **Extract** (`prax.parsers`, `scripts/parse_pending.py`). An extractor
   chosen by MIME type turns the original into Markdown: pymupdf4llm for
   PDFs with a text layer, plain MuPDF as fallback, RapidOCR for scans when
   asked, Docling when named, trafilatura for HTML with `<pre>` blocks
   fenced, plain decode for text with source files fenced as code (by
   extension, else Magika), and `claude-vision` for images (Claude
   describes the picture and transcribes its text, handwriting included).
   The Markdown is its own content-addressed artifact (`documents.text_hash`),
   stamped in `meta.text_source` as `name/version[-rN]`; every attempt is
   appended to `meta.parse_history`. A better extractor later is a queue
   selection (`--upgrade <prefix>`), never a migration. (R3, R8)
3. **Chunk** (`prax.chunking`, inside `index_text`). The Markdown is parsed
   into sections of paragraphs, whole tables with caption and parsed grid,
   figure captions and code listings. Each chunk has a `kind`, a `locator`
   (character range into the artifact plus page, with the invariant
   `chunk.text == artifact[start:end]`), the heading path it sits under,
   and for tables a JSON grid in `data`. Chunks are disposable:
   `scripts/rechunk.py` rebuilds them from the artifacts. (R13)
4. **Index**. FTS5 rows follow chunk inserts through triggers.
   `scripts/embed_pending.py` embeds chunks that have no vector from the
   current model into a usearch HNSW file, then embeds the document field.
   The document field (title, kind words, creators, venue, extraction
   summary, an image description's opening paragraph) is rebuilt by the
   store whenever a document's text or metadata changes and indexed in
   `documents_fts`; it is what makes "schematic" find the schematic. (R6)
5. **Graph**. The Zotero importer seeds `authored_by` edges;
   `prax.extraction` sends the document's header, its first 12,000
   characters and its closing sections to Claude (sync or the Batch API)
   and writes the returned triples with a quoted `evidence`, parking
   misfits in `review_queue`; `prax.importers.citations` writes `cites`
   edges from Crossref or OpenAlex reference lists. Every edge carries the
   ontology version it was written under and bi-temporal validity; nothing
   is deleted, only invalidated. Types are validated against
   `ontology.yaml` at the door; when the ontology grows, `prax.review`
   replays the queue. (R7)
6. **Resolve**. `prax.resolution` merges entities that name the same thing
   through `canonical_id`: sure merges (case, accents, punctuation, author
   initials), concept/method twins into the method, and likely merges by
   name embedding that a Claude adjudicator confirms. Traversal, hubs and
   context walk canonical ids; edges keep the alias they were written with.
7. **Pages**. A note on a document, a project thread or a topic write-up is
   a document with `source = wiki`, Markdown text and append-only
   revisions; its relationships are edges (`annotates`, `part_of`). An
   agent may append but never overwrite a person's revision. (R15)

## 4. Life of a query

```mermaid
flowchart LR
  Q[query, kind?, doctype?, mode] --> FTS[chunk BM25<br/>chunks_fts]
  Q --> QE[query embedding<br/>bge-small] --> KNN[chunk KNN<br/>vectors-model.usearch]
  Q --> DF[document field BM25<br/>documents_fts, weight by query length]
  QE --> DK[document field KNN<br/>vectors-doc-model.usearch]
  FTS --> RRF[reciprocal rank fusion<br/>per document, k = 60]
  KNN --> RRF
  DF --> RRF
  DK --> RRF
  RRF --> H[hits: chunk_id, doc_id, title, snippet, kind,<br/>heading, page, score, fts/vec/field/dvec ranks]
  H -->|get_chunk| C[one chunk: text, locator, table grid]
  H -->|get offset/max_chars| T[text window of the artifact]
  H -->|doc/id/context| X[summary, entities, similar, citations,<br/>shared entities, authors, notes, Zotero]
  H -->|traverse entity| G[1-2 hop neighbourhood, with provenance]
  H -->|ask| B[bundle: one passage per document,<br/>graph facts per document] --> M[local GGUF model, Claude,<br/>or the MCP client itself] --> A[answer citing n] -->|ask/save| P[page section with sources,<br/>annotates edges]
```

`search` fuses four rank lists per document: chunk BM25, chunk KNN, and
BM25 and KNN over the document field. The field list is weighted 2 for
queries of up to three words and fades to 1 by seven, because a short
query names a thing and a long one describes content. A hit says which
lists found it; a hit found only through the field opens at the
document's best-matching chunk. `fts` and `vec` modes are the raw chunk
lists; hybrid degrades to FTS-only when no vectors exist, so the serving
host works before embeddings do. Responses stay small by design (invariant
6): snippets and ids, then `get_chunk`, `get` or `context` for exactly
what is needed.

`ask` is retrieval plus generation on top of the same search (`prax.ask`). The bundle is one passage per document (the matched chunk, 1,200 characters) for the top eight documents plus what the graph records about each of them (its extracted relations, canonical names, `cites` left out), about 3,000 tokens, so it fits a 7B model with an 8 K window. Which model answers is a per-host setting (`PRAX_ASK`): `local` runs a GGUF model in the door's process on the desktop (Qwen2.5-7B answers in about 20 s on the GTX 1070), `claude` calls the API, `none` returns the bundle alone, which is what the MCP tool gives Claude Code by default and what the serving board does, since it loads no model (invariant 7). The answer cites passages as `[n]`; the numbers are resolved to chunk and document ids, the UI links them, and an answer worth keeping is appended to a page as the agent with its sources listed and `annotates` edges to the documents it rests on.

## 5. Module map

| Module | Responsibility | Writes SQLite? |
|---|---|---|
| `prax.store` | the only door: connect, migrations, register, index_text, chunks, the document field, search (FTS, vec, hybrid, doctype), get, context, similar documents, hubs, link, traverse, invalidate, review queue, pages, embedding bookkeeping for chunks and fields | yes, the only one |
| `prax.chunking` | Markdown → structure-aware chunks with locators | no (pure) |
| `prax.parsers` | extractor registry by MIME type with revisions; `parsers.queue` the parse queue with fallback chain, size/page/OCR guards, history; `parsers.vision` images described by Claude | via store |
| `prax.embeddings` | ONNX embedder registry (bge-small default), provider/variant selection, hash embedder for tests | no |
| `prax.vectors` | a usearch index file: view for reads, writable copy for batch jobs, atomic save | no (writes the index file) |
| `prax.ontology` | parses `ontology.yaml`, validates edge types, versions | no |
| `prax.importers.zotero` | read-only copy of `zotero.sqlite` → documents, notes, attachments, authored_by seeds; idempotent per key | via store |
| `prax.importers.citations` | Crossref or OpenAlex by DOI or exact title → `cites` edges, citation counts in `meta.citations`; idempotent per document | via store |
| `prax.extraction` | document input (head plus closing sections), ontology-derived prompt and JSON schema, Claude and local extractors, `apply()` into edges / review queue / stamps with guards | via store |
| `prax.lineformat` | tab-separated output format for local models: bounded GBNF grammar from the ontology, parse/render to `Extraction` | no |
| `prax.local_llm` | optional llama.cpp runtime (`local` extra): DLL path quirk, one loaded GGUF model behind `chat()`, shared per process | no |
| `prax.ask` | a question answered from the library: bundle (passages plus graph facts), answer backends (local, Claude, none, stub), citation resolution, saving an answer to a page | via store |
| `prax.review` | replay of the review queue against a newer ontology | via store |
| `prax.resolution` | entity merge candidates (normalized names, initials, concept/method twins, name embeddings), adjudicators, apply through `merge_entities` | via store |
| `prax.rerank` | optional cross-encoder over the top hits; off by default (measured no gain) | no |
| `prax.evaluation` | fixture store builder, query set runner, report | via store (throwaway) |
| `prax.auth` | bearer token or session cookie on the HTTP door; loopback-only when unset | no |
| `prax.api` | FastAPI door: agent endpoints, browsing, context, graph overview, review, pages, ask; serves the UI's static files with no-cache | via store |
| `prax/ui/` | the web UI: one page, plain JS and CSS, vendored Markdown renderer, an SVG force layout; a client of the door (R14) | no |
| `prax.mcp_server` | FastMCP stdio door; no logic | via store |
| `prax.config` | paths, `PRAX_DATA_DIR`, migrations dir | no |
| `scripts/*.py` | thin CLIs over the modules above: import, parse, rechunk, embed, refresh fields, extract, import citations, resolve, replay, eval, compare extractors, bench the local model, build fixture | via store |

## 6. Data model

```
documents        id, hash (sha256 of original), mime, title, source_url,
                 original_path, text_hash (artifact), added_at, parsed_at, meta JSON
chunks           id, doc_id, seq, text, kind, locator JSON, heading JSON, data JSON
chunks_fts       FTS5 over chunks.text (content table; triggers keep it in step)
chunk_embeddings chunk_id, model, embedded_at          (which model made the vector)
documents_fts    FTS5 over the document field (title, kind, creators, venue, summary, ...)
document_embeddings  doc_id, model, embedded_at        (which document has a field vector)
vectors-<model>.usearch       HNSW index keyed by chunk id, f16, cosine (a file, not a table)
vectors-doc-<model>.usearch   HNSW index of the document field, keyed by document id
entities         id, name, type, canonical_id (resolution merges), created_at
edges            src, dst, rel, confidence, weight, source_doc, ontology_version,
                 evidence (a quote or a source id), producer, run,
                 valid_from, valid_to, ingested_at
review_queue     triples the extractor could not fit, with reason, evidence, resolution
pages            doc_id, slug, kind (addendum | project | topic)
page_revisions   doc_id, revision, text_hash, author (human | agent), note, created_at
```

Schema changes are numbered migrations in `src/prax/migrations/`
(`0001_baseline` through `0007_provenance`), applied by `store.init_db` and
tracked in `PRAGMA user_version`. The vector indexes are files beside the
database, not tables (R6). (R12)

`documents.meta` is the extension point for anything a source knows that
has no column yet. Conventions in use:

| key | meaning |
|---|---|
| `source` | `"zotero"`, `"wiki"` for pages; a browser capture or inbox file later |
| `zotero.kind`, `zotero.keys`, `zotero.items`, `zotero.parent`, `zotero.modified`, … | provenance and change detection for the importer |
| `creators`, `date`, `doi`, `abstract`, `tags`, `collections`, `fields` | lifted metadata |
| `text_source` | extractor stamp of the current text artifact |
| `parse_history` | every extraction attempt: extractor, chars, seconds, outcome or error |
| `summary` | the extraction's two-sentence summary |
| `extraction`, `extraction_history` | stamp of the last extraction (extractor, ontology version, run, counts, token usage) and every earlier stamp |
| `citations` | source, work id, citation count, reference count, fetch time |
| `page` | slug, kind, current revision and author of a page |

## 7. Batch jobs and their stamps

Every batch job is idempotent because it selects by a stamp and writes a
stamp. Interrupt any of them and rerun the same command.

| Job | Selects | Writes | Guards |
|---|---|---|---|
| `import_zotero.py` | Zotero keys not in `meta.zotero.keys`, or changed `dateModified` | documents, text from Zotero's cache, `authored_by` edges | copies `zotero.sqlite`, opens read-only |
| `parse_pending.py` | `parsed_at IS NULL`, or `meta.text_source` prefix, or `--ids` | text artifact, chunks, `text_source`, `parse_history` | fallback chain; scans refused without OCR; 40 MB / 400 page caps; "seen" skip; short new text keeps the old; vision and Docling explicit only |
| `rechunk.py` | indexed documents (or legacy rows) | chunks only | none needed |
| `embed_pending.py` | chunks without a vector from the current model, then document fields without one | the two `.usearch` files, `chunk_embeddings`, `document_embeddings` | dimension check; batch 64; saves every 50 K; reconciles on start; the door must be stopped on Windows |
| `refresh_document_fields.py` | every document (or `--ids`) | `documents_fts`; drops the vector of a changed field | backfill after migration 0005 or a change to `store.document_field` |
| `extract_graph.py` | indexed documents whose `meta.extraction.ontology_version` is not current, with at least 500 characters of text | edges, `review_queue`, `meta.summary`, `meta.extraction` | sync with a budget, or Batch API (`--submit-batch` / `--collect-batch`, idempotent per document); reference-number names rejected; page/project names must be pages |
| `import_citations.py` | documents without `meta.citations`, DOIs first (`--resolve-titles` for the rest) | `cites` edges, `meta.citations` | two sources behind one flag; polite-pool contact; retries |
| `resolve_entities.py` | unmerged entities | `entities.canonical_id` | sure tier automatic; `--twins` and `--adjudicate` opt in |
| `replay_review.py` | open typed review items | edges, `review_queue.resolution` | links only what the current ontology accepts |
| `backfill_provenance.py` | edges without a producer | `edges.producer`, `edges.run` | from evidence prefixes and document stamps; idempotent |
| `eval_retrieval.py` | the query set | a report | throwaway or existing store |

Long passes run as batches of short-lived processes (`--limit N` in a
loop); the queue makes each batch do real work.

## 8. Configuration

| Variable | Effect |
|---|---|
| `PRAX_DATA_DIR` | the store directory (default `<repo>/data`) |
| `PRAX_TOKEN` | bearer token for the HTTP door; unset = loopback clients only |
| `ANTHROPIC_API_KEY` | the Claude API for extraction, vision and adjudication |
| `PRAX_EXTRACT`, `PRAX_EXTRACT_MODEL`, `PRAX_EXTRACT_EFFORT` | extractor (`stub`, `local`, or a Claude model), model and effort |
| `PRAX_LOCAL_MODEL`, `PRAX_LOCAL_CTX` | GGUF file and context for the local extractor and the local ask backend |
| `PRAX_ASK`, `PRAX_ASK_MODEL` | who answers questions: `local`, `claude`, `none` (default `local` when a local model is set, else `none`); the Claude model for `claude` (default Sonnet 5) |
| `PRAX_VISION_MODEL` | Claude model that describes images (default Sonnet 5) |
| `PRAX_CITATIONS_MAILTO` | polite-pool contact for Crossref and OpenAlex |
| `PRAX_RERANK` | cross-encoder name, `stub`, or `0` (default off) |
| `PRAX_ONTOLOGY` | alternative `ontology.yaml` |
| `PRAX_EMBED` | model name, `hash` (tests), `0` (off) |
| `PRAX_EMBED_VARIANT`, `PRAX_EMBED_PROVIDERS`, `PRAX_EMBED_THREADS` | onnxruntime precision, providers, threads |
| `PRAX_VEC_DTYPE`, `PRAX_VEC_EF` | index precision (`f16`, `i8`) and search expansion |
| `PRAX_MAX_LAYOUT_MB`, `PRAX_MAX_LAYOUT_PAGES` | caps for MuPDF layout analysis |
| `PRAX_OCR_MAX_PAGES` | page budget of the OCR extractor |
| `PRAX_PYTHON` | interpreter for the MCP server in `.mcp.json` |

## 9. Where to touch what

| I want to… | Touch |
|---|---|
| add a source | a module under `prax.importers` that calls `store.register` / `index_text` and stamps `meta.source`; a script; a fixture and tests |
| add an extractor | a `bytes -> str` function (`filename=` when `hints=True`) and an `Extractor` entry in `prax.parsers.REGISTRY`; bump `revision` when its output changes; run `parse_pending.py --upgrade <old stamp>` |
| change chunking | `prax.chunking`; run `rechunk.py --all`; the locator invariant is asserted |
| add a media kind (audio) | a chunk `kind` and locator shape in `prax.chunking`; an analyzer that produces the searchable rendering (images already go through `claude-vision`) |
| change what a document *is* for search | `store.document_field`; run `refresh_document_fields.py`, then `embed_pending.py` |
| change the embedding model | an entry in `prax.embeddings.MODELS`; `embed_pending.py` re-embeds into new index files; another dimension also needs `VEC_DIM` |
| add entity or relation types | `ontology.yaml` plus a version bump; `replay_review.py`; old edges keep their version; the bump re-selects documents for extraction |
| replace one producer's work | re-extract (a new `run`), then `store.retire_run(producer=, run=)` on the old one; history stays |
| change the extraction prompt | `extraction.system_prompt` (the JSON text is cached across calls) and `docs/eval/` for a before/after on the three benchmark papers |
| change the schema | a new `NNNN_name.sql` under `src/prax/migrations/`; never edit an applied one |
| add an agent tool | a store function first, then one handler each in `prax.api` and `prax.mcp_server`; keep responses compact |
| add a UI view | a hash route and a render function in `prax/ui/app.js`; new data needs a read endpoint on the door, never a store call from the browser |
| add a page kind | `store.PAGE_KINDS` and the `pages` view; relationships stay edges |
| change what a model sees when asked | `ask.gather` (passages, facts) and `ask.SYSTEM`; a backend is an `Answerer` with `name` and `answer(bundle)` |

## 10. Numbers as of 2026-09-11

| | |
|---|---|
| Documents | 9,236 (8,452 indexed; 9,019 PDFs, 108 web pages, 100 text files, 3 images, 1 page) |
| Archive / database / vectors | 18 GB / 1.6 GB / 784 MB + 8 MB |
| Chunks | 855,770: 772,268 text, 41,791 figure captions, 35,062 tables, 6,649 code |
| Vectors | 855,770 chunk vectors and 9,236 document vectors (bge-small, f16) |
| Graph | 49,971 live edges: 23,858 citations, 19,356 extracted, 6,756 from Zotero; 52 invalidated |
| Entities | 22,307 papers, 5,157 authors, 3,633 concepts, 2,460 methods, 2,109 claims, 950 tools, 418 venues, 146 datasets; 2,809 merged aliases |
| Extraction | 1,018 documents under ontology v3 (Sonnet 5, batch); 3,686 open review items |
| Citations | 1,280 documents resolved at Crossref (the title pass still running) |
| Retrieval eval (62 library queries) | MRR 0.82 fts, 0.79 vec, 0.89 hybrid; hit@1 0.85 hybrid |
| Costs so far | about $60 of Claude API: three-model comparison, two extraction batches, vision, adjudication |

## 11. What is not built yet

Browser capture and the inbox watcher (Stage 1), the zoetrope backfill,
extraction of the
remaining 6,900 documents (a model choice: Sonnet in batch, or a cheaper
provider after a quality trial), a typing pass for the unmapped review
items, page deletion or archiving, the move of the service onto the
serving board, and the MCP server proxying the HTTP door instead of
importing the store.
