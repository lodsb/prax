-- A walk as of a day (`store.held_at`, `traverse(as_of=)`) reads ended
-- edges too, which the partial indexes on `src` and `dst` (live edges
-- only) leave out: without these, it scans every edge at each step of
-- the walk. Keyed by the end and the time it was written, as the
-- condition reads (2026-10-03).
CREATE INDEX IF NOT EXISTS idx_edges_src_held ON edges(src, valid_from);
CREATE INDEX IF NOT EXISTS idx_edges_dst_held ON edges(dst, valid_from);
