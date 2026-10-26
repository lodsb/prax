-- A person's cut through a group of photos (2026-10-27, the events tab):
-- a group starts again at the first photo taken at or after `at`
-- (store.split_event), however short the gap before it. Kept apart from
-- the candidates, which every pass finds again, so the nightly does not
-- join what a person split; store.join_event takes a cut back.
CREATE TABLE event_cuts (
    at      TEXT PRIMARY KEY,  -- the moment taken, as meta.taken.date to the second
    by      TEXT NOT NULL,
    made_at TEXT NOT NULL
);
