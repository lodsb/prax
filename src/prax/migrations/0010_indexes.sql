-- Expression indexes on the JSON paths every document-level filter reads
-- (a scan of documents.meta cost about 40 ms at 9,700 rows and grew with
-- the store), and the review queue's open items by document (what a
-- re-read of a document supersedes). Partial where the interesting rows
-- are few, so the indexes stay small. No code change needed: SQLite uses
-- an expression index when the query repeats the expression verbatim,
-- which is why the store always writes json_extract(meta, '$.path').
CREATE INDEX IF NOT EXISTS idx_documents_source
    ON documents(json_extract(meta, '$.source'))
    WHERE json_extract(meta, '$.retired') IS NULL;
CREATE INDEX IF NOT EXISTS idx_documents_retired
    ON documents(id)
    WHERE json_extract(meta, '$.retired') IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_documents_domains
    ON documents(json_extract(meta, '$.domains'));
CREATE INDEX IF NOT EXISTS idx_documents_ontology
    ON documents(json_extract(meta, '$.extraction.ontology_version'))
    WHERE json_extract(meta, '$.retired') IS NULL AND text_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_documents_promote
    ON documents(json_extract(meta, '$.promote.at'))
    WHERE json_extract(meta, '$.promote') IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_documents_zotero_parent
    ON documents(json_extract(meta, '$.zotero.parent'))
    WHERE json_extract(meta, '$.zotero.parent') IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_review_open_doc
    ON review_queue(source_doc)
    WHERE resolution IS NULL;
