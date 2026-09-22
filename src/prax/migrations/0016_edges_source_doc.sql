-- Edges by the document they came from. Everything that asks "what does
-- the graph record about this document" (the facts beside an ask's
-- passages, a document's context, retiring a reading, a twin's edge
-- count) filtered 240 K edges by source_doc without an index: 80 ms a
-- document, 20 s for the twin-documents check over 469 title groups
-- (2026-09-22). Partial on the live edges, which is what every reader
-- asks for; the retirement update finds its rows through it too.
CREATE INDEX IF NOT EXISTS idx_edges_source_doc
    ON edges(source_doc)
    WHERE valid_to IS NULL;
