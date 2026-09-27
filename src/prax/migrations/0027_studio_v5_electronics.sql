-- Studio 4 -> 5 and the electronics module
-- (docs/ontology-electronics.md). Studio only widens (has_part from a
-- component), so a reading made against studio 4 is valid against 5 and
-- its stamp moves. A document read against every module was not read
-- against electronics, which is new: its stamp moves too, but it still
-- lacks `electronics1`, so it stays due for a reading against the whole
-- ontology (61 documents on 2026-09-27). As in 0026 the match is on the
-- version string alone.
UPDATE documents
   SET meta = replace(replace(meta, '+studio4"', '+studio5"'), '+studio4+', '+studio5+')
 WHERE meta LIKE '%+studio4%';
