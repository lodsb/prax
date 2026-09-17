-- The likely tier of entity resolution, computed off the door: a worker
-- embeds the names of a type and posts the pairs that are close; the door
-- keeps them here until a person or an adjudicator decides. A row is an
-- unordered pair (a < b) with the cosine of the names and who computed it;
-- a type's rows are replaced whole when a worker computes that type again.
CREATE TABLE IF NOT EXISTS entity_candidates (
    a        INTEGER NOT NULL REFERENCES entities(id),
    b        INTEGER NOT NULL REFERENCES entities(id),
    type     TEXT    NOT NULL,
    score    REAL    NOT NULL,
    producer TEXT    NOT NULL,
    at       TEXT    NOT NULL,
    PRIMARY KEY (a, b)
);
CREATE INDEX IF NOT EXISTS idx_entity_candidates_type ON entity_candidates(type);
