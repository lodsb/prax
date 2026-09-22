-- The figures nobody has read, as a partial index: the unread-figures
-- check (GET /heal, the Jobs page) read every figure chunk's data (108 K
-- of them, 7.6 s) to find the ones without readings. The index holds
-- exactly those rows and drops each as its reading is written, so the
-- check is an index scan. The expression is the one the check uses,
-- verbatim, which is what lets SQLite choose the index.
CREATE INDEX IF NOT EXISTS idx_chunks_figure_unread
    ON chunks(doc_id)
    WHERE kind = 'figure'
      AND coalesce(json_array_length(json_extract(data, '$.readings')), 0) = 0;
