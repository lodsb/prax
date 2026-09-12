# prax

A personal knowledge base you run yourself: one SQLite file, a
content-addressed archive of the originals, hybrid search, a small
evidence-bearing knowledge graph, a wiki of pages that are documents too,
and doors for people (a plain web UI, a browser extension) and for models
(an MCP server, an "ask" endpoint). It runs on a Pi-class board at home;
the heavy work (parsing, embedding, extraction) runs on a desktop with a
GPU, or on nothing at all when a hosted model is chosen.

The name: the praxinoscope succeeded the zoetrope, same drum, sharper
image. prax succeeds an external-disk store of the same library.

## What it is for

Taking what you read, collect and write back into your own
infrastructure, and then being able to use it: find it, ask it questions,
see how it connects, write on top of it. The first library was a
researcher's: a Zotero collection of papers, web pages, schematics,
manuals, code and notes. Nothing in the design is specific to papers,
though. What goes in is decided by the importers and the captures, what
the graph makes of it is decided by which ontology modules a document
belongs to, and those are small YAML files. Research literature, gear
manuals and datasheets, magazine articles, recipes, build logs, family
documents can live in one store, each read with its own vocabulary, and a
document may belong to several.

The models are a means, not the point, and you bring your own. Which
model does which step is a line in a config file: any GGUF file served by
llama.cpp on your own GPU, or any OpenAI-compatible server, for the pass
over everything; the Claude API for the few documents worth it; none at
all for the steps where a person or the calling model does better. The
pipeline never spends money unasked, and the extraction prompt, grammar
and schema are generated from the ontology, so a swapped model needs no
prompt work. Everything a model produces carries its provenance, so it
can be redone by a better model later without losing what the earlier
one wrote. Private material never has to leave the machine: a whole
library was read by a local model on one card, and the measurements
comparing it with the hosted one are in `docs/eval/`.

## What you can do with it

- **Bring your own models.** Run the local ones you can and pay only
  for what deserves it: extraction, titles and answers through a model
  on your own hardware, a hosted model for a promoted few, the choice
  per step in `prax.yaml` and per run on the command line.
- **Bring things in.** Import a Zotero library read-only. Drop files
  into a folder. Upload from the web UI. Send the page you are looking
  at, or every tab in the window, from the browser: a self-contained
  snapshot with its images, or the PDF fetched with your own session
  when it sits behind a login. Everything that needs a model afterwards
  (text, a proper title, the graph, vectors) happens on its own on the
  batch host, without spending money unasked.
- **Find things.** Keyword and vector search fused per document, with the
  library's own acronyms expanded, filters by document type and domain,
  similar documents, and a context column on every document: summary,
  entities, citations in and out, related documents, notes, projects.
- **Ask.** A question is answered from the best passages and what the
  graph knows about their documents, with numbered citations back to the
  chunk. The answer can be kept on a page with edges to the documents it
  rests on. From Claude Code, the same store is available as MCP tools:
  search, read, traverse, link, capture, write pages.
- **See how things connect.** A graph of typed, evidenced relations
  extracted against a small versioned ontology, grown from what the
  review queue shows the models wanted to say: papers, methods, claims,
  organizations, devices and their features and specifications, and
  whatever the next module adds. Citation edges from Crossref or
  OpenAlex. Entities resolved in tiers, from sure name variants to a
  model adjudicating likely pairs.
- **Write on top of it.** Notes on documents, project threads with
  reading lists, topic write-ups and syntheses across sources, as
  Markdown pages with revisions. A model may append to a page and never
  overwrites a person.
- **Keep it honest.** Every edge says who wrote it, from which document,
  under which ontology version, with what evidence. A misfit goes to a
  review queue, never into the graph. A document sent twice is one
  document; a wrong one is retired, not deleted, with its history kept.

## What it does, by layer

- **Ingest.** A Zotero library imported read-only; files dropped in a
  folder, uploaded, or sent from the browser (`clients/extension/`). Originals
  are archived by SHA-256; the database holds metadata and hashes only.
  Each document carries a domain set naming the ontology modules it is
  read against.
- **Parse.** A pluggable queue: PDFs through pymupdf4llm (Docling
  optional), OCR on request, HTML through trafilatura, Word documents
  (`.docx` here, `.doc`/`.rtf`/`.odt` through LibreOffice when it is
  installed), source files and code regions kept as code (by extension
  or Magika), images described and transcribed by a vision model.
- **Index.** Structure-aware chunks (text, tables, figure captions, code)
  with locators back into the original; FTS5 for keywords; bge-small
  vectors in a usearch index; a document-level field (what a document
  *is*: title, kind, summary) with its own BM25 and vectors; an acronym
  table built from the texts.
- **Search.** Reciprocal rank fusion of the rank lists per document, a
  document-type and a domain filter, similar documents by vector, and the
  context column on every document page.
- **Graph.** Typed triples extracted by a model against the composed
  ontology of a document's modules (`ontology/`: core, research, studio,
  more to come). Every edge carries confidence, evidence, the source
  document, the ontology version, and which producer wrote it in which
  run; a producer re-reading a document supersedes its own earlier
  reading; misfits go to a review queue with typing rules over it.
- **Pages.** Notes, projects, topics, syntheses: Markdown documents with
  revisions, edited by people and appended to by models.
- **Ask.** Hybrid search, one passage per document plus the graph's
  facts, a model of your choosing, citations resolved to chunks.
- **Pipeline and jobs.** The inbox watcher takes every capture the rest
  of the way (parse, titles, extract, embed) with a local model; every
  batch pass is a job the UI shows; the UI follows changes in the store.
- **Models are configuration.** `prax.yaml` names models (Claude, an
  OpenAI-compatible server such as llama-server, a GGUF file in process)
  and assigns one to each step: extraction, promote, ask, titles, vision,
  adjudication.
- **Doors.** A FastAPI service with a bearer token, a static web UI
  (search, ask, browse, document, graph, review, pages, promote, inbox,
  jobs), a Manifest V3 browser extension, and a thin MCP proxy of the door so
  Claude Code can work in the same store.

## State

Built and in daily use on one library, September 2026:

| | |
|---|---|
| Documents | 9,538 (9,186 PDFs, 241 web pages, 102 text files, images, one wiki page); 8,752 with text; 302 came in through the drop folder, the UI or the browser extension |
| Chunks and vectors | 876,000 chunks, all with vectors; 9,534 document vectors; 5,887 acronyms |
| Graph | 124,000 live edges: 67,900 by a local model, 19,400 by Sonnet 5, 25,600 citations, 6,800 from Zotero, 4,100 by typing rules |
| Entities | 38,200 papers, 22,100 concepts, 12,600 methods, 8,500 authors, 5,500 tools, 3,400 claims, 1,300 venues, 700 organizations; 6,900 merged aliases |
| Ontology | core, research and studio modules; 9,517 documents in the research domain, 18 in studio, 2 in both |
| Retrieval, 62 queries over the library | MRR 0.905 hybrid (0.82 keyword, 0.79 vector); hit@1 0.85 |
| Extraction | every document with text read once by a local Qwen3.6-35B-A3B on an RTX 4090 or by Sonnet 5; the re-read under the current ontology is under way |
| Review queue | 21,900 open items, the evidence the next ontology change is drawn from |

Not built: the move of the service onto the serving board with the
desktop draining model work through the door, and the MCP server
proxying the HTTP door instead of importing the store; the browser
extension is hand-tested in Firefox and Waterfox so far. Checklists with
dates and the planned passes: `docs/PLAN.md`.

## Quick start

    python -m venv .venv
    . .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
    pip install -e ".[dev,embed,ingest]"
    pytest

    export PRAX_DATA_DIR=/path/to/store      # Windows: $env:PRAX_DATA_DIR = "D:\prax-data"
    prax serve                               # http://127.0.0.1:8000/ui/
    prax status                              # what the library holds

An empty store answers on the first request; migrations run on connect.
Then import a Zotero library (`scripts/import_zotero.py`), parse
(`scripts/parse_pending.py`), embed (`scripts/embed_pending.py`), and
extract (`scripts/extract_graph.py`), in that order; each script has a
dry run. Or skip the library: drop files into the store's `inbox/`
folder, `prax add <file, folder or URL>`, upload in the UI, or install
the browser extension, and run `prax work --watch` on the machine with
the models to take them the rest of the way (it talks to the door, never
to the database). Copy `prax.example.yaml` to the store directory as `prax.yaml`
to say which model does which step; extraction, image description and
adjudication with Claude need `ANTHROPIC_API_KEY`. Extras: `serve`
(the door on the board: vectors for hybrid search), `work` (the worker:
parsing, code detection, vectors), `docling` (a heavier PDF extractor);
a local model runs in llama-server, never in prax's own process. Claude Code picks up the
MCP server from `.mcp.json` when it opens this repository; the server
is a proxy, so the door has to be running (`PRAX_DOOR`, default the
local one). The clients — the `prax` command and the browser extension —
live in `clients/`. Every step, with the commands: `docs/howto.md`.

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
shaped by one library and one set of machines (a Windows desktop with
an RTX 4090 for batch work, an 8 GB Arm board as the serving target). It is
published so the design and the measurements can be read and reused, not
as a packaged product: there is no installer, no multi-user story, and
the defaults reflect that library. Issues and pull requests are welcome
but may wait.

## License

MIT, see `LICENSE`, except the browser extension: `clients/extension/` is
AGPL-3.0 (`clients/extension/LICENSE`) because it bundles SingleFile for
page snapshots,
the way the Zotero connector does; it is a separate program talking to
the server over HTTP. The test fixture under `tests/fixtures/` holds open-access papers under their own Creative Commons terms; `tests/fixtures/zotero/README.md` lists them with their licenses.
