-- The modules the reading-in-full pilot asked for (2026-11-26): core 8 -> 9
-- (`influenced_by` moved in from society), craft 1 -> 2 (`applies` takes a
-- concept), music 1 -> 2 (people in music, wider kinds), sound 1 -> 2
-- (wider `affects`, `arises_from`, `applied_to`), society 1 -> 2 (without
-- `influenced_by`, which core now holds for it). Only additions and
-- widenings, so as in 0039, 0050, 0051 and 0056 every reading made under
-- the old strings is valid under the new ones: the stamps move (the
-- extraction's, its history, a failure's, a reading in full's plan)
-- instead of every document becoming due again. A version string is the
-- modules' names and numbers joined by "+", core first.
UPDATE documents
   SET meta = replace(replace(replace(replace(replace(replace(
              replace(replace(replace(replace(meta,
              '"core8+', '"core9+'), '"core8"', '"core9"'),
              '+craft1+', '+craft2+'), '+craft1"', '+craft2"'),
              '+music1+', '+music2+'), '+music1"', '+music2"'),
              '+sound1+', '+sound2+'), '+sound1"', '+sound2"'),
              '+society1+', '+society2+'), '+society1"', '+society2"')
 WHERE meta LIKE '%"core8%';
