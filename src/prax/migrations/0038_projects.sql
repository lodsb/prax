-- A project's manifest, kept in prax rather than as a `.prax-project` in
-- the repository (stage AL, step 3; 2026-10-03): a committed file tells
-- colleagues, and switched the session-end hook on by being there. One
-- row a project: where its working copy is (the canonical git remote and
-- the folder within the repository, so a checkout elsewhere or on another
-- machine finds the same project), what is read (`settings`: domains,
-- tags, include, exclude, as JSON), whether the session-end hook syncs it
-- on its own, and the last sync's counts.
CREATE TABLE projects (
    name       TEXT PRIMARY KEY,
    remote     TEXT,
    prefix     TEXT NOT NULL DEFAULT '',
    settings   TEXT NOT NULL DEFAULT '{}',
    auto_sync  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    synced_at  TEXT,
    last       TEXT
);

CREATE UNIQUE INDEX idx_projects_where ON projects (remote, prefix)
    WHERE remote IS NOT NULL;
