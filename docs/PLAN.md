# prax build plan

Work one stage per Claude Code session. Each stage ends green: tests pass,
`ruff` clean, and the stage's checklist fully ticked before moving on.

## Stage 0 — Prove the core (skeleton exists, finish it)

- [ ] `prax.store`: schema init from `schema.sql`, WAL on, content-hash
      ingest of raw text/files into `data/archive/`
- [ ] Chunking (simple: ~1000-char windows, overlap 150) into `chunks` + FTS5
- [ ] `search` (FTS5-only for now), `get`, `link`, `traverse` (recursive CTE,
      max 2 hops, valid edges only)
- [ ] FastAPI app exposing the five endpoints
- [ ] FastMCP server exposing `search` / `get` / `traverse` / `link` /
      `ingest` as tools (stdio)
- [ ] pytest: ingest → search roundtrip; link → traverse roundtrip;
      duplicate ingest is a no-op (same hash)
- [ ] Register in Claude Code via `.mcp.json`; verify tools respond

## Stage 1 — Real ingestion

- [ ] PDF parsing via Docling (batch job: `scripts/backfill.py`)
- [ ] Web snapshots: trafilatura for text extraction; store original HTML in
      the archive
- [ ] Inbox watcher: poll a folder (later: Karakeep/Linkwarden API) and feed
      the ingest endpoint
- [ ] Backfill run against a copy of the old zoetrope disk; review dedupe
      report before touching originals

## Stage 2 — Hybrid retrieval

- [ ] Embedding batch job: quantized bge-small ONNX (384-dim) → `chunks_vec`
- [ ] `search` becomes hybrid: FTS5 + vec in parallel, RRF fusion
- [ ] Optional cross-encoder rerank behind a flag; benchmark on the Pi
- [ ] Eval harness: ~20 hand-written queries with expected docs, tracked in
      `tests/eval/`

## Stage 3 — Graph enrichment

- [ ] `ontology.yaml` v1 (entity + relation types; keep it under ~10 each)
- [ ] Nightly extraction job (Claude API): triples against ontology,
      confidence-tagged, `source_doc` + `ontology_version` stamped
- [ ] Entity resolution pass (embedding candidates → LLM adjudication);
      merges recorded via `entities.canonical_id`
- [ ] Edge invalidation on contradiction (set `valid_to`, insert successor)
- [ ] MCP `traverse` surfaces confidence + provenance in results

## Later / maybe

- Streamable-HTTP MCP transport for remote access over Tailscale
- Litestream replication of `data/prax.db`
- Kùzu migration script (only if entity threshold is crossed)
- Complement/"blast-radius" SQL tools exposed via MCP
