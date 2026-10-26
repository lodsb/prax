-- Events from photos (2026-10-26, AR's photo stage). Core 5 -> 6 adds
-- `recorded_at` (document to event, schema:recordedAt): only an
-- addition, so as in 0039 and 0050 the extraction stamps move.
UPDATE documents
   SET meta = replace(replace(meta, '"core5+', '"core6+'), '"core5"', '"core6"')
 WHERE meta LIKE '%"core5%';

-- A group of photos taken close together (store.find_event_candidates):
-- a candidate until a person says it is an event, named (state 'event',
-- its entity and the run its facts were written under), or not one
-- ('not'). An open candidate is found again by every pass; a decided one
-- is kept, so a group is asked once.
CREATE TABLE event_candidates (
    id          INTEGER PRIMARY KEY,
    first_taken TEXT NOT NULL,
    last_taken  TEXT NOT NULL,
    photos      INTEGER NOT NULL,
    suggestion  TEXT NOT NULL,
    cues        TEXT NOT NULL DEFAULT '{}',  -- JSON: place, folder
    state       TEXT NOT NULL DEFAULT 'open'
                CHECK (state IN ('open', 'event', 'not')),
    name        TEXT,
    entity_id   INTEGER REFERENCES entities(id),
    run         TEXT,
    found_at    TEXT NOT NULL,
    decided_at  TEXT
);
CREATE INDEX event_candidates_state ON event_candidates(state, first_taken);

CREATE TABLE event_candidate_photos (
    candidate_id INTEGER NOT NULL REFERENCES event_candidates(id) ON DELETE CASCADE,
    doc_id       INTEGER NOT NULL REFERENCES documents(id),
    PRIMARY KEY (candidate_id, doc_id)
);
CREATE INDEX event_candidate_photos_doc ON event_candidate_photos(doc_id);
