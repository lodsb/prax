-- A global id for an entity and a page (plan item AP, docs/global-ids.md):
-- what means the same row in another library, where the integer key is
-- this store's alone. Minted once, never derived: an entity's name changes
-- and two libraries' equal names are not one thing; a page's slug may be
-- another library's too and every revision has a new hash. `e:` or `p:`
-- and 26 Crockford base32 characters, 130 random bits.
--
-- SQLite's random() is the same generator as randomblob(); `& 31` takes
-- five bits of it a character (abs() would overflow on the one negative
-- number it has no positive for). A trigger mints every row any writer
-- makes, so no code path can make one without; a merge keeps both rows,
-- and the folded row keeps its gid, an alias found through canonical_id.
--
-- A document's global id is its hash (invariant 2), and an edge's is
-- computed from its document's hash, its relation and its two ends' gids
-- when an export writes it (prax.graph.graphio): its document may still be
-- mended (a duplicate's edges move to the survivor), so a stored one would
-- go stale.
ALTER TABLE entities ADD COLUMN gid TEXT;
ALTER TABLE pages ADD COLUMN gid TEXT;

UPDATE entities SET gid = 'e:'
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1);

UPDATE pages SET gid = 'p:'
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1);

CREATE UNIQUE INDEX entities_by_gid ON entities(gid);
CREATE UNIQUE INDEX pages_by_gid ON pages(gid);

CREATE TRIGGER entities_gid_minted
AFTER INSERT ON entities WHEN NEW.gid IS NULL
BEGIN
    UPDATE entities SET gid = 'e:'
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
    WHERE id = NEW.id;
END;

CREATE TRIGGER pages_gid_minted
AFTER INSERT ON pages WHEN NEW.gid IS NULL
BEGIN
    UPDATE pages SET gid = 'p:'
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
        || substr('0123456789ABCDEFGHJKMNPQRSTVWXYZ', (random() & 31) + 1, 1)
    WHERE doc_id = NEW.doc_id;
END;

CREATE TRIGGER entities_gid_fixed
BEFORE UPDATE OF gid ON entities WHEN OLD.gid IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'an entity''s gid is minted once (docs/global-ids.md)');
END;

CREATE TRIGGER pages_gid_fixed
BEFORE UPDATE OF gid ON pages WHEN OLD.gid IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'a page''s gid is minted once (docs/global-ids.md)');
END;
