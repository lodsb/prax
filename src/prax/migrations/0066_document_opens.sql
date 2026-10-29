-- Which documents were opened, and when (plan item "Reading activity as a
-- signal", docs/PLAN.md): the first of the outcome signals, recorded now
-- so a later measurement has months to stand on. Nothing ranks by it yet.
--
-- One row an opening: the document, who opened it (`viewer`: a named
-- token's name, or `admin` for the administrator's token and the web page
-- signed in with it) and through what (`via`: `web` for the document page,
-- `agent` for an agent's brief read, `api` for any other read of the whole
-- document). The door writes at most one row a document, viewer and way
-- in `OPEN_EVERY` (store.documents.opens), so the page's live refresh and
-- an agent reading a book in windows count once.
CREATE TABLE document_opens (
    id        INTEGER PRIMARY KEY,
    doc_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    viewer    TEXT NOT NULL,
    via       TEXT NOT NULL CHECK (via IN ('web', 'agent', 'api')),
    opened_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX document_opens_by_doc ON document_opens(doc_id, opened_at);
CREATE INDEX document_opens_by_time ON document_opens(opened_at);
