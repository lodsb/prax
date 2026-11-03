-- A page moved or renamed without its text changing (docs/howto.md "Moving
-- and renaming a page"): bookkeeping, recorded in the page's history like
-- any save, but not a write of anyone's words.
--
-- `change` says what a revision did: `text` (every revision before this
-- migration, and every save since), `move` (the page's project or its
-- place in the tree of pages, store.move_page) or `rename` (its slug,
-- store.rename_page). A move or a rename keeps the text hash of the
-- revision before it, and the check that an agent never writes over a
-- person's text reads the last `text` revision, so a person's page an
-- agent moved is still a person's page.
ALTER TABLE page_revisions ADD COLUMN change TEXT NOT NULL DEFAULT 'text'
    CHECK (change IN ('text', 'move', 'rename'));

-- A slug a page had before a rename. It names that page for ever: a read,
-- an append or a write under it reaches the page, and no other page may
-- take it. A chain of renames leaves every old slug here, each naming the
-- page itself, so a resolves as c does after a -> b -> c. Renaming a page
-- back to one of its own old slugs takes that row out.
CREATE TABLE page_aliases (
    slug       TEXT PRIMARY KEY,
    doc_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX page_aliases_by_doc ON page_aliases(doc_id);

-- a page never takes a slug that is another page's old name
CREATE TRIGGER pages_slug_not_an_alias
BEFORE INSERT ON pages
WHEN EXISTS (SELECT 1 FROM page_aliases WHERE slug = NEW.slug)
BEGIN
    SELECT RAISE(ABORT, 'that slug is an old name of a page');
END;

CREATE TRIGGER pages_renamed_not_to_an_alias
BEFORE UPDATE OF slug ON pages
WHEN EXISTS (
    SELECT 1 FROM page_aliases WHERE slug = NEW.slug AND doc_id != NEW.doc_id
)
BEGIN
    SELECT RAISE(ABORT, 'that slug is an old name of another page');
END;
