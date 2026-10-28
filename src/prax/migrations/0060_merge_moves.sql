-- What a merge moved besides the entity it folded (the review of
-- 2026-10-28): merge_entities flattens chains, so an entity folded into B
-- earlier points straight at A once B is folded into A. Undoing B's merge
-- has to point it back at B, and nothing on its row says it was B's.
CREATE TABLE merge_moves (
    run        TEXT NOT NULL,
    entity_id  INTEGER NOT NULL REFERENCES entities(id),
    was        INTEGER NOT NULL REFERENCES entities(id)
);
CREATE INDEX merge_moves_run ON merge_moves(run);
