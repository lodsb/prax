-- Background jobs: the batch scripts (parse, titles, extract, embed, the
-- inbox watcher, dedupe) announce themselves here so the door and the UI
-- can show what is running on the batch host, how far it is, and what ran
-- lately. A job that stops updating without finishing is shown as stale;
-- the rows are bookkeeping, never a queue: nothing reads them to decide
-- what to do next.
CREATE TABLE jobs (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,          -- parse, titles, extract, embed, inbox-watch, dedupe…
    host        TEXT,
    pid         INTEGER,
    started_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,          -- the heartbeat
    finished_at TEXT,
    status      TEXT NOT NULL,          -- running | done | failed
    done        INTEGER NOT NULL DEFAULT 0,
    total       INTEGER,
    note        TEXT
);
CREATE INDEX jobs_status ON jobs(status, updated_at);
