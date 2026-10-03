-- When a fact holds in the world, beside when prax held it (stage AL,
-- step 5; 2026-10-03). `valid_from`/`valid_to` are record time: when an
-- edge was written and when it was ended. These are the world's time, as
-- the source states it: a date as precise as written (`2019`, `2019-07`,
-- `2019-07-03`) and its precision, filled only where a source says it.
-- A fact that ended at a date nobody gives has `world_to` NULL and
-- `world_to_precision` 'unknown' (Utopia's shape). The precision names
-- its date's length; `store.link` keeps them together.
ALTER TABLE edges ADD COLUMN world_from TEXT
    CHECK (world_from IS NULL OR length(world_from) IN (4, 7, 10));
ALTER TABLE edges ADD COLUMN world_from_precision TEXT
    CHECK (world_from_precision IS NULL OR world_from_precision IN ('year', 'month', 'day'));
ALTER TABLE edges ADD COLUMN world_to TEXT
    CHECK (world_to IS NULL OR length(world_to) IN (4, 7, 10));
ALTER TABLE edges ADD COLUMN world_to_precision TEXT
    CHECK (world_to_precision IS NULL OR world_to_precision IN ('year', 'month', 'day', 'unknown'));
