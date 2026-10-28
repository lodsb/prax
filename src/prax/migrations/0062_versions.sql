-- Versions of a work (2026-10-28, plan item AS). Core 7 -> 8 adds
-- `example_of` (a document to the work it is a version of): only an
-- addition, so as in 0039, 0050, 0051 and 0056 the extraction stamps move.
UPDATE documents
   SET meta = replace(replace(meta, '"core7+', '"core8+'), '"core7"', '"core8"')
 WHERE meta LIKE '%"core7%';

-- Two live documents that are probably one work in two versions (a
-- preprint and its journal version) or one text twice: found by the
-- `versions` pass of `prax maintain` (`store.find_versions`), decided by
-- a person. `a < b`, one row a pair, never deleted once decided, so a
-- pair a person answered is not asked again. `overlap` is of the two
-- texts' shingles (`prax.text.shingles`), read from `a_text` and `b_text`
-- (the text artifacts' hashes, so a re-parse is measured again).
-- AUTOINCREMENT: an id is never given twice, so a page shown before a
-- pass never decides another pair.
CREATE TABLE version_candidates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    a          INTEGER NOT NULL REFERENCES documents(id),
    b          INTEGER NOT NULL REFERENCES documents(id),
    overlap    REAL NOT NULL,
    reasons    TEXT NOT NULL,   -- JSON: why the two were paired
    a_text     TEXT,
    b_text     TEXT,
    found_at   TEXT NOT NULL,
    verdict    TEXT CHECK (verdict IN ('version', 'copy', 'different')),
    decided_at TEXT,
    run        TEXT,            -- the decision's run: its edges, its retirement
    CHECK (a < b),
    UNIQUE (a, b)
);
CREATE INDEX version_candidates_open ON version_candidates(verdict, overlap DESC);
