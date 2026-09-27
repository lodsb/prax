-- Ontology v9 (docs/ontology-v9.md): research 8 -> 9. The bump only
-- widens what the module accepts (a document where a paper was named, a
-- person where an author was), so every reading made under the v8
-- strings is valid under the v9 ones, and the misfits it now accepts are
-- the review queue's replay, not a re-extraction. As in 0018 the
-- extraction stamps move to the new strings instead of every document
-- read under v8 becoming due again. Edges keep the version they were
-- written under. The match is on the version string alone: a meta
-- written by json_set has no space after its colons (1,033 of the 2,292
-- stamps on 2026-09-27), which 0018's pattern would have missed.
UPDATE documents
   SET meta = replace(replace(meta, '+research8"', '+research9"'), '+research8+', '+research9+')
 WHERE meta LIKE '%+research8%';
