-- The last id a kind of row was given, for the rows that are replaced
-- whole and must never give an id twice (the review of 2026-10-28): a
-- page shown before the event candidates were found again names a
-- candidate by its id, and a new group given that id would be decided in
-- its place. Rowids are reused once the highest rows are deleted.
CREATE TABLE id_marks (
    name TEXT PRIMARY KEY,
    last INTEGER NOT NULL
);
INSERT INTO id_marks (name, last)
    SELECT 'event_candidates', coalesce(max(id), 0) FROM event_candidates;
