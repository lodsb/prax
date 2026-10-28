-- Every face of a picture, the ended ones too (the quality review of
-- 2026-10-28): a picture is personal while a face of it is named, and an
-- answer may stay on a face a later reading ended (store.apply_faces), so
-- the wall's one predicate (store.personal_sql) asks by picture over all
-- of them. faces_doc (migration 53) holds the live faces only.
CREATE INDEX faces_doc_any ON faces(doc_id);
