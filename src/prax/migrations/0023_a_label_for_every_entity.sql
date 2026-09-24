-- Every entity answers to at least its own name.
--
-- `entity_labels` was built to be what a thing is called (migration 20)
-- and only the entities a pass had touched ever got a row: 1,516 of
-- 121,303 live ones. So "the labels are authoritative" was not true, and
-- `entities.name` could not be rebuilt from them — which is what the
-- identity change needs (docs/identity.md).
--
-- One preferred label per entity, from the name it carries now, in the
-- language nobody has placed (NULL: the `languages` pass of `prax
-- maintain` fills those in from the documents that named the entity).
-- Merged aliases get one too: their name is a name the thing answers to,
-- and `_label_from_merge` only records it on the survivor.
--
-- `INSERT OR IGNORE` against the unique index of migration 20, so an
-- entity that already has this label keeps the row it has, and against
-- the one-pref-per-language index of migration 22, so an entity that
-- already has a preferred label in its language keeps that one and this
-- insert does nothing.
INSERT OR IGNORE INTO entity_labels (entity_id, label, lang, kind, producer, run)
SELECT e.id, e.name, NULL, 'pref', 'baseline', 'labels-0023'
  FROM entities e
 WHERE NOT EXISTS (
     SELECT 1 FROM entity_labels l
      WHERE l.entity_id = e.id AND l.kind = 'pref'
 );

CREATE INDEX IF NOT EXISTS idx_entity_labels_entity ON entity_labels(entity_id);
