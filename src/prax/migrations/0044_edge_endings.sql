-- Which edges a repair run ended (the review of 2026-10-04): the heal
-- repairs `backwards-part-of` and `not-venues` end an edge and write
-- what it meant beside it under their own run. Retiring that run ended
-- the replacements but could not bring back what the run had ended, so
-- the run was not undoable. Recorded here, `store.restore_run` restates
-- each ended fact as a new edge (the ended one stays ended: record time
-- is history) and retires the run's own edges.
CREATE TABLE edge_endings (
    edge_id     INTEGER NOT NULL REFERENCES edges(id),
    run         TEXT NOT NULL,
    ended_at    TEXT NOT NULL,
    restated_as INTEGER REFERENCES edges(id),
    PRIMARY KEY (edge_id, run)
);

CREATE INDEX idx_edge_endings_run ON edge_endings (run);
