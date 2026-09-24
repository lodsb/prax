-- One preferred name per language, and a rename recorded as what it is.
--
-- `entity_labels` was built to the shape SKOS gives a concept — a label
-- per language, one of them preferred — but it enforced neither half of
-- it (docs/stratification.md, step 5). SKOS requires exactly one
-- prefLabel per language, and without that constraint the name a German
-- search should show is again whichever row arrives first, which is the
-- hole migration 20 was written to close.
--
-- The store is clean today: no entity has two preferred names in one
-- language and no preferred label lacks a language, so the index goes on
-- without a repair. `add_label` demotes the one already there when a new
-- preferred name arrives, so the constraint is kept rather than hit.
CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_labels_pref
    ON entity_labels(entity_id, lang) WHERE kind = 'pref';

-- The vocabulary pass needed to record the name an entity had before it
-- renamed it, so `unmerge_run` could put it back, and it used a third
-- `kind` to do it (2026-09-24). That made an undo record wear a label's
-- clothes: `was` is not a kind of label, it is a fact about one. The kind
-- goes back to SKOS's two and the fact gets a column.
ALTER TABLE entity_labels ADD COLUMN was INTEGER NOT NULL DEFAULT 0;

UPDATE entity_labels SET was = 1, kind = 'alt' WHERE kind = 'was';

CREATE INDEX IF NOT EXISTS idx_entity_labels_was
    ON entity_labels(run) WHERE was = 1;
