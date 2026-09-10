# prax

Personal research knowledge base. Successor to the zoetrope external-disk
store, named for the praxinoscope: the zoetrope's successor, same drum,
sharper image.

One SQLite file is the canonical store: FTS5 for keyword search, a
document-level retrieval field, a plain edge table for the knowledge
graph, and the bookkeeping for two usearch vector files (chunks and
documents) that sit next to it. Originals (PDFs, HTML snapshots, images,
source files) live in a content-addressed archive. Everything that changes
the store goes through one module, `prax.store`; a FastAPI service and a
thin FastMCP server are its doors, and a plain web UI is a client of the
HTTP door. Target hardware for serving is a Pi-class board behind
Tailscale; the batch jobs run on a desktop.

What the library holds after import, parsing, embedding, extraction and
resolution (September 2026): 9,236 documents, 856,000 chunks with vectors,
a graph of 50,000 edges over 37,000 entities (papers, authors, concepts,
methods, claims, tools, venues, datasets), a citation network from
Crossref, and a wiki of pages that are documents too. Numbers and the
picture of the whole: `docs/architecture.md`.

## Quick start

Windows (PowerShell):

    py -3.13 -m venv .venv
    .venv\Scripts\Activate.ps1
    pip install -e ".[dev,embed,ingest]"
    pytest

Linux / Raspberry Pi:

    python3 -m venv .venv
    . .venv/bin/activate
    pip install -e ".[dev,embed,ingest]"
    pytest

Run the HTTP door and the UI:

    $env:PRAX_DATA_DIR = "C:\prax-data"      # or any directory
    uvicorn prax.api:app --port 8000          # http://127.0.0.1:8000/ui/

Claude Code picks up the MCP server from `.mcp.json` when you open this
repository. Extras: `embed` (vectors), `ingest` (PDF, HTML, code
detection), `local` (a GGUF model in process), `docling` (a heavier PDF
extractor). Graph extraction, image description and entity adjudication
call the Claude API and need `ANTHROPIC_API_KEY`. Full instructions in
`docs/howto.md`.

## Documentation

| File | What it is |
|---|---|
| `CLAUDE.md` | Architecture invariants and conventions. Loaded into every Claude Code session. |
| `docs/architecture.md` | The system as built: hosts, life of a document and of a query, module map, data model, batch jobs, where to touch what, numbers. |
| `docs/howto.md` | Setting up, running each batch job, the doors, the UI, backup. |
| `docs/rationale.md` | Decision records R1–R15: what was chosen, why, what was measured, when to revisit. |
| `docs/ui.md` | The web UI: endpoints it uses, routes, rules. |
| `docs/ontology-v2.md` | How the ontology grew from the review queue's evidence. |
| `docs/PLAN.md` | Staged build plan with checklists and dates. |
| `docs/sources.md` | Data sources: the Zotero import, citation sources, browser capture and inbox (planned). |
| `docs/eval/` | Measurements: extractors, retrieval on the fixture and the library, the local LLM, the document field. |
| `docs/research.md` | The raw landscape survey the first decisions were drawn from. |

## Status

Stages 0 to 3 are built: the store with numbered migrations and a
versioned ontology (v3), the Zotero importer, a pluggable parse queue
(PDF, HTML, OCR on request, code by extension or Magika, images described
by Claude vision), structure-aware chunks, hybrid retrieval fusing chunk
and document-level BM25 and vectors (MRR 0.89 on the library query set),
Claude extraction into an evidence-bearing graph with a review queue and
ontology replay, entity resolution in three tiers, a citation network, a
web UI with search, ask, document, context, graph, review and pages
views, a bearer-token door, and an optional local llama.cpp path that
extracts and answers questions ("ask": passages plus graph facts to a
local model, Claude, or the MCP client; answers cite and can be kept on
a page). Not built: browser capture and the inbox watcher, the move
onto the serving board. Checklists in `docs/PLAN.md`.
