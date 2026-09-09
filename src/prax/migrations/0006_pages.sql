-- Pages: living Markdown documents in the store (rationale R15): notes on a
-- document (addenda), ongoing projects, topic pages written by a person or
-- an agent. A page is a documents row (source "wiki", MIME text/markdown),
-- so chunks, the retrieval field, embeddings, extraction and the context
-- column apply unchanged. Its identity is the slug; every save is a new
-- content-addressed text artifact recorded here, append-only, so the
-- page's history is a query. The relationship to other documents is edges
-- (annotates, part_of), never a column.
CREATE TABLE IF NOT EXISTS pages (
    doc_id  INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    slug    TEXT NOT NULL UNIQUE,
    kind    TEXT NOT NULL              -- addendum | project | topic
);

CREATE TABLE IF NOT EXISTS page_revisions (
    id         INTEGER PRIMARY KEY,
    doc_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    revision   INTEGER NOT NULL,
    text_hash  TEXT NOT NULL,          -- the revision's text artifact
    author     TEXT NOT NULL,          -- human | agent
    note       TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (doc_id, revision)
);
