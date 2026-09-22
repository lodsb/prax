-- The length of a document's text artifact in characters, written by
-- index_text. A caller that wants the row without the text (the work
-- hand-out, a reading request, the document page's info line) read the
-- whole artifact from disk for this one number before. NULL means an
-- artifact indexed before this column: get_document reads it, and the
-- maintain pass `lengths` fills the column once.
ALTER TABLE documents ADD COLUMN text_len INTEGER;

-- A person's request for the graph to be read again (meta.extraction_stale
-- .requested, store.request_extraction) is selected in every extract scope,
-- by a query of its own beside the scope's (select_for_extraction): this
-- partial index answers it. The requests are few, so it stays small.
CREATE INDEX IF NOT EXISTS idx_documents_extract_requested
    ON documents(id)
    WHERE json_extract(meta, '$.extraction_stale.requested') IS NOT NULL;
