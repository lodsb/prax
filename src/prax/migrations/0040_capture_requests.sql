-- What the door could not fetch and the browser can (stage AL, step 6;
-- 2026-10-04): a TLS chain the server does not trust, a 403, a bot check.
-- 7 of about 30 captures failed so in the first client's session. The
-- request waits here; the browser extension asks for the waiting ones,
-- fetches each with the person's own session, uploads it by the usual
-- routes with the request's title and domains, and says which request it
-- was (`done`, with the document) or why it could not (`failed`, after a
-- few tries). One waiting request a URL.
CREATE TABLE capture_requests (
    id         INTEGER PRIMARY KEY,
    url        TEXT NOT NULL,
    title      TEXT,
    domains    TEXT,            -- JSON list, as the capture asked
    tags       TEXT,            -- JSON list
    by         TEXT,            -- who asked: agent, cli, url
    note       TEXT,
    why        TEXT NOT NULL,   -- what the door's own fetch answered
    state      TEXT NOT NULL DEFAULT 'waiting'
               CHECK (state IN ('waiting', 'done', 'failed', 'dropped')),
    tries      INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,            -- the extension's, when a try failed
    doc_id     INTEGER REFERENCES documents(id),
    asked_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    done_at    TEXT
);

CREATE UNIQUE INDEX idx_capture_requests_waiting ON capture_requests (url)
    WHERE state = 'waiting';
