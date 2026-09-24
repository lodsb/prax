-- Every entity carries a label of the name it has, not only of the name
-- it had when nobody had given it one.
--
-- Migration 23 wrote a preferred label for each entity `WHERE NOT EXISTS
-- (a preferred label)`, which skipped exactly the entities that most
-- needed one: the 1,516 a pass had already given a preferred name in
-- another language. Their own name was then recorded nowhere, and the
-- first rebuild of the display names renamed four of them —
-- `Chomsky-Normalform` to `Chomsky normal form` — with the German
-- spelling left in no row at all. A name is not lost by a rename under
-- this design; that was the bug, not the intent.
--
-- So: the entity's own name, as a label, wherever it is not one already.
-- Preferred when the entity has no preferred label in any language, an
-- alternative when it has — one preferred label per language is the rule
-- (migration 22), and this migration cannot know the language, so it
-- must not claim to.
INSERT OR IGNORE INTO entity_labels (entity_id, label, lang, kind, producer, run)
SELECT e.id,
       e.name,
       NULL,
       CASE WHEN EXISTS (SELECT 1 FROM entity_labels p
                          WHERE p.entity_id = e.id AND p.kind = 'pref')
            THEN 'alt' ELSE 'pref' END,
       'baseline',
       'labels-0024'
  FROM entities e
 WHERE NOT EXISTS (
     SELECT 1 FROM entity_labels l
      WHERE l.entity_id = e.id AND l.label = e.name COLLATE NOCASE
 );
