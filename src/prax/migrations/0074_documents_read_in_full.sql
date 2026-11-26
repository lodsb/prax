-- The documents asked to be read in full (`meta.parts.asked`, plan item 3,
-- 2026-11-26). The extract step's hand-out asks for them on every call,
-- an empty one too (`store.parts_asked`); without an index that read the
-- meta of every document: 200 ms warm on the live store of 2026-11-26
-- (33,811 documents). The shape of `idx_documents_promote`.
CREATE INDEX IF NOT EXISTS idx_documents_parts
    ON documents(json_extract(meta, '$.parts.asked.at'))
    WHERE json_extract(meta, '$.parts.asked') IS NOT NULL;
