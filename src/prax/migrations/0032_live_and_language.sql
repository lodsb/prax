-- Two document filters that read `meta` row by row
-- (docs/research-database-layout.md, measured on a copy on 2026-09-30).
--
-- The live documents (`json_extract(meta, '$.retired') IS NULL`, 55
-- uses in the store). 0010 indexed the retired side only, so counting
-- the live ones read every document's `meta`. Keyed by the order the
-- library is browsed in, newest first: keyed by id, the planner took it
-- for the listing and sorted every live row, 138 ms against 0.4.
CREATE INDEX IF NOT EXISTS idx_documents_live
    ON documents(added_at, id)
    WHERE json_extract(meta, '$.retired') IS NULL;

-- A document's language (`json_extract(meta, '$.lang')`, 14 uses). The
-- vocabulary pass joins every live edge to its document's language; with
-- the index it starts from the documents in one language instead.
CREATE INDEX IF NOT EXISTS idx_documents_lang
    ON documents(json_extract(meta, '$.lang'));
