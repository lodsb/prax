-- The canonical id of every entity as an expression index: traverse walks
-- over raw ids and expands each reached entity to its alias group through
-- this index, instead of joining a canon CTE (no index) into the recursion,
-- which scanned every live edge per frontier row and ran for minutes on a
-- hub at two hops.
CREATE INDEX IF NOT EXISTS idx_entities_canonical
    ON entities(COALESCE(canonical_id, id));
