-- A quote looked for and not found may be found by a later matcher (AV,
-- 2026-11-17). Of the live edges whose quote the `places` pass looked
-- for, 196,119 were not found; in a sample of 400 from open papers, about
-- 40% were the text's own words behind quotation marks, Markdown or an
-- ellipsis, which `prax.text.quotes` version 2 finds. Migration 48 wrote
-- "not found" once and for good, so none of them could be placed.
--
-- A place that was found stays where it stood. "Not found" (the text's
-- hash and no range) may become a range in that same text, once.
-- `evidence_looked` is the matcher's version that last looked for an
-- unplaced quote, so the pass looks again once per version, not nightly.
ALTER TABLE edges ADD COLUMN evidence_looked INTEGER;

DROP TRIGGER IF EXISTS edges_evidence_place_fixed;

CREATE TRIGGER IF NOT EXISTS edges_evidence_place_fixed
BEFORE UPDATE OF evidence_start, evidence_end, evidence_text_hash ON edges
WHEN OLD.evidence_start IS NOT NULL
  OR (OLD.evidence_text_hash IS NOT NULL
      AND (NEW.evidence_text_hash IS NOT OLD.evidence_text_hash
           OR (NEW.evidence_start IS NULL) != (NEW.evidence_end IS NULL)
           OR (NEW.evidence_start IS NOT NULL
               AND NOT (0 <= NEW.evidence_start
                        AND NEW.evidence_start <= NEW.evidence_end))))
BEGIN
    SELECT RAISE(ABORT, 'where an edge''s quote stood is written once');
END;
