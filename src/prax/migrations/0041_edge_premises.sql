-- What a derived edge follows from (stage AN, 2026-10-04): the rule pass
-- writes an edge only with its premises, so "why is this here" has an
-- answer, and a derivation whose premises are gone is ended on the next
-- pass. An edge of the graph is evidence (invariant 8); a derived one is
-- INFERRED, signed by its rule (producer `rule:<kind>`, run
-- `rule:<relation>`), and this table is its evidence.
CREATE TABLE edge_premises (
    edge_id    INTEGER NOT NULL REFERENCES edges(id),
    premise_id INTEGER NOT NULL REFERENCES edges(id),
    PRIMARY KEY (edge_id, premise_id)
);

CREATE INDEX idx_edge_premises_premise ON edge_premises (premise_id);
