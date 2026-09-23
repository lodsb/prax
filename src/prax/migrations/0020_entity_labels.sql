-- The names an entity is known by, with the language each is in and who
-- said so. Two holes this fills, both named in docs/normalization.md.
--
-- A merge carried no provenance: entities.canonical_id says a duplicate
-- is the same thing, and nothing says which pass decided that or when,
-- so a bad round of merging could not be retired the way a bad
-- extraction is (invariant 8 for identity). A label row carries the
-- producer, the run and the entity the name came from, and undoing a run
-- is clearing those pointers again.
--
-- And an alias carried no language, so the canonical name of a thing was
-- whichever spelling arrived first — no use to a German search over an
-- English library, which is where the multilingual work is going
-- (docs/research-multilingual-2026-09-23.md).
CREATE TABLE IF NOT EXISTS entity_labels (
    id          INTEGER PRIMARY KEY,
    entity_id   INTEGER NOT NULL REFERENCES entities(id),
    label       TEXT NOT NULL,
    lang        TEXT,                        -- ISO 639-1; NULL: nobody knows
    kind        TEXT NOT NULL DEFAULT 'alt', -- pref (one per language) | alt
    from_entity INTEGER REFERENCES entities(id),  -- the merge it came from
    source_doc  INTEGER REFERENCES documents(id),
    producer    TEXT,                        -- resolution, an importer, a person
    run         TEXT,                        -- the pass, for retiring it
    confidence  TEXT,                        -- EXTRACTED | INFERRED | AMBIGUOUS
    at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_labels_one
    ON entity_labels(entity_id, label, coalesce(lang, ''));
CREATE INDEX IF NOT EXISTS idx_entity_labels_label ON entity_labels(label);
CREATE INDEX IF NOT EXISTS idx_entity_labels_run ON entity_labels(run);
