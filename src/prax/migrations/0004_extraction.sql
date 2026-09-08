-- Stage 3: LLM extraction writes edges with a quote as evidence and parks
-- triples that do not fit the current ontology in a review queue instead of
-- the graph (CLAUDE.md invariants 8 and 9).
ALTER TABLE edges ADD COLUMN evidence TEXT;   -- short quote from source_doc's text

CREATE TABLE IF NOT EXISTS review_queue (
    id           INTEGER PRIMARY KEY,
    source_doc   INTEGER REFERENCES documents(id),
    src          TEXT NOT NULL,
    src_type     TEXT,
    rel          TEXT NOT NULL,
    dst          TEXT NOT NULL,
    dst_type     TEXT,
    evidence     TEXT,
    reason       TEXT NOT NULL,                 -- why it was refused
    ontology_version TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    resolved_at  TEXT,                          -- NULL = still open
    resolution   TEXT                           -- linked | dropped | ontology
);
CREATE INDEX IF NOT EXISTS idx_review_open ON review_queue(resolved_at) WHERE resolved_at IS NULL;
