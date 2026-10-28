-- The rows folded into an entity, found by an index (the gallery's picture
-- sets, 2026-10-28): every read of an alias group asked
-- `canonical_id = ?` and scanned the whole table for it, some 10 ms at
-- 263,645 entities, and a walk of a place's places asked it once a level.
-- Partial: only a folded row has a canonical_id.
CREATE INDEX entities_by_canonical ON entities(canonical_id)
    WHERE canonical_id IS NOT NULL;
