-- What this library decided about another library's entities (plan item
-- AP, docs/global-ids.md, "The import"): a row says "their e:... is our
-- entity N", or that it is a new one here, or that it is left out. A
-- graph file of prax-graph/2 names its entities by gid; the second import
-- of a source lands where the first was decided.
--
-- state: `likely` (a name here answers to theirs, `candidate_id`; it waits
-- on the Review page), `same` (theirs is our `entity_id`), `new` (nothing
-- here was it; `entity_id` once an edge made it), `left_out` (their edges
-- that reach it are not written). `decided_by` is `import` for what the
-- import settled by itself (a new name), else who answered.
CREATE TABLE foreign_entities (
    source       TEXT NOT NULL,              -- the import's name
    gid          TEXT NOT NULL,              -- theirs
    name         TEXT NOT NULL,              -- as their file names it
    type         TEXT NOT NULL,
    labels       TEXT,                       -- their labels (JSON), added once decided
    state        TEXT NOT NULL CHECK (state IN ('likely', 'same', 'new', 'left_out')),
    entity_id    INTEGER REFERENCES entities(id),
    candidate_id INTEGER REFERENCES entities(id),
    decided_by   TEXT,
    run          TEXT,                       -- the import that met it
    decided_at   TEXT,
    PRIMARY KEY (source, gid)
);

CREATE INDEX foreign_entities_likely ON foreign_entities(source)
    WHERE state = 'likely';

-- The edges of an import that wait for a likely end to be decided, as the
-- file had them (`edge_id` NULL), and the ones written onto an entity of
-- theirs (`edge_id` the edge), so an answer taken back ends them and holds
-- them again (`store.undo_foreign`). Not evidence: a waiting row goes when
-- its end is left out or the claim is here already, and a newer import of
-- the source holds its own.
CREATE TABLE import_held (
    id         INTEGER PRIMARY KEY,
    source     TEXT NOT NULL,
    run        TEXT NOT NULL,                -- the import's, which its edge is written under
    src_gid    TEXT NOT NULL,
    dst_gid    TEXT NOT NULL,
    source_doc INTEGER REFERENCES documents(id),
    record     TEXT NOT NULL,                -- the edge line (JSON)
    edge_id    INTEGER REFERENCES edges(id), -- written as; NULL while it waits
    held_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX import_held_by_src ON import_held(source, src_gid);
CREATE INDEX import_held_by_dst ON import_held(source, dst_gid);
