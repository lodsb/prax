-- A clean-up from before 2026-11-07 kept the edges it ended in each
-- document's meta.retired.ended rather than in edge_endings, and every
-- undo had to read that list first (record_old_endings, in two places).
-- Each list becomes its run's endings at the retirement's time, and the
-- list goes (the quality review of 2026-11-11, finding 4). 377 documents
-- of 4 runs on the owner's store, 1,034 edges, none recorded yet.

INSERT OR IGNORE INTO edge_endings (edge_id, run, ended_at)
SELECT CAST(e.value AS INTEGER),
       json_extract(d.meta, '$.retired.run'),
       coalesce(json_extract(d.meta, '$.retired.at'), g.valid_to)
FROM documents d,
     json_each(json_extract(d.meta, '$.retired.ended')) e
JOIN edges g ON g.id = CAST(e.value AS INTEGER)
WHERE json_extract(d.meta, '$.retired.run') IS NOT NULL
  AND coalesce(json_extract(d.meta, '$.retired.at'), g.valid_to) IS NOT NULL;

UPDATE documents
SET meta = json_remove(meta, '$.retired.ended')
WHERE json_extract(meta, '$.retired.ended') IS NOT NULL
  AND json_extract(meta, '$.retired.run') IS NOT NULL;
