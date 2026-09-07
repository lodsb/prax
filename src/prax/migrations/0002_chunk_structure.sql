-- Structure-aware chunks (rationale R13). A chunk is an addressable region of
-- a document with a searchable rendering, not merely a window of text.
--   kind     text | table | figure | code   (media kinds such as audio later)
--   locator  JSON; for text artifacts {"char_start", "char_end", "page"?}
--            so chunk.text == artifact[char_start:char_end] always holds
--   heading  JSON list, the section path the chunk sits under
--   data     JSON payload for structured kinds (a table's header + rows)
-- Existing rows keep NULLs until the document is re-chunked
-- (scripts/rechunk.py); FTS is unaffected (content table, text column only).
ALTER TABLE chunks ADD COLUMN kind TEXT;
ALTER TABLE chunks ADD COLUMN locator TEXT;
ALTER TABLE chunks ADD COLUMN heading TEXT;
ALTER TABLE chunks ADD COLUMN data TEXT;
CREATE INDEX IF NOT EXISTS idx_chunks_kind ON chunks(kind);
