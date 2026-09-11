# prax

A personal research knowledge base: one SQLite file, a content-addressed
archive of the originals, hybrid search, a small evidence-bearing
knowledge graph, a wiki of pages that are documents too, and doors for
people (a plain web UI) and for models (an MCP server, an "ask" endpoint).
Built for one person's library of papers, web snapshots, schematics, code
and notes, to run on a Pi-class board at home; batch work (parsing,
embedding, extraction) runs on a desktop with a GPU.

The name: the praxinoscope succeeded the zoetrope, same drum, sharper
image. prax succeeds an external-disk store of the same library.

## What it does

- **Ingest.** A Zotero library imported read-only; files dropped in by
  hand or through the HTTP door. Originals are archived by SHA-256; the
  database holds metadata and hashes only.
- **Parse.** A pluggable queue: PDFs through pymupdf4llm (Docling
  optional), OCR on request, HTML through trafilatura, source files and
  code regions kept as code (by extension or Magika), images described
  and transcribed by a vision model.
- **Index.** Structure-aware chunks (text, tables, figure captions, code)
  with locators back into the original; FTS5 for keywords; bge-small
  vectors in a usearch index; a document-level field (what a document
  *is*: title, kind, summary) with its own BM25 and vectors.
- **Search.** Reciprocal rank fusion of four rank lists per document, a
  document-type filter, similar documents by vector, and a context column
  on every document page: summary, entities, citations in and out, related
  documents, notes, project membership.
- **Graph.** Typed triples extracted by a model against a small versioned
  ontology (papers, authors, concepts, methods, claims, tools, datasets,
  venues, pages, projects). Every edge carries confidence, evidence, the
  source document, the ontology version, and which producer wrote it in
  which run; misfits go to a review queue, never into the graph. Citation
  edges come from Crossref or OpenAlex. Entities are resolved in three
  tiers, from sure name variants to a model adjudicating likely pairs.
- **Pages.** Notes on documents, project threads with reading lists, topic
  write-ups and syntheses across sources, as Markdown documents with
  revisions. A model may append to a page and never overwrites a person.
- **Ask.** A question goes to the hybrid search; the best passage per
  document plus what the graph records about those documents goes to a
  model, which answers with numbered citations. The answer can be kept on
  a page with edges to the documents it rests on.
- **Models are configuration.** `prax.yaml` names models (Claude, a GGUF
  file in process through llama.cpp, or any OpenAI-compatible server) and
  assigns one to each AI step: extraction, ask, titles, vision,
  adjudication. Private material can stay on the machine; the API is used
  where it is worth it.
- **Doors.** A FastAPI service with a bearer token, a static web UI
  (search, ask, browse, document, graph, review, pages), and a thin
  FastMCP server so Claude Code can search, read, traverse, link, and
  write pages in the same store.

## State

Built and in daily use on one library, September 2026:

| | |
|---|---|
| Documents | 9,236 (9,019 PDFs, 108 web pages, 100 text files, images, one wiki page); 8,452 with text |
| Chunks and vectors | 856,000 chunks, all with vectors; 9,236 document vectors |
| Graph | 110,000 live edges: 77,000 extracted (local model and Sonnet), 25,600 citations, 6,800 from Zotero |
| Entities | 34,000 papers, 16,200 concepts, 9,700 methods, 7,000 authors, 4,100 tools, 3,000 claims; 6,900 merged aliases |
| Retrieval, 62 queries over the library | MRR 0.89 hybrid (0.82 keyword, 0.79 vector); hit@1 0.85 |
| Titles | 3,771 file-name titles replaced by a local 7B model reading the first page |
| Extraction | every document with text read once: 6,878 by a local Qwen3.6-35B-A3B on an RTX 4090 (8 hours), 1,019 by Sonnet 5 |

Not built: the move of the service onto the serving board, and the MCP
server proxying the HTTP door instead of importing the store. The browser
extension (`extension/`) is new and hand-tested in one browser so far. Checklists with dates: `docs/PLAN.md`.

## Quick start

    python -m venv .venv
    . .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
    pip install -e ".[dev,embed,ingest]"
    pytest

    export PRAX_DATA_DIR=/path/to/store      # Windows: $env:PRAX_DATA_DIR = "D:\prax-data"
    uvicorn prax.api:app --port 8000         # http://127.0.0.1:8000/ui/

An empty store answers on the first request; migrations run on connect.
Then import a Zotero library (`scripts/import_zotero.py`), parse
(`scripts/parse_pending.py`), embed (`scripts/embed_pending.py`), and
extract (`scripts/extract_graph.py`), in that order; each script has a
dry run. Copy `prax.example.yaml` to the store directory as `prax.yaml`
to say which model does which step; extraction, image description and
adjudication with Claude need `ANTHROPIC_API_KEY`. Extras: `embed`
(vectors), `ingest` (PDF, HTML, code detection), `local` (a GGUF model in
process), `docling` (a heavier PDF extractor). Claude Code picks up the
MCP server from `.mcp.json` when it opens this repository. Every step,
with the commands: `docs/howto.md`.

## Design

Ten invariants in `CLAUDE.md` hold the shape: SQLite is the canonical
store and the only database; files are content-addressed; every mutation
goes through one module; one writer; a thin MCP proxy; agent-shaped
endpoints that return snippets and ids, never whole documents; nothing in
the serving path that needs more than a gigabyte of memory; edges are
evidence with provenance, never truth; a small versioned ontology;
importers never write to their source. The reasoning behind each, with
what was measured and when to revisit: `docs/rationale.md`. The system as
built, module by module: `docs/architecture.md`.

## Documentation

| File | What it is |
|---|---|
| `CLAUDE.md` | Architecture invariants and conventions. Loaded into every Claude Code session. |
| `docs/architecture.md` | The system as built: hosts, life of a document and of a query, module map, data model, batch jobs, configuration, where to touch what, numbers. |
| `docs/howto.md` | Setting up, every batch job, `prax.yaml`, the doors, the UI, backup. |
| `docs/rationale.md` | Decision records R1 to R16: what was chosen, why, what was measured, when to revisit. |
| `docs/ui.md` | The web UI: endpoints it uses, routes, rules. |
| `docs/ontology-v2.md`, `docs/ontology-v4.md`, `docs/ontology-v5.md`, `docs/ontology-studio.md` | How the ontology grew: from the review queue's evidence, for syntheses, for organizations and mentions, and the studio module for gear and its manuals. |
| `docs/PLAN.md` | Staged build plan with checklists and dates. |
| `docs/sources.md` | Data sources: the Zotero import, citation sources, captures and the drop folder. |
| `docs/extension.md` | The browser extension: installing it, what it sends, its settings (server, token, domains), how it authenticates. |
| `docs/eval/` | Measurements: extractors, retrieval on the fixture and the library, the local LLM, the document field. |
| `docs/research.md` | The landscape survey the first decisions were drawn from. |
| `prax.example.yaml` | Template for `prax.yaml`: models and the step each serves. |

## Scope and status of the project

This is one person's tool, built with Claude Code over a few weeks and
shaped by one library and one set of machines (a Windows desktop with a
GTX 1070 for batch work, an 8 GB Arm board as the serving target). It is
published so the design and the measurements can be read and reused, not
as a packaged product: there is no installer, no multi-user story, and
the defaults reflect that library. Issues and pull requests are welcome
but may wait.

## License

MIT, see `LICENSE`, except the browser extension: `extension/` is AGPL-3.0
(`extension/LICENSE`) because it bundles SingleFile for page snapshots,
the way the Zotero connector does; it is a separate program talking to
the server over HTTP. The test fixture under `tests/fixtures/` holds open-access papers under their own Creative Commons terms; `tests/fixtures/zotero/README.md` lists them with their licenses.
