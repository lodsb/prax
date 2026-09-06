# prax

Personal research knowledge base. Successor to the zoetrope external-disk
store, named for the praxinoscope: the zoetrope's successor, same drum,
sharper image.

One SQLite file is the canonical store (FTS5 for keyword search, sqlite-vec
for embeddings, a plain edge table for the knowledge graph). Originals
(PDFs, HTML snapshots) live in a content-addressed archive next to it. A
single FastAPI service is the only writer, and a thin FastMCP server gives
Claude `search` / `get` / `traverse` / `link` / `ingest` as tools. Target
hardware is a Raspberry Pi or N100 home server behind Tailscale.

Sources feeding it: an existing Zotero library, open browser tabs sent from
an extension, and a drop folder. See `docs/sources.md`.

## Quick start

Windows (PowerShell):

    py -3.13 -m venv .venv
    .venv\Scripts\Activate.ps1
    pip install -e ".[dev]"
    pytest

Linux / Raspberry Pi:

    python3 -m venv .venv
    . .venv/bin/activate
    pip install -e ".[dev]"
    pytest

Run the HTTP door with `uvicorn prax.api:app --reload`. Claude Code picks up
the MCP server from `.mcp.json` when you open this repository. Full
instructions in `docs/howto.md`.

## Documentation

| File | What it is |
|---|---|
| `CLAUDE.md` | Architecture invariants. Loaded into every Claude Code session. |
| `docs/PLAN.md` | Staged build plan with checklists. One stage per session. |
| `docs/rationale.md` | Decision records: what was chosen, why, and when to revisit. |
| `docs/howto.md` | Setting up, running, testing, deploying, backing up. |
| `docs/sources.md` | Data source specifications: Zotero import, browser capture, inbox. |
| `docs/research.md` | Raw landscape survey the decisions were drawn from. |

## Status

Stage 0 (prove the core) is complete: text ingest, FTS5 search, graph
link/traverse, HTTP door, MCP tools. Stage 1 is under way: numbered schema
migrations, a validated versioned ontology, and the Zotero importer with
its test fixture are in; the browser extension, inbox watcher and parse
queue are next. Checklists in `docs/PLAN.md`.
