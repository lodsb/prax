-- Where an original's bytes lie (plan item AQ, docs/media-by-reference.md,
-- 2026-10-19): every file a crawl saw at a place, held by prax or not. A
-- place is a root, named in prax.yaml (`places.roots`) and mapped to a
-- directory per host, and a path inside it with `/` separators, so a share
-- mounted elsewhere on another host changes no row. A file is its full
-- sha256; a document's places are the rows of its hash, and a place may
-- hold a file that is no document, so there is no foreign key. `mtime` is
-- whole seconds; `gone` is when a crawl of the root no longer found it, and
-- the row stays, so prax can say where a file was.
CREATE TABLE IF NOT EXISTS places (
    root TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    gone TEXT,
    PRIMARY KEY (root, path)
);
CREATE INDEX IF NOT EXISTS idx_places_sha256 ON places(sha256);

-- Whether prax holds the original in the archive. Every document so far
-- is held; a referenced one (stage 3 of AQ) is read from a place.
ALTER TABLE documents ADD COLUMN held INTEGER NOT NULL DEFAULT 1;
