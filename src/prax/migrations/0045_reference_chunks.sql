-- The reference chunks of a set of documents (`cited_but_missing`, the
-- quality review of 2026-10-05): with only the kind index the planner
-- walked every reference chunk of the library (158,645) for a handful of
-- documents, 65-90 ms; through the document index it read every chunk of
-- those documents, 1.5 s cold for 500. This partial index holds the
-- reference chunks alone, by document: 0-23 ms. The query names it
-- (INDEXED BY): prax keeps no planner statistics.
CREATE INDEX IF NOT EXISTS idx_chunks_reference ON chunks(doc_id) WHERE kind = 'reference';
