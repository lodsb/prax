-- A world date and its precision are one thing (AL, "as Utopia does
-- it"; 2026-10-05): a year is four characters, a month seven, a day ten,
-- and a fact that ended at a date nobody gives has no `world_to` and the
-- precision `unknown`. Migration 36 checked each column alone; a table
-- CHECK across columns cannot be added to a table that exists, so these
-- triggers hold the pair together, as migration 35's hold an edge's fact.
-- `store.link` writes them right already: no live edge broke the rule
-- when it was added (0 of 271 dated edges).
CREATE TRIGGER IF NOT EXISTS edges_world_dates_fit
BEFORE INSERT ON edges
WHEN NOT (
    (NEW.world_from IS NULL) = (NEW.world_from_precision IS NULL)
    AND (NEW.world_from IS NULL OR length(NEW.world_from) =
        CASE NEW.world_from_precision WHEN 'year' THEN 4 WHEN 'month' THEN 7
        WHEN 'day' THEN 10 END)
    AND CASE
        WHEN NEW.world_to_precision = 'unknown' THEN NEW.world_to IS NULL
        WHEN NEW.world_to IS NULL THEN NEW.world_to_precision IS NULL
        ELSE length(NEW.world_to) =
            CASE NEW.world_to_precision WHEN 'year' THEN 4 WHEN 'month' THEN 7
            WHEN 'day' THEN 10 END
    END
)
BEGIN
    SELECT RAISE(ABORT, 'a world date and its precision must fit (year 4, month 7, day 10; unknown: no date)');
END;

CREATE TRIGGER IF NOT EXISTS edges_world_dates_fixed
BEFORE UPDATE OF world_from, world_from_precision, world_to, world_to_precision ON edges
BEGIN
    SELECT RAISE(ABORT, 'an edge''s world dates do not change: end it and link anew');
END;
