-- The live edges of a relation, by document (AV, 2026-11-22). A walk
-- counts a fact's support by work (`store.graph.edges.as_facts`), so it
-- asks which of a hub's documents are versions of a work: the live
-- `example_of` edges of those documents (`store.retrieval.works_of`).
-- Through `idx_edges_source_doc` that read every live edge of every
-- document, about 20 ms for a thousand documents on the live store of
-- 2026-11-22 (345,926 live edges); with this index, 0.3 ms. A read of
-- one relation alone (`rel = ?`) was a scan of the table, 125 ms.
CREATE INDEX IF NOT EXISTS idx_edges_rel_doc
    ON edges(rel, source_doc)
    WHERE valid_to IS NULL;
