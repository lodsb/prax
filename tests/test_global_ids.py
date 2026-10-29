"""Global ids (plan item AP, ``docs/global-ids.md``, migration 64): an
entity's and a page's, minted once by the database for every row any
writer makes, kept through a merge and an edit."""

from __future__ import annotations

import re
import sqlite3

import pytest

from prax import store

GID = re.compile(r"^[ep]:[0-9A-HJKMNP-TV-Z]{26}$")


def _entity(con: sqlite3.Connection, name: str, etype: str = "concept") -> int:
    return int(
        con.execute(
            "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
        ).fetchone()[0]
    )


def test_every_entity_and_page_has_one_and_no_two_share_it(
    con: sqlite3.Connection,
) -> None:
    doc = int(store.ingest_text(con, "Wave digital filters. " * 20)["doc_id"])
    for dst in ("wave digital filter", "circuit simulation", "adaptor"):
        store.link(
            con,
            store.Edge("WDF paper", "paper", "uses", dst, "concept"),
            source_doc=doc,
        )
    # a writer that is not store.link, as a migration or a script would be
    con.execute("INSERT INTO entities (name, type) VALUES ('by hand', 'concept')")
    store.write_page(con, "notes", "# Notes\n\nFirst.")
    store.write_page(con, "more-notes", "# More\n\nSecond.")
    rows = con.execute("SELECT gid FROM entities").fetchall()
    pages = con.execute("SELECT gid FROM pages").fetchall()
    gids = [r[0] for r in rows] + [r[0] for r in pages]
    assert len(rows) == 5 and len(pages) == 2
    assert all(g and GID.match(g) for g in gids)
    assert {g[:2] for g in (r[0] for r in rows)} == {"e:"}
    assert {g[:2] for g in (r[0] for r in pages)} == {"p:"}
    assert len(set(gids)) == len(gids)


def test_a_gid_is_minted_once(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO entities (name, type) VALUES ('fixed', 'concept')")
    with pytest.raises(sqlite3.IntegrityError, match="minted once"):
        con.execute("UPDATE entities SET gid = 'e:other' WHERE name = 'fixed'")
    store.write_page(con, "notes", "# Notes\n\nFirst.")
    with pytest.raises(sqlite3.IntegrityError, match="minted once"):
        con.execute("UPDATE pages SET gid = 'p:other'")


def test_a_folded_twin_keeps_its_gid_and_answers_as_the_survivor(
    con: sqlite3.Connection,
) -> None:
    for name in ("wave digital filter", "wave-digital filter"):
        con.execute("INSERT INTO entities (name, type) VALUES (?, 'concept')", (name,))
    keep, twin = (
        _entity(con, "wave digital filter"),
        _entity(con, "wave-digital filter"),
    )
    gids = store.gids_of_entities(con, [keep, twin])
    store.merge_entities(con, twin, keep, producer="t", run="m1")
    assert store.gids_of_entities(con, [keep, twin]) == gids
    assert store.entities_by_gid(con, list(gids.values())) == {
        gids[keep]: keep,
        gids[twin]: keep,
    }
    store.unmerge_run(con, "m1")
    assert store.entities_by_gid(con, [gids[twin]]) == {gids[twin]: twin}
    assert store.entities_by_gid(con, ["e:" + "Z" * 26]) == {}


def test_a_page_keeps_its_gid_through_its_revisions(con: sqlite3.Connection) -> None:
    first = store.write_page(con, "notes", "# Notes\n\nFirst.")
    gid = con.execute("SELECT gid FROM pages").fetchone()[0]
    again = store.write_page(con, "notes", "# Notes\n\nFirst, and then more.")
    assert again["revision"] == 2
    assert con.execute("SELECT gid FROM pages").fetchone()[0] == gid
    assert store.page_by_gid(con, gid) == first["doc_id"] == again["doc_id"]
    assert store.page_by_gid(con, "p:" + "Z" * 26) is None
