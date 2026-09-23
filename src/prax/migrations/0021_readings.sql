-- A document's reading requests, as a queue rather than one slot.
--
-- A reading was a single field on the document (`meta.reading`), so
-- asking for one replaced whatever was waiting: a figures request
-- displaced the formulas the door had queued behind a marker read, and a
-- bulk request over the library wiped every pending reading it touched.
-- The plan has carried this since 2026-09-20 ("worth a queue some day");
-- the crop pass, which every PDF wants alongside its other readings,
-- made it due.
--
-- The table is the truth for what waits and what happened. `meta.reading`
-- stays as the last finished reading of a document, which is what the
-- page shows and what the ailments read.
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY,
    doc_id      INTEGER NOT NULL REFERENCES documents(id),
    extractor   TEXT NOT NULL,
    mode        TEXT,
    asked_by    TEXT NOT NULL DEFAULT 'human',   -- human | door | a rule
    state       TEXT NOT NULL DEFAULT 'requested',  -- requested | done | error
    at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    finished_at TEXT,
    outcome     TEXT,                            -- the parse action
    stamp       TEXT,                            -- what read it
    error       TEXT
);

-- one pending request per document and extractor and mode: asking twice
-- changes nothing, asking for another reading queues beside it
CREATE UNIQUE INDEX IF NOT EXISTS idx_readings_one
    ON readings(doc_id, extractor, coalesce(mode, ''))
    WHERE state = 'requested';
CREATE INDEX IF NOT EXISTS idx_readings_waiting
    ON readings(id) WHERE state = 'requested';
CREATE INDEX IF NOT EXISTS idx_readings_doc ON readings(doc_id);
CREATE INDEX IF NOT EXISTS idx_readings_finished ON readings(finished_at);

-- what is waiting now, moved out of the documents' meta so nothing is
-- lost in the change; a finished one stays where it is, as history
INSERT INTO readings (doc_id, extractor, mode, asked_by, state, at)
SELECT id,
       json_extract(meta, '$.reading.extractor'),
       json_extract(meta, '$.reading.mode'),
       coalesce(json_extract(meta, '$.reading.by'), 'human'),
       'requested',
       coalesce(json_extract(meta, '$.reading.at'),
                strftime('%Y-%m-%dT%H:%M:%SZ','now'))
FROM documents
WHERE json_extract(meta, '$.reading.state') = 'requested'
  AND json_extract(meta, '$.reading.extractor') IS NOT NULL;
