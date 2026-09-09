-- Document-level retrieval field (rationale R6 addendum, 2026-09-10).
-- Chunk scoring finds documents *about* a term, not documents that *are*
-- the thing ("schematic": a CAD manual outranks the one schematic). Each
-- document gets a short field of its identity: title, kind words, creators,
-- extraction summary, an image description's opening paragraph. It is
-- indexed here for BM25 and embedded once per document into its own
-- usearch file (data/vectors-doc-<model>.usearch); search fuses both with
-- the chunk lists. prax.store rebuilds the field whenever a document's
-- text or metadata changes (index_text, set_meta).
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(field);

CREATE TABLE IF NOT EXISTS document_embeddings (
    doc_id      INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    embedded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_document_embeddings_model ON document_embeddings(model);
