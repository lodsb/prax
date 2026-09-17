-- A likely pair an adjudicator said no to stays in the table as decided,
-- so the next computation of its type does not ask about it again (the
-- question costs money): decided is 'different', decided_by the model or
-- person, at the moment. A pair merged needs no mark: one side gains a
-- canonical_id and the pair leaves the plan by that.
ALTER TABLE entity_candidates ADD COLUMN decided TEXT;
ALTER TABLE entity_candidates ADD COLUMN decided_by TEXT;
