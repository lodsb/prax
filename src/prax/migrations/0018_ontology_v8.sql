-- Ontology v8 (docs/ontology-v8.md): core 2 -> 3, research 7 -> 8. The
-- bump only widens what the modules accept (affiliation beyond persons,
-- located_in for a person) and adds aliases; every reading made under
-- the v7 strings is valid under the v8 ones, and what the models parked
-- as unmapped is the review queue's replay, not a re-extraction. So the
-- extraction stamps move to the new strings here instead of every
-- document being re-selected for the backlog pass (as v7 was: 7,018
-- documents were still due from it on 2026-09-22). Edges keep the
-- version they were written under, as always.
UPDATE documents
   SET meta = replace(meta, '"ontology_version": "core2+', '"ontology_version": "core3+')
 WHERE meta LIKE '%"ontology_version": "core2+%';
UPDATE documents
   SET meta = replace(replace(meta, '+research7"', '+research8"'), '+research7+', '+research8+')
 WHERE meta LIKE '%"ontology_version": "core3+%research7%';
