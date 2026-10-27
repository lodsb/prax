-- What a face looks like (2026-10-27, plan item AT step 2): the model
-- whose vector of it the face index holds (vectors-face-<model>.usearch,
-- keyed by the face's id; NULL: none yet), when it was written, and the
-- small square picture of it the Review page shows, filed in the archive
-- by its hash like a thumbnail (invariant 2: never a blob here).
ALTER TABLE faces ADD COLUMN vector_model TEXT;
ALTER TABLE faces ADD COLUMN vector_at TEXT;
ALTER TABLE faces ADD COLUMN crop TEXT;
CREATE INDEX faces_unvectored ON faces(doc_id) WHERE gone_at IS NULL AND vector_model IS NULL;
