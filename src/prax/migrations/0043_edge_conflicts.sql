-- Two facts that cannot both hold (stage AN, 2026-10-04): a relation the
-- ontology calls functional (`published_in`: one venue) with two values
-- for one subject, neither a part of the other. A disagreement is kept
-- visible beside the edges, never resolved by ending one of them: an
-- edge is evidence (invariant 8), and which of two documents is right is
-- a person's call. Beside the edges as `edge_premises` is, because an
-- edge of the graph joins two entities and a conflict joins two facts.
-- Written by the `conflicts` pass of `prax maintain` (producer
-- `rule:functional`, run `conflicts:<relation>`); a conflict whose facts
-- no longer disagree is ended (`ended_at`), never deleted.
CREATE TABLE edge_conflicts (
    id        INTEGER PRIMARY KEY,
    edge_a    INTEGER NOT NULL REFERENCES edges(id),
    edge_b    INTEGER NOT NULL REFERENCES edges(id),
    rel       TEXT NOT NULL,
    producer  TEXT NOT NULL,
    run       TEXT NOT NULL,
    found_at  TEXT NOT NULL,
    ended_at  TEXT
);

CREATE UNIQUE INDEX idx_edge_conflicts_open ON edge_conflicts (edge_a, edge_b)
    WHERE ended_at IS NULL;
CREATE INDEX idx_edge_conflicts_b ON edge_conflicts (edge_b) WHERE ended_at IS NULL;
