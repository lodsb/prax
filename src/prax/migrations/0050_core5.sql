-- Core 4 -> 5 (2026-10-26, AR's photo stage): `taken_in` (document to
-- place, from a picture's GPS position) and `located_in` widened to take
-- a place on the left. Only additions, so as in 0039 every reading made
-- under the core 4 strings is valid under core 5 and the extraction
-- stamps move instead of every document becoming due again.
UPDATE documents
   SET meta = replace(replace(meta, '"core4+', '"core5+'), '"core4"', '"core5"')
 WHERE meta LIKE '%"core4%';
