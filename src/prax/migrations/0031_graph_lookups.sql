-- Two lookups of the graph that scanned (docs/research-database-layout.md,
-- measured on a copy on 2026-09-30).
--
-- A name is looked up without regard to case (`label = ? COLLATE NOCASE`,
-- in five places: the walk, a query's expansion, linking, the context,
-- the labels). The index on `label` compares with case, so each lookup
-- scanned all 249,000 labels: 12 to 26 ms warm, 300 ms cold, several
-- times a query. An index in the lookup's own collation answers it.
CREATE INDEX IF NOT EXISTS idx_entity_labels_label_nocase
    ON entity_labels(label COLLATE NOCASE);

-- An entity and its aliases are found by `id = ? OR canonical_id = ?`.
-- The index on `COALESCE(canonical_id, id)` does not serve the bare
-- column, so each was a scan of `entities`. Only merged entities have a
-- `canonical_id`, so the index holds only those.
CREATE INDEX IF NOT EXISTS idx_entities_canonical_id
    ON entities(canonical_id) WHERE canonical_id IS NOT NULL;

-- Two indexes that repeated another's leading column: chunks' own
-- UNIQUE (doc_id, seq), and entity_labels' (entity_id, label, lang).
DROP INDEX IF EXISTS idx_chunks_doc;
DROP INDEX IF EXISTS idx_entity_labels_entity;
