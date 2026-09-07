# How-to

Practical steps for developing, running, testing, and deploying prax. What
is described here works today unless a section is marked *planned*.

## 1. Development environment

The repo expects a virtualenv at `.venv` in the project root. `.mcp.json`
points at it.

Windows (PowerShell). Bare `python` is usually the Microsoft Store stub;
use the `py` launcher:

    py -3.13 -m venv .venv
    .venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    pip install -e ".[dev]"

Linux, macOS, Raspberry Pi:

    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install --upgrade pip
    pip install -e ".[dev]"

Extras, install only where they run (see `rationale.md` R8):

| Extra | Contents | Where |
|---|---|---|
| `dev` | pytest, ruff, httpx | every dev checkout |
| `embed` | sqlite-vec, onnxruntime | Stage 2; the batch host and the Pi |
| `ingest` | pymupdf4llm, trafilatura | Stage 1 parse queue; the desktop |
| `docling` | docling (about 3 GB with PyTorch) | optional; only for `--extractor docling` |

Check the installed FastMCP major version after upgrades; the code targets
the 4.x line:

    python -c "import fastmcp; print(fastmcp.__version__)"

## 2. Tests and lint

    python -m pytest
    ruff check src tests

Tests never touch `data/`. Every fixture uses `tmp_path` and points
`PRAX_DATA_DIR` at it before importing the API or MCP modules.

## 3. Data directory

Layout, all under one directory:

    data/
      prax.db            SQLite (WAL); the canonical store
      prax.db-wal, -shm  WAL sidecar files; copy them with the db
      archive/<xx>/<sha256>   originals and parsed-text artifacts
      inbox/             drop folder (planned, Stage 1)

The location defaults to `<repo>/data` and is overridden with the
`PRAX_DATA_DIR` environment variable. On the Pi point it at the SSD.

### Schema migrations

The schema lives in `src/prax/migrations/NNNN_name.sql`. `store.init_db`
(called by the API, the MCP server and every script) applies the files
whose number is above the database's `PRAGMA user_version`, each in its own
transaction, and stamps the version. To change the schema, add the next
numbered file; never edit one that has been applied. A database created
before migrations existed (version 0) is upgraded in place.

### Ontology

`ontology.yaml` lists the entity and relation types the graph accepts and a
`version`. `store.link` rejects anything else. Add types and bump the
version; edges keep the version they were written under. `PRAX_ONTOLOGY`
points at a different file (tests use it).

## 3a. Importing the Zotero library

The importer never opens the live `zotero.sqlite`; it copies the file into
`<data dir>/zotero-import/` and opens the copy read-only. Everything goes
through `prax.store`. Details of the mapping: `docs/sources.md` §1.

    # read-only census; nothing is written. --hash adds sha256 dedupe (reads every file)
    python scripts/import_zotero.py R:/Zotero --dry-run
    # trial run, then the whole library. Re-runs skip what is already imported.
    $env:PRAX_DATA_DIR = "C:\prax-data"
    python scripts/import_zotero.py R:/Zotero --commit --limit 500
    python scripts/import_zotero.py R:/Zotero --commit --quiet

The test fixture in `tests/fixtures/zotero/` is regenerated with
`scripts/make_zotero_fixture.py` (the exact command is in its docstring).

## 3b. Parse queue

Extractors live in `prax.parsers` (see its docstring for the table) and are
picked by MIME type in registry order, falling back to the next one when
one raises. The queue records every attempt in `meta.parse_history` and the
winner in `meta.text_source` as `<name>/<version>`, so any pass can be
redone later with `--upgrade <prefix>`. An upgrade keeps the old text when
the new one is suspiciously short (login walls, scans without OCR) unless
`--force` is given. Runs on the desktop, never on the serving host.

    # documents never indexed (registered by an importer or the inbox)
    python scripts/parse_pending.py --pending
    # re-extract everything Zotero's cache produced, HTML first
    python scripts/parse_pending.py --upgrade zotero-ft-cache --mime text/html
    # scans: OCR is explicit and bounded (PRAX_OCR_MAX_PAGES, default 60)
    python scripts/parse_pending.py --pending --extractor pymupdf4llm-ocr
    # Docling on a hand-picked set
    python scripts/parse_pending.py --ids 12 34 --extractor docling --force

Before switching the default extractor for a document class, run
`scripts/compare_extractors.py` over a sample and read the texts, not only
the metrics table it writes.

Long passes over thousands of PDFs should run as a loop of short-lived
processes: MuPDF's layout analysis grows the process over time, and one
run over 5,000 documents was killed for memory. Every invocation
re-selects what is left, so batching costs nothing:

    for ($i = 0; $i -lt 40; $i++) {
      python scripts/parse_pending.py --upgrade zotero-ft-cache --limit 250 --quiet
    }

Originals above `PRAX_MAX_LAYOUT_MB` (default 40) skip layout analysis and
get plain text through the fallback.

## 3c. Chunks

`prax.chunking` turns each text artifact into structure-aware chunks
(rationale R13): sections of paragraphs, whole tables with their caption
and a parsed grid, figure captions, code blocks, each with a heading path
and a locator (character range and page). Search hits carry `kind`,
`heading` and `page`; `kind="table"` narrows to tables. After changing the
chunker, or after a migration that added chunk columns:

    python scripts/rechunk.py --all       # every indexed document
    python scripts/rechunk.py --legacy    # only rows that have no kind yet

Chunks are disposable; nothing else is touched.

## 4. Running the HTTP door

    uvicorn prax.api:app --reload --port 8000

Endpoints:

| Method | Path | Body / params | Returns |
|---|---|---|---|
| POST | `/ingest` | JSON `{text, title?, source_url?}` | `{doc_id, hash, created}` |
| POST | `/ingest/file` | multipart `file`, form `title?`, `source_url?` | `{doc_id, hash, created}` |
| GET | `/get/{doc_id}` | `offset?`, `max_chars?` | document row plus text |
| GET | `/search` | `q`, `limit?`, `kind?` | list of `{chunk_id, doc_id, title, snippet, score, kind, heading, page}` |
| GET | `/chunk/{chunk_id}` | | one chunk: text, kind, heading, locator, table `data` |
| POST | `/link` | JSON `{src, src_type, rel, dst, dst_type, confidence?, source_doc?}` | `{edge_id}` |
| GET | `/traverse` | `entity`, `hops?` (max 2) | list of edges with types and hop distance |

A first smoke run from PowerShell:

    Invoke-RestMethod -Method Post http://127.0.0.1:8000/ingest `
      -ContentType application/json `
      -Body '{"text": "Granular synthesis smears transients.", "title": "note"}'
    Invoke-RestMethod "http://127.0.0.1:8000/search?q=granular"

## 5. MCP server in Claude Code

`.mcp.json` in the repo root registers the server. Claude Code runs the
command with the project root as the working directory, so the interpreter
path is relative:

    "command": "${PRAX_PYTHON:-.venv/Scripts/python.exe}"

On Linux or macOS set `PRAX_PYTHON=.venv/bin/python` in the shell that
launches Claude Code. Claude Code reads `.mcp.json` at startup only, so
restart the session after editing it. The first time it sees the server it
asks whether to trust it; if that prompt was dismissed, run
`claude mcp reset-project-choices`.

Inside a session, `/mcp` shows connection state. The tools are `search`,
`get`, `traverse`, `link`, `ingest`, and `ingest_file`. The server can also
be run by hand to check it starts:

    python -m prax.mcp_server

It speaks MCP over stdio, so it will sit waiting for input; Ctrl+C ends it.

## 6. Deployment on the Pi / N100 (*planned*)

The shape, to be finalized in Stage 1:

- `data/` on the external SSD, `PRAX_DATA_DIR` set in the service
  environment.
- `uvicorn prax.api:app --host 100.x.y.z --port 8000` bound to the
  Tailscale address only, run from a systemd unit.
- Parsing and embedding jobs scheduled with systemd timers on the batch
  host; they share the same `data/` over the network or the file is copied
  back after each run.
- The MCP server on the Pi will use the streamable-HTTP transport and proxy
  the HTTP door (`rationale.md` R5).

## 7. Backup and moving the store

Everything is two things: one SQLite file and one directory of
content-addressed files.

- Database: use SQLite's online backup so the WAL is folded in, then copy
  the result.

      python -c "import sqlite3; s=sqlite3.connect('data/prax.db'); d=sqlite3.connect('backup.db'); s.backup(d)"

- Archive: `rsync -a data/archive/ backup/archive/`. Files are immutable
  and named by hash, so an interrupted copy can simply be resumed.

Litestream replication is on the later list.

## 8. Adding a source

Every source is a client of `prax.store`. The pattern (see `sources.md`):

1. Obtain original bytes and whatever metadata the source has.
2. `register(...)`: archive, insert the row. Dedupe is automatic by hash.
3. If text is already available, call `index_text(...)`. Otherwise leave
   `parsed_at` NULL and let the parse queue pick it up.
4. Seed graph edges from metadata with `link(...)`, `confidence="EXTRACTED"`
   and `source_doc` set.
