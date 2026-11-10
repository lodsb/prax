-- Which document an entity of a document type is (structure review
-- finding 17, 2026-11-10): a document's node was found from the document
-- by `document_node` and back from the node by its title, in seven places
-- with four rules for a title that several documents share (the lowest
-- id, the highest live one, every namesake). The link is kept once, when
-- an edge first reaches the node (`store.base._claim_node`), and read
-- back by `store.node_document`. A rename keeps it: the node is the same
-- thing under a new name. The nodes written before this migration are
-- linked by `init_db` right after it (`_claim_document_nodes`), by the
-- same rule.
ALTER TABLE entities ADD COLUMN doc_id INTEGER REFERENCES documents(id);

CREATE INDEX entities_by_doc ON entities(doc_id) WHERE doc_id IS NOT NULL;
