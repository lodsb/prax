-- A merge carries its own provenance, as an edge does.
--
-- `unmerge_run` found a round's merges through the labels the round wrote:
-- the duplicate's name, kept on the survivor with the run. That label is
-- written INSERT OR IGNORE, and when the survivor already answers to the
-- same name in the same language (a concept and a method both called
-- `augmented reality`) nothing is written, and the merge is under no run
-- at all. The round of 2026-09-27 made 394 merges and left 363 such
-- labels: 31 merges no unmerge could find.
--
-- So the merge is stamped on the row it changes. The labels stay what
-- they are, the names an entity answers to.
ALTER TABLE entities ADD COLUMN merged_by TEXT;
ALTER TABLE entities ADD COLUMN merged_run TEXT;

-- what the labels recorded, carried over; the index makes it a lookup
-- per merged entity instead of a scan of the labels for each
CREATE INDEX IF NOT EXISTS idx_entity_labels_from_entity
    ON entity_labels(from_entity) WHERE from_entity IS NOT NULL;

UPDATE entities
   SET merged_run = (SELECT l.run FROM entity_labels l
                      WHERE l.from_entity = entities.id AND l.run IS NOT NULL
                      ORDER BY l.id DESC LIMIT 1),
       merged_by = (SELECT l.producer FROM entity_labels l
                     WHERE l.from_entity = entities.id AND l.producer IS NOT NULL
                     ORDER BY l.id DESC LIMIT 1)
 WHERE canonical_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_entities_merged_run
    ON entities(merged_run) WHERE merged_run IS NOT NULL;
