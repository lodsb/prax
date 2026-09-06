# prax — personal research knowledge base

Successor to the "zoetrope" external-disk store. A self-hosted knowledge base
(PDFs + web snapshots) with hybrid search, a small knowledge graph, and a
Claude/MCP agent interface. Runs on a Raspberry Pi / N100 home server behind
Tailscale.

## Architecture invariants (do not violate without updating this file)

1. **SQLite is the canonical store.** One database file (`data/prax.db`):
   FTS5 for BM25, sqlite-vec for embeddings, a plain `edges` table for the
   graph. WAL mode always on. No Postgres, no Neo4j, no server databases.
2. **Files are content-addressed.** Originals (PDFs, HTML snapshots) live at
   `data/archive/<sha256[:2]>/<sha256>`. The DB stores metadata + hash only.
   Never store blobs in SQLite.
3. **One door.** All mutations go through `prax.store` (used by the FastAPI
   app in `prax.api`). Capture inboxes, cron jobs, and the MCP server are all
   clients of that layer. No module writes to SQLite directly except
   `prax.store`.
4. **Single writer.** The service process is the only writer. Batch jobs run
   through the same store functions, serialized.
5. **The MCP server is a thin proxy.** `prax.mcp_server` imports `prax.store`
   directly (same process) and exposes tools; it contains no business logic.
6. **Agent-shaped endpoints.** `search` returns compact snippets + ids, never
   full documents. `get` fetches one record fully. `traverse` expands 1–2 hops.
   Keep responses small; Claude's context is the scarce resource.
7. **Pi-class hardware target.** No dependency that requires >1 GB resident
   RAM in the serving path. Parsing (Docling) and embedding run as batch jobs,
   never inline in a request.
8. **Graph edges are evidence, not truth.** Every edge carries
   `confidence` (EXTRACTED | INFERRED | AMBIGUOUS), `source_doc`,
   `ontology_version`, and bi-temporal columns (`valid_from`, `valid_to`,
   `ingested_at`). Enrichment invalidates edges (sets `valid_to`); it never
   deletes them.
9. **Ontology is small and versioned.** Entity/relation types live in
   `ontology.yaml`. Extraction emits triples only against the current
   version; misfits go to a review queue, not into the graph.

## Decision thresholds (revisit design only past these)

- Vectors > ~1M → move the vector layer to LanceDB; everything else stays.
- Entities > ~50–100k or slow recursive-CTE traversal → move edges to Kùzu
  (embedded); not Neo4j.
- SQLite write contention across capture sources → the answer is the single
  writer queue, not a new database.

## Retrieval design

Hybrid: FTS5 (BM25) and vector search run in parallel, fused with Reciprocal
Rank Fusion. Optional cross-encoder rerank (bge-reranker-v2-m3) over fused
top-N — benchmark on target hardware before enabling by default. Graph
traversal expands entry-point hits 1–2 hops. Complement queries ("what is NOT
connected") and weighted multi-hop scoring are explicit SQL tools, never
retrieval.

## Conventions

- Python ≥3.11, `pyproject.toml` with uv/pip, `pytest` for tests.
- Type hints everywhere; `ruff` clean.
- Embeddings: 384-dim (bge-small-class, quantized ONNX). The dimension is
  baked into the `chunks_vec` table — changing models means a migration.
- Timestamps are UTC ISO-8601 strings.
- Tests must not touch `data/`; use tmp_path fixtures.

## Roadmap

See `docs/PLAN.md`. Work one stage per session; write tests before wiring
the MCP layer. Background research and rationale: `docs/research.md`.
