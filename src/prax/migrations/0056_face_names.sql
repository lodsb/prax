-- Naming the faces (2026-10-27, plan item AT step 4). Core 6 -> 7 adds
-- `depicts` (document to person) and `attended` (person to event): only
-- additions, so as in 0039, 0050 and 0051 the extraction stamps move.
UPDATE documents
   SET meta = replace(replace(meta, '"core6+', '"core7+'), '"core6"', '"core7"')
 WHERE meta LIKE '%"core6%';

-- What a person said of a face: who it is ('person', the entity and the
-- `depicts` edge it holds up), that it is no face at all ('not_face'), or
-- a face with nobody to name ('nobody': a stranger in a crowd, a face on
-- a poster). One live answer a face; a changed answer ends the old one
-- (ended_at), never deletes it. A face is said of, not a group: the
-- groups are made again every night (migration 55), the faces stay.
-- Several faces may hold up one edge (a person named in a picture twice,
-- or once a face and once again after an undo); the edge is ended when
-- the last live answer holding it up is.
CREATE TABLE face_names (
    id        INTEGER PRIMARY KEY,
    face_id   INTEGER NOT NULL REFERENCES faces(id),
    verdict   TEXT NOT NULL CHECK (verdict IN ('person', 'not_face', 'nobody')),
    entity_id INTEGER REFERENCES entities(id),
    edge_id   INTEGER REFERENCES edges(id),
    by        TEXT NOT NULL,
    run       TEXT NOT NULL,
    said_at   TEXT NOT NULL,
    ended_at  TEXT,
    CHECK ((verdict = 'person') = (entity_id IS NOT NULL))
);
CREATE UNIQUE INDEX face_names_live ON face_names(face_id) WHERE ended_at IS NULL;
CREATE INDEX face_names_run ON face_names(run);
CREATE INDEX face_names_entity ON face_names(entity_id) WHERE ended_at IS NULL;
