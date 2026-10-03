-- A pair the adjudicator said is one thing, held for a person when the
-- merge would reach far (2026-10-03, after Utopia's execution gate): the
-- reason, or NULL. A held pair stays undecided, leaves the automatic
-- adjudicator's list, and heads the Review page's "same thing?" list.
ALTER TABLE entity_candidates ADD COLUMN held TEXT;
