-- The local model's answer to "are these one thing?" as a number
-- (docs/PLAN.md, Q; docs/eval/confidence-2026-09-28.md): the probability,
-- calibrated on a person's decisions, that the pair is one thing, and who
-- measured it. A pair it is sure about is decided as well; one in the
-- uncertain middle keeps the number and stays open for a person, and the
-- adjudicate step does not hand it out again. A type's undecided rows are
-- replaced when its pairs are computed again, and the number goes with them.
ALTER TABLE entity_candidates ADD COLUMN p_same REAL;
ALTER TABLE entity_candidates ADD COLUMN p_by TEXT;
