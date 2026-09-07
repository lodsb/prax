-- Bookkeeping for chunk vectors (rationale R6). The vectors themselves live
-- in the sqlite-vec virtual table chunks_vec, created in code by
-- prax.store.init_db once the extension is loaded (a virtual table cannot
-- be declared here). This plain table answers "which chunks still need a
-- vector, and from which model" cheaply and without the extension, and
-- disappears with its chunk (foreign keys are on per connection).
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id    INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    embedded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_model ON chunk_embeddings(model);
