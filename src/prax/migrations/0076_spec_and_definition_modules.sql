-- What the review queue's grades of 2026-11-27 widened: studio 5 -> 6
-- (`has_spec` from a tool or a document: a build's measured value, a
-- paper's reported figure) and research 9 -> 10 (`defines` a method).
-- Only widenings, so as in 0075 every reading made under the old strings
-- is valid under the new ones: the stamps move (the extraction's, its
-- history, a failure's, a reading in full's plan) instead of every
-- document becoming due again. A version string is the modules' names
-- and numbers joined by "+", core first, so neither module opens one.
UPDATE documents
   SET meta = replace(replace(replace(replace(meta,
              '+research9+', '+research10+'), '+research9"', '+research10"'),
              '+studio5+', '+studio6+'), '+studio5"', '+studio6"')
 WHERE meta LIKE '%+research9%' OR meta LIKE '%+studio5%';
