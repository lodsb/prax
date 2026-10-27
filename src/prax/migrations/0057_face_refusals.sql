-- "Not this person" (2026-10-27, plan item AT step 5): a face the
-- matching offered as someone and a person said it is not them. The face
-- is never offered as them again; it stays open to be named anyone else.
-- Ended (ended_at) by an undo of its action, or when the face is named as
-- that person after all; never deleted.
CREATE TABLE face_refusals (
    id        INTEGER PRIMARY KEY,
    face_id   INTEGER NOT NULL REFERENCES faces(id),
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    by        TEXT NOT NULL,
    run       TEXT NOT NULL,
    said_at   TEXT NOT NULL,
    ended_at  TEXT
);
CREATE UNIQUE INDEX face_refusals_live ON face_refusals(face_id, entity_id)
    WHERE ended_at IS NULL;
CREATE INDEX face_refusals_run ON face_refusals(run);
