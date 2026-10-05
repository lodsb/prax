-- Which edge corrects which (the quality review of 2026-10-05, finding
-- 5): the heal repairs `backwards-part-of` and `not-venues` end an edge
-- and write what it meant beside it, and kept no record of the pair, so
-- "this fact was corrected into that one" was a guess. The repair sets
-- `corrected_by` on its ending. A column of its own: `restated_as` is
-- what `store.restore_run` wrote back, and NULL there means "not restored
-- yet", which cannot carry a second meaning. It is the edge-level
-- `supersedes` of AL ("As Utopia does it") too, rather than a fifth side
-- table beside premises, conflicts and endings.
ALTER TABLE edge_endings ADD COLUMN corrected_by INTEGER REFERENCES edges(id);

CREATE INDEX idx_edge_endings_corrected_by ON edge_endings (corrected_by)
    WHERE corrected_by IS NOT NULL;
