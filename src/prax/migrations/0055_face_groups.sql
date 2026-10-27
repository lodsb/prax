-- The faces that are probably one person (2026-10-27, plan item AT step
-- 3): groups by density over the face vectors (prax.ml.clusters), made
-- whole again by the facegroups pass of prax maintain. A group is a
-- candidate, like entity_candidates, and says nothing a person said: a
-- name (step 4) is a fact about a face, so these rows may be replaced on
-- every run. A new group takes the id of the old one it overlaps most
-- (Jaccard 0.5 or more), so a group keeps its id while its faces come
-- and go. pictures and doubled count the distinct pictures and those
-- with two of its faces: a group with a doubled picture is surely more
-- than one person.
CREATE TABLE face_groups (
    id          INTEGER PRIMARY KEY,
    size        INTEGER NOT NULL,
    pictures    INTEGER NOT NULL,
    doubled     INTEGER NOT NULL,
    persistence REAL NOT NULL,     -- HDBSCAN's stability of the group
    model       TEXT NOT NULL,     -- the face vectors' model
    run         TEXT NOT NULL,
    built_at    TEXT NOT NULL
);
-- a face is in one group at most; strength 0 to 1, how firmly
CREATE TABLE face_group_members (
    face_id  INTEGER PRIMARY KEY REFERENCES faces(id),
    group_id INTEGER NOT NULL REFERENCES face_groups(id),
    strength REAL NOT NULL
);
CREATE INDEX face_group_members_group ON face_group_members(group_id, strength DESC);
