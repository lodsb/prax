-- prax canonical schema. Applied idempotently by prax.store.init_db().
-- sqlite-vec table is created separately in code (extension must be loaded).

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    hash          TEXT UNIQUE NOT NULL,          -- sha256 of original file
    mime          TEXT,
    title         TEXT,
    source_url    TEXT,
    original_path TEXT,                          -- pre-migration provenance
    text_hash     TEXT,                          -- sha256 of the parsed-text artifact; NULL until indexed
    added_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    parsed_at     TEXT,
    meta          TEXT                           -- JSON
);

CREATE TABLE IF NOT EXISTS chunks (
    id     INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id),
    seq    INTEGER NOT NULL,
    text   TEXT NOT NULL,
    UNIQUE (doc_id, seq)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content='chunks',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text)
        VALUES ('delete', old.id, old.text);
END;

-- Entities: resolution merges point canonical_id at the survivor.
CREATE TABLE IF NOT EXISTS entities (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    type         TEXT NOT NULL,
    canonical_id INTEGER REFERENCES entities(id),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (name, type)
);

-- Edges: evidence, not truth. Bi-temporal; invalidate, never delete.
CREATE TABLE IF NOT EXISTS edges (
    id               INTEGER PRIMARY KEY,
    src              INTEGER NOT NULL REFERENCES entities(id),
    dst              INTEGER NOT NULL REFERENCES entities(id),
    rel              TEXT NOT NULL,
    confidence       TEXT NOT NULL
                     CHECK (confidence IN ('EXTRACTED','INFERRED','AMBIGUOUS')),
    weight           REAL,
    source_doc       INTEGER REFERENCES documents(id),
    ontology_version TEXT,
    valid_from       TEXT,
    valid_to         TEXT,                       -- NULL = currently valid
    ingested_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
