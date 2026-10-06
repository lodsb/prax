-- Where an edge's quote stood (the plan's "evidence that knows where it
-- stood", after Utopia's offsets and document version; 2026-10-06): the
-- character range of `evidence` in the text artifact it was found in, and
-- that artifact's hash. A re-read of the document then says which
-- evidence still stands where it was and which moved (`store.evidence_place`).
-- The range is in characters of the text, as a chunk's locator is.
--
-- Written once, like the fact itself (migration 35): a place may be filled
-- where there was none (the `places` pass, for edges written before this),
-- and never changed after.
ALTER TABLE edges ADD COLUMN evidence_start INTEGER;
ALTER TABLE edges ADD COLUMN evidence_end INTEGER;
ALTER TABLE edges ADD COLUMN evidence_text_hash TEXT;

CREATE TRIGGER IF NOT EXISTS edges_evidence_place_fixed
BEFORE UPDATE OF evidence_start, evidence_end, evidence_text_hash ON edges
WHEN OLD.evidence_text_hash IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'where an edge''s quote stood is written once');
END;

-- A range, or none at all: the `places` pass records a quote it looked
-- for and did not find as the text's hash with no range
CREATE TRIGGER IF NOT EXISTS edges_evidence_place_fits
BEFORE INSERT ON edges
WHEN NOT (
    (NEW.evidence_start IS NULL AND NEW.evidence_end IS NULL)
    OR (NEW.evidence_text_hash IS NOT NULL
        AND 0 <= NEW.evidence_start AND NEW.evidence_start <= NEW.evidence_end)
)
BEGIN
    SELECT RAISE(ABORT, 'a quote''s place is a range in its text');
END;
