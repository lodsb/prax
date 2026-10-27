-- The faces in a picture (2026-10-27, plan item AT step 1): each a box
-- and five landmarks as fractions of the upright picture, with the
-- detector's score, found by the faces step (prax.ml.faces) in the
-- picture's preview. A table rather than a chunk kind: a chunk is a
-- region of a document's text and disposable (invariant 2), a face is a
-- region of its picture, and the face vectors, the names a person gives
-- and their "not this person" (steps 2 to 5) need an id that a rechunk
-- does not change. A reading of a later version ends the faces of the
-- one before (gone_at) and writes its own; a face is never deleted, so
-- what a person said about one stays traceable.
CREATE TABLE faces (
    id        INTEGER PRIMARY KEY,
    doc_id    INTEGER NOT NULL REFERENCES documents(id),
    box       TEXT NOT NULL,     -- JSON [x, y, w, h], fractions of the picture
    landmarks TEXT NOT NULL,     -- JSON [[x, y], ...]: eyes, nose, mouth corners
    score     REAL NOT NULL,
    px        INTEGER NOT NULL,  -- the box's width in pixels where it was found
    detector  TEXT NOT NULL,     -- prax.ml.faces.stamp(): model and version
    found_at  TEXT NOT NULL,
    gone_at   TEXT
);
CREATE INDEX faces_doc ON faces(doc_id) WHERE gone_at IS NULL;
