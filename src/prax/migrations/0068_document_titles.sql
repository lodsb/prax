-- A document found by its title (the review of 2026-11-03, quality 1): a
-- page's members and the documents it annotates are entities named by a
-- title, and no index served one, so each lookup scanned every document,
-- meta and all (50 ms on the live store of 33,104; get_page of a project
-- page 105 ms). The titles are some 1.2 MB.
CREATE INDEX IF NOT EXISTS idx_documents_title ON documents(title);
