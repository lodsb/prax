-- What changed in a period (`store.changes`, 2026-10-04) reads the edges
-- written or ended between two moments, and on world time those that
-- began or ended between two dates. Without these it scanned every edge:
-- 1.2 s for a day and 7.2 s for a year on a copy of the library.
CREATE INDEX IF NOT EXISTS idx_edges_written ON edges(valid_from);
CREATE INDEX IF NOT EXISTS idx_edges_ended ON edges(valid_to) WHERE valid_to IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_edges_world_from ON edges(world_from)
    WHERE world_from IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_edges_world_to ON edges(world_to)
    WHERE world_to IS NOT NULL;
