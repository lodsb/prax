-- The regions of the library (prax.communities, docs/communities.md): a
-- partition of the topical entities at two levels, rebuilt by the
-- `communities` pass of `prax maintain`. A derived index like chunks: it
-- may be dropped and rebuilt at any time. A rebuild keeps the id, the
-- label and the summary of the community a new one overlaps most, and
-- marks the summary stale when the members moved.
CREATE TABLE IF NOT EXISTS communities (
    id           INTEGER PRIMARY KEY,
    level        INTEGER NOT NULL,          -- 0: the regions; 1: their parts
    parent       INTEGER,                   -- a part's region
    size         INTEGER NOT NULL,          -- its members
    label        TEXT,                      -- a short name (the summaries step)
    summary      TEXT,
    summary_meta TEXT,                      -- JSON: source, run, at, stale
    run          TEXT NOT NULL,             -- the rebuild that made it
    built_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_communities_level ON communities(level, size DESC);

CREATE TABLE IF NOT EXISTS entity_communities (
    entity_id    INTEGER NOT NULL,          -- a canonical entity
    level        INTEGER NOT NULL,
    community_id INTEGER NOT NULL,
    weight       REAL NOT NULL,             -- its weighted degree: the order members are named in
    PRIMARY KEY (entity_id, level)
);
CREATE INDEX IF NOT EXISTS idx_entity_communities_community
    ON entity_communities(community_id, weight DESC);
