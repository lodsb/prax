-- What is written once stays as written, said by the database itself
-- (2026-10-03, after Utopia's audit table): a code path that would break
-- it fails loudly instead of changing history quietly.
--
-- An edge is evidence (invariant 8): it is ended (`valid_to`), never
-- deleted, and the fact it states (its two ends, its relation, its
-- confidence, its quote) never changes; a correction ends it and links
-- anew. Its provenance and its document may still be mended: a duplicate's
-- edges move to the survivor, a missing producer is backfilled, a wrong
-- version stamp is repaired.
CREATE TRIGGER IF NOT EXISTS edges_never_deleted
BEFORE DELETE ON edges
BEGIN
    SELECT RAISE(ABORT, 'an edge is ended (valid_to), never deleted (invariant 8)');
END;

CREATE TRIGGER IF NOT EXISTS edges_fact_fixed
BEFORE UPDATE OF src, dst, rel, confidence, evidence ON edges
BEGIN
    SELECT RAISE(ABORT, 'an edge''s fact does not change: end it and link anew');
END;

-- A page's revisions are its history; what was spent is a ledger.
CREATE TRIGGER IF NOT EXISTS page_revisions_kept
BEFORE UPDATE ON page_revisions
BEGIN
    SELECT RAISE(ABORT, 'a page revision is history: write a new one');
END;

CREATE TRIGGER IF NOT EXISTS page_revisions_not_deleted
BEFORE DELETE ON page_revisions
BEGIN
    SELECT RAISE(ABORT, 'a page revision is history: write a new one');
END;

CREATE TRIGGER IF NOT EXISTS spend_kept
BEFORE UPDATE ON spend
BEGIN
    SELECT RAISE(ABORT, 'what was spent is a ledger: add a row');
END;

CREATE TRIGGER IF NOT EXISTS spend_not_deleted
BEFORE DELETE ON spend
BEGIN
    SELECT RAISE(ABORT, 'what was spent is a ledger: add a row');
END;
