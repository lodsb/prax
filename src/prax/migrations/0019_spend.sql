-- What the paid steps cost. Every call to a model that costs money is a
-- row: when, which step asked, which model answered, the document it was
-- about, the tokens, and the money at the price of that moment (a price
-- change never rewrites what was paid). The budget reads the sum of a
-- day and of a month from here (prax.budget); the Jobs view shows it.
-- Nothing is written for a local model: free work needs no ledger.
CREATE TABLE IF NOT EXISTS spend (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL,      -- store.now(), UTC to the second
    step          TEXT NOT NULL,      -- extract, promote, vision, ask, typing, …
    model         TEXT NOT NULL,      -- the runtime name that answered
    doc_id        INTEGER,            -- when the call was about one document
    run           TEXT,               -- the pass it belonged to
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    usd           REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_spend_at ON spend(at);
