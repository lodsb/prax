-- studio v4: written_by was core's authored_by under another name; the
-- edges and queued items written under it take the name the ontology
-- keeps (their ontology_version stamp stays what it was).
UPDATE edges SET rel = 'authored_by' WHERE rel = 'written_by';
UPDATE review_queue SET rel = 'authored_by' WHERE rel = 'written_by';
