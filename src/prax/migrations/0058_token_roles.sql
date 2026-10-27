-- What a named token may do (2026-10-27, the wall): a 'reader' reads, and
-- asks only where no paid model answers; a 'contributor' also adds to the
-- library and changes it (captures, pages, links, titles, modules,
-- readings), which is what every named token could do until now, so the
-- tokens made before this keep it. A new token is a reader unless told.
ALTER TABLE tokens ADD COLUMN role TEXT NOT NULL DEFAULT 'contributor'
    CHECK (role IN ('reader', 'contributor'));
