-- The embedding stamps by model and time (docs/research-database-layout.md,
-- measured on a copy on 2026-09-30).
--
-- At start the door asks which vectors were stamped since the index
-- files were last saved (`model = ? AND embedded_at >= ?`), to forget
-- those the files lost. The index on `model` alone, a column with one
-- value, answered it by reading every row: 158 ms warm, 1.8 s cold,
-- where the index on both answers in 0.3 ms. It serves `model = ?` as
-- well, so the one on the model goes.
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_stamp
    ON chunk_embeddings(model, embedded_at);
DROP INDEX IF EXISTS idx_chunk_embeddings_model;

CREATE INDEX IF NOT EXISTS idx_document_embeddings_stamp
    ON document_embeddings(model, embedded_at);
DROP INDEX IF EXISTS idx_document_embeddings_model;
