# prax build plan

Work one stage per Claude Code session. Each stage ends green: tests pass,
`ruff` clean, and the stage's checklist fully ticked before moving on.
Decisions behind the stages: `docs/rationale.md`. Source details:
`docs/sources.md`.

## Stage 0 — Prove the core

Goal: text in, search and graph out, reachable from Claude Code. No parsers,
no embeddings, no real sources yet; those come in Stage 1 and 2 once this
path is proven.

- [ ] `prax.store`: schema init from `schema.sql`, WAL on, one process-wide
      lock, connection usable from worker threads (FastAPI and FastMCP both
      run sync handlers off the main thread)
- [ ] Two-step ingest: `register` archives the original bytes and inserts
      the document row (hash = sha256 of the original); `index_text` stores
      the parsed text as its own content-addressed artifact, chunks it
      (~1000-char windows, overlap 150) into `chunks` + FTS5, and stamps
      `parsed_at`. `ingest_text` composes both for plain text.
- [ ] `get` reads the text artifact (never re-joins overlapping chunks) and
      accepts `offset` / `max_chars`
- [ ] `search` (FTS5-only): the store builds the MATCH expression from the
      user string; punctuation and operators in a query never raise
- [ ] `link`, `traverse` (recursive CTE, max 2 hops, both edge endpoints
      within the hop limit, valid edges only, result rows carry entity
      types and hop distance)
- [ ] FastAPI: `POST /ingest` (text), `POST /ingest/file` (multipart),
      `GET /get/{id}`, `GET /search`, `POST /link`, `GET /traverse`
- [ ] FastMCP over stdio: `search`, `get`, `traverse`, `link`, `ingest`,
      `ingest_file` (server-local path); no connection opened at import time
- [ ] pytest, all on `tmp_path`: ingest → search, link → traverse, duplicate
      ingest is a no-op, register-without-text then index, plus regression
      tests for the four skeleton bugs (thread affinity, FTS syntax crash,
      traverse off-by-one, chunk reassembly), API through TestClient, MCP
      through the in-process client
- [ ] `.mcp.json` points at the venv interpreter (relative path,
      `PRAX_PYTHON` override); tools respond from Claude Code

## Stage 1 — Real sources and parsing

Goal: the existing Zotero library and live browser tabs flow into prax.
Parsing is a batch job; the serving path never parses.

- [ ] Zotero importer, `scripts/import_zotero.py`: works on a copy of
      `zotero.sqlite` opened read-only; `--dry-run` prints an inventory
      (items by type, attachments by link mode, missing files, duplicate
      hashes) before anything is written. Maps items → documents with
      Zotero key, item type, creators, date, DOI, URL, abstract, tags and
      collection paths in `meta`; archives attachments by hash; imports
      notes as text documents. Idempotent on re-run.
- [ ] Zotero test fixture: a handful of items with small PDFs and notes,
      exported from the real library, under `tests/fixtures/zotero/`
- [ ] Parse queue, `scripts/parse_pending.py`: every document with
      `parsed_at IS NULL` is parsed by MIME type (Docling for PDF,
      trafilatura for HTML, plain read for text) and handed to `index_text`.
      Runs on the N100 or a laptop, not the Pi.
- [ ] Browser capture: a Manifest V3 extension (Chrome and Firefox) with
      "send this tab" and "send all tabs in window". Posts URL, title and the
      rendered DOM to `POST /ingest/html`; a URL-only `POST /ingest/url`
      fallback fetches server-side. Captures tagged with a capture-session
      id. Bearer token, reachable only over Tailscale.
- [ ] Inbox folder watcher: files dropped in `data/inbox/` are registered
      and queued for parsing
- [ ] Backfill of the old zoetrope disk: the hash inventory in
      `scripts/backfill.py` gains a `--commit` mode that registers files
      through the store; review the dedupe report first
- [ ] Zotero-derived graph seeds: `authored_by` edges from creators, with
      `confidence = EXTRACTED` and `source_doc` set

## Stage 2 — Hybrid retrieval

- [ ] Embedding batch job: quantized bge-small ONNX (384-dim) → `chunks_vec`
      (sqlite-vec; extension loading verified on the dev machine)
- [ ] `search` becomes hybrid: FTS5 + vec in parallel, RRF fusion
- [ ] Optional cross-encoder rerank behind a flag; benchmark on the Pi
- [ ] Eval harness: ~20 hand-written queries against the Zotero fixture with
      expected docs, tracked in `tests/eval/`

## Stage 3 — Graph enrichment

- [ ] `ontology.yaml` v1 (entity + relation types; keep it under ~10 each)
- [ ] Nightly extraction job (Claude API): triples against ontology,
      confidence-tagged, `source_doc` + `ontology_version` stamped
- [ ] Entity resolution pass (embedding candidates → LLM adjudication);
      merges recorded via `entities.canonical_id`; `traverse` follows
      canonical ids
- [ ] Edge invalidation on contradiction (set `valid_to`, insert successor)
- [ ] MCP `traverse` surfaces confidence + provenance in results

## Later / maybe

- Streamable-HTTP MCP transport for remote access over Tailscale, and the
  MCP server proxying the HTTP door instead of importing the store
- Litestream replication of `data/prax.db`
- Kùzu migration script (only if the entity threshold is crossed)
- Complement / "blast-radius" SQL tools exposed via MCP
- Karakeep or Linkwarden as an additional capture front-end feeding the inbox
