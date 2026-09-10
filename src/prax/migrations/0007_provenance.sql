-- Provenance on edges (2026-09-11): which producer wrote the edge (a model
-- name, "zotero", "crossref", "openalex", "page", "replay", "manual",
-- "agent") and which run (a batch id, a script run id). An attribute of the
-- fact, not a fact in the graph: selecting, retiring and upgrading a
-- producer's work is a WHERE clause, never a hop. Backfilled by
-- store.backfill_provenance from evidence prefixes and document stamps.
ALTER TABLE edges ADD COLUMN producer TEXT;
ALTER TABLE edges ADD COLUMN run TEXT;
CREATE INDEX IF NOT EXISTS idx_edges_producer ON edges(producer);
CREATE INDEX IF NOT EXISTS idx_edges_run ON edges(run);
