-- Stage U, the wall (docs/PLAN.md). A document's sensitivity: NULL for an
-- open one, 'suspected' when a rule thinks it personal, 'personal' when a
-- person says so. A token that may not see personal documents sees
-- neither of the two. A column rather than a meta key: every read of a
-- restricted viewer asks it, and the index keeps that one lookup cheap.
ALTER TABLE documents ADD COLUMN sensitivity TEXT;
CREATE INDEX IF NOT EXISTS idx_documents_sensitivity
    ON documents(sensitivity) WHERE sensitivity IS NOT NULL;

-- Named API tokens beside the administrator's PRAX_TOKEN. Only the sha256
-- of a secret is kept; the secret is shown once, when it is made.
CREATE TABLE IF NOT EXISTS tokens (
    name       TEXT PRIMARY KEY,
    hash       TEXT NOT NULL UNIQUE,
    domains    TEXT,                       -- JSON list of modules; NULL: every module
    personal   INTEGER NOT NULL DEFAULT 0, -- 1: sees personal documents too
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_used  TEXT
);
