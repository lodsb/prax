-- A version stamp names its modules (AV, the BWS survey of 2026-11-17).
-- Before 2026-09-11 the ontology was one file, `ontology.yaml`, with a
-- plain number for its version ("0" to "5"); that file became
-- `ontology/research.yaml` when core was split out of it, and version 5
-- was restated then as "core1+research5" (scripts/restamp_ontology.py,
-- for the four documents read under it). The edges written under the
-- single file kept the plain number: 114,761 of them on the live store,
-- some 54,000 live, which a reader of a composed stamp could not place.
--
-- Here each takes the name of the module its file became: "0" to "4"
-- are "research0" to "research4", and "5" is "core1+research5" as the
-- restamp wrote it. The stamp says the same thing in the shape every
-- other stamp has; a version stamp is provenance a repair may mend
-- (migration 35), and no fact changes. A subset's stamp ("core3", a
-- document read against core alone) already names its module and stays.
UPDATE edges
   SET ontology_version = CASE ontology_version
       WHEN '5' THEN 'core1+research5'
       ELSE 'research' || ontology_version END
 WHERE ontology_version IN ('0', '1', '2', '3', '4', '5');

UPDATE review_queue
   SET ontology_version = CASE ontology_version
       WHEN '5' THEN 'core1+research5'
       ELSE 'research' || ontology_version END
 WHERE ontology_version IN ('0', '1', '2', '3', '4', '5');

UPDATE documents
   SET meta = json_set(meta, '$.extraction.ontology_version',
       CASE json_extract(meta, '$.extraction.ontology_version')
       WHEN '5' THEN 'core1+research5'
       ELSE 'research' || json_extract(meta, '$.extraction.ontology_version') END)
 WHERE json_extract(meta, '$.extraction.ontology_version')
       IN ('0', '1', '2', '3', '4', '5');

UPDATE documents
   SET meta = json_set(meta, '$.extraction_error.ontology_version',
       CASE json_extract(meta, '$.extraction_error.ontology_version')
       WHEN '5' THEN 'core1+research5'
       ELSE 'research' || json_extract(meta, '$.extraction_error.ontology_version') END)
 WHERE json_extract(meta, '$.extraction_error.ontology_version')
       IN ('0', '1', '2', '3', '4', '5');
