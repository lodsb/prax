-- Core 3 -> 4 (2026-10-04, stage AL step 4): `links_to`, `supersedes`
-- and `invalidates`, document to document. The bump only adds relations,
-- so every reading made under the core 3 strings is valid under core 4,
-- and as in 0018 and 0026 the extraction stamps move to the new strings
-- instead of every document becoming due again (core is in every
-- document's set, so the whole library would have been). Edges keep the
-- version they were written under. Core is always first in a composed
-- version, so the match is the string's start: `"core3+` and `"core3"`.
UPDATE documents
   SET meta = replace(replace(meta, '"core3+', '"core4+'), '"core3"', '"core4"')
 WHERE meta LIKE '%"core3%';
