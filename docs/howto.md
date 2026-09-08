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
| `embed` | sqlite-vec, onnxruntime, tokenizers, huggingface_hub | Stage 2; the desktop (GPU via onnxruntime-directml) and the serving host |
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

## 3d. Embeddings and hybrid search

`prax.embeddings` runs bge-small-en-v1.5 (384-d) through onnxruntime;
model files download from the Hugging Face hub on first use. Vectors live
in `<data dir>/vectors-<model>.usearch` (a memory-mapped HNSW index,
`prax.vectors`), bookkeeping in `chunk_embeddings`. Search is hybrid by
default and falls back to FTS when there is no index file, no usearch or
`PRAX_EMBED=0`.

    pip install -e ".[embed]"
    # Windows desktop with a GPU: DirectML instead of the CPU runtime
    pip uninstall -y onnxruntime; pip install onnxruntime-directml

    python scripts/embed_pending.py --dry-run     # counts
    python scripts/embed_pending.py --batch 64    # everything pending; idempotent
    python scripts/embed_pending.py --compact     # after re-parsing: drop stale keys

The job saves the index every 50,000 chunks and reconciles index and
bookkeeping on start, so an interrupted run is simply started again. Copy
the `.usearch` file together with `prax.db` when moving the store. A store
that still has the Stage 2 `chunks_vec` table loses it on the next
`init_db` (sqlite-vec must still be importable for that); run `VACUUM`
once afterwards to reclaim about 1.3 GB.

Settings: `PRAX_EMBED` (model name, `hash` for tests, `0` off),
`PRAX_EMBED_VARIANT` (`fp32` on a GPU, `int8` on CPU by default),
`PRAX_EMBED_PROVIDERS`, `PRAX_EMBED_THREADS`; `PRAX_VEC_DTYPE` (`f16`
default, `i8` for half the file at recall 0.93) and `PRAX_VEC_EF` (search
expansion, 64) for the index. Changing the model means re-embedding into
a new file: `chunk_embeddings.model` records what each vector came from
and `embed_pending.py` picks up the difference.

Query: `search(q, mode="hybrid"|"fts"|"vec", kind=...)` in the store, the
API (`/search?mode=`) and the MCP tool. Hybrid hits carry `score` (RRF),
`fts_rank` and `vec_rank`.

## 3e. Graph extraction (Stage 3)

`prax.extraction` sends each document's metadata header and the first
12,000 characters of its text to Claude with a JSON schema generated from
`ontology.yaml`, and writes the returned triples through `store.link` with
a confidence and a quoted evidence string. Triples that do not fit the
ontology go to `review_queue`; the summary goes to `meta.summary`; the
document is stamped with the ontology version so reruns are incremental.

    # credentials: ANTHROPIC_API_KEY, or `ant auth login`
    python scripts/extract_graph.py --dry-run             # selection and cost estimate
    python scripts/extract_graph.py --limit 20            # trial, synchronous
    python scripts/extract_graph.py --submit-batch        # whole selection at half price
    python scripts/extract_graph.py --collect-batch <id>  # apply when the batch has ended

Settings: `PRAX_EXTRACT_MODEL` (default `claude-opus-5`),
`PRAX_EXTRACT_EFFORT` (default `medium`), `PRAX_EXTRACT=stub` for tests,
`PRAX_EXTRACT=local` with `PRAX_LOCAL_MODEL=<model.gguf>` for a model on
this machine (section 3f; `PRAX_LOCAL_CTX`, default 8192). The local path
asks for tab-separated lines instead of JSON under a grammar that bounds
the output to 20 triples (`prax.lineformat`); the extractor name stamped
on documents is `local:<model file>` and its cost is zero.
Bumping the ontology version re-selects every document. Review the queue
with `store.list_review` (a UI view is planned) and close items with
`resolve_review`.

### Entity resolution

    python scripts/resolve_entities.py --dry-run               # both tiers listed
    python scripts/resolve_entities.py --commit --no-embed     # sure merges only
    python scripts/resolve_entities.py --commit --adjudicate   # Claude decides the rest

Sure merges are equal names after normalization (case, accents,
punctuation, plural, suffixes) and author initials forms that abbreviate
exactly one full name; they need no model. Likely merges are close by
name embedding, for concepts, methods, tools, datasets and venues only,
and merge nothing unless an adjudicator says yes. A merge sets
`entities.canonical_id`; nothing is deleted, `traverse` and the UI follow
the pointer, and undoing one is clearing that column.

## 3f. Local models (optional)

A GGUF model can run in-process through `llama-cpp-python` on the batch
host, for extraction of new documents without the API, private material,
and the planned "ask" feature. Measured on the desktop's GTX 1070:
`docs/eval/local-llm-2026-09-08.md` (Qwen2.5-7B-Instruct Q4_K_M, 5.2 GB
VRAM, valid schema-constrained extractions at 80 s per document).

    # CPU build (any host)
    pip install -e ".[local]"
    # CUDA 12 wheel on Windows or Linux with an NVIDIA driver >= 525
    pip install -e ".[local]" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
    pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12   # Windows: the DLLs the wheel needs
    python -c "from prax import local_llm; print(local_llm.llama_class())"

Models are GGUF files, e.g. from the Hugging Face cache:

    python -c "from huggingface_hub import hf_hub_download as d; print(d('bartowski/Qwen2.5-7B-Instruct-GGUF', 'Qwen2.5-7B-Instruct-Q4_K_M.gguf'))"
    python scripts/bench_local_llm.py <path.gguf> --docs 3

Always go through `prax.local_llm.llama_class()` rather than importing
`llama_cpp` directly: on Windows the wheel's loader only searches `PATH`,
and that function puts the venv's `nvidia/*/bin` and `llama_cpp/lib`
folders there first. A stale `CUDA_PATH` (this desktop has a 10.2 toolkit)
does no harm. An 8 GB card fits a 7–8B model at Q4 with an 8 K context;
the Q6A has no usable GPU and would run a 3B model at a few tokens per
second, which is why the API stays the default there.

## 4. Running the HTTP door

    uvicorn prax.api:app --reload --port 8000

### Access

The door checks one shared secret, `PRAX_TOKEN`, on every request except
the UI's static files and `/health`. Generate one and set it in the
service's environment:

    python -c "import secrets; print(secrets.token_urlsafe(32))"
    $env:PRAX_TOKEN = "<the token>"          # PowerShell
    export PRAX_TOKEN=<the token>             # shell

Scripts and the extension send `Authorization: Bearer <token>`. The UI
asks for the token once and exchanges it for an HttpOnly session cookie
(`POST /session`, 30 days, `DELETE /session` to end it), so links to
originals work in new tabs. Without `PRAX_TOKEN` the door admits
loopback clients only, which is what a development server needs and what
keeps a misconfigured deployment closed. Bind the service to the
Tailscale address (`--host 100.x.y.z`) on the serving host; the MCP
server over stdio needs no token.

Endpoints:

| Method | Path | Body / params | Returns |
|---|---|---|---|
| POST | `/ingest` | JSON `{text, title?, source_url?}` | `{doc_id, hash, created}` |
| POST | `/ingest/file` | multipart `file`, form `title?`, `source_url?` | `{doc_id, hash, created}` |
| GET | `/get/{doc_id}` | `offset?`, `max_chars?` | document row plus text |
| GET | `/search` | `q`, `limit?`, `kind?`, `mode?` | list of `{chunk_id, doc_id, title, snippet, score, kind, heading, page}` |
| GET | `/chunk/{chunk_id}` | | one chunk: text, kind, heading, locator, table `data` |
| POST | `/link` | JSON `{src, src_type, rel, dst, dst_type, confidence?, source_doc?}` | `{edge_id}` |
| GET | `/traverse` | `entity`, `hops?` (max 2) | list of edges with types and hop distance |

The web UI is served by the same process at `http://127.0.0.1:8000/ui/`
(`/` redirects there): search, document and browse views; the graph view
follows Stage 3. It needs no build step; the files live in `src/prax/ui/`.

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
