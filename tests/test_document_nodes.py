"""Which document a node is (structure review finding 17): a page's node
has one type whatever writes to it, the document is kept on the entity
when an edge first reaches it (migration 69), and the way back from a node
reads it instead of matching titles."""

from __future__ import annotations

import sqlite3

from prax import store
from prax.store.base import _claim_document_nodes
from prax.store.repair.graph import (
    _page_node_twins,
    _repair_page_node_twins,
    _repair_unlinked_document_nodes,
    _unlinked_document_nodes,
)


def _doc(con: sqlite3.Connection, text: str, title: str) -> int:
    doc = int(
        store.register(con, text.encode(), mime="text/plain", title=title)["doc_id"]
    )
    store.index_text(con, doc, text)
    return doc


def _doc_of(con: sqlite3.Connection, name: str, etype: str) -> int | None:
    row = con.execute(
        "SELECT doc_id FROM entities WHERE name = ? AND type = ?", (name, etype)
    ).fetchone()
    return None if row is None else row[0]


def test_a_project_page_is_one_node_whatever_links_it(con: sqlite3.Connection) -> None:
    project = store.write_page(con, "lab", "# Lab\n\nThe lab's work.", kind="project")
    pid = int(project["doc_id"])
    assert store.document_node(con, pid) == ("Lab", "project")
    page = store.write_page(con, "log", "# Log\n\nNotes.", part_of="lab")
    paper = _doc(con, "A paper the lab uses. " * 20, "Paper")
    store.add_to_project(con, "lab", paper)
    # a write, a membership and a link by the document's id meet on one node
    store.link(
        con,
        store.Edge("Log", "page", "part_of", *store.document_node(con, pid)),
        source_doc=int(page["doc_id"]),
    )
    assert [
        tuple(r)
        for r in con.execute(
            "SELECT type, doc_id FROM entities WHERE name = 'Lab' ORDER BY type"
        )
    ] == [("project", pid)]
    members = store.get_page(con, "lab", text=False)["members"]["items"]
    assert {(m["title"], m["doc_id"]) for m in members} == {
        ("Log", int(page["doc_id"])),
        ("Paper", paper),
    }


def test_the_document_is_kept_and_read_back(con: sqlite3.Connection) -> None:
    doc = _doc(con, "On wavelets, by Ada. " * 20, "Wavelets")
    store.link(
        con,
        store.Edge("Wavelets", "paper", "authored_by", "Ada", "person"),
        source_doc=doc,
    )
    assert _doc_of(con, "Wavelets", "paper") == doc
    assert _doc_of(con, "Ada", "person") is None  # no document type
    assert store.node_document(con, "Wavelets", "paper") == doc
    assert store.node_document(con, "Nothing", "paper") is None
    # a retitled document's node keeps its document under the new name
    store.retitle(con, doc, "Wavelets for beginners", source="human")
    assert store.node_document(con, "Wavelets for beginners", "paper") == doc


def test_two_documents_of_one_title_keep_their_own(con: sqlite3.Connection) -> None:
    first = _doc(con, "The first introduction. " * 20, "Introduction")
    second = _doc(con, "The second introduction. " * 20, "Introduction")
    # the second's own facts reach the shared node first: it is the second's
    store.link(
        con,
        store.Edge("Introduction", "paper", "authored_by", "Bob", "person"),
        source_doc=second,
    )
    assert _doc_of(con, "Introduction", "paper") == second
    store.link(
        con,
        store.Edge("Introduction", "paper", "authored_by", "Eve", "person"),
        source_doc=first,
    )
    assert _doc_of(con, "Introduction", "paper") == second  # kept, not moved


def test_the_upgrade_links_the_nodes_written_before_it(
    con: sqlite3.Connection,
) -> None:
    doc = _doc(con, "A datasheet. " * 30, "XR2206")
    other = _doc(con, "Another. " * 30, "Shared")
    later = _doc(con, "Another, later. " * 30, "Shared")
    con.executemany(
        "INSERT INTO entities (name, type) VALUES (?, ?)",
        [("XR2206", "paper"), ("Shared", "paper"), ("Nobody's", "paper")],
    )
    shared = con.execute("SELECT id FROM entities WHERE name = 'Shared'").fetchone()[0]
    # the later namesake states the shared node's facts: it is that one's
    con.execute(
        "INSERT INTO entities (name, type) VALUES ('Carol', 'person')",
    )
    carol = con.execute("SELECT id FROM entities WHERE name = 'Carol'").fetchone()[0]
    con.execute(
        "INSERT INTO edges (src, dst, rel, confidence, source_doc)"
        " VALUES (?, ?, 'authored_by', 'EXTRACTED', ?)",
        (shared, carol, later),
    )
    con.commit()
    assert _claim_document_nodes(con) == 2
    assert _doc_of(con, "XR2206", "paper") == doc
    assert _doc_of(con, "Shared", "paper") == later != other
    assert _doc_of(con, "Nobody's", "paper") is None


def test_the_heal_links_a_cited_node_and_folds_a_pages_twin(
    con: sqlite3.Connection,
) -> None:
    # a cited title named before its document came
    con.execute("INSERT INTO entities (name, type) VALUES ('Cited later', 'paper')")
    con.commit()
    doc = _doc(con, "The cited paper, here now. " * 20, "Cited later")
    found = _unlinked_document_nodes(con)
    assert [(r["name"], r["doc_id"]) for r in found] == [("Cited later", doc)]
    assert _repair_unlinked_document_nodes(con, found) == 1
    assert _unlinked_document_nodes(con) == []
    # a project page's 'page' node, as a link by its document id wrote one
    project = store.write_page(con, "lab", "# Lab\n\nWork.", kind="project")
    pid = int(project["doc_id"])
    store.link(
        con,
        store.Edge("Lab", "project", "part_of", "Group", "organization"),
        source_doc=pid,
    )
    con.execute(
        "INSERT INTO entities (name, type, doc_id) VALUES ('Lab', 'page', ?)", (pid,)
    )
    con.commit()
    twins = _page_node_twins(con)
    assert [(r["type"], r["doc_id"]) for r in twins] == [("page", pid)]
    assert _repair_page_node_twins(con, twins) == 1
    merged = con.execute(
        "SELECT canonical_id, merged_run FROM entities WHERE name = 'Lab'"
        " AND type = 'page'"
    ).fetchone()
    assert merged[0] == twins[0]["into"]
    assert merged[1].startswith("heal:page-node-twins:")
    assert _page_node_twins(con) == []


def test_a_retired_copy_hands_its_node_to_the_live_one(con: sqlite3.Connection) -> None:
    """A note synced again is a new document and the old one is retired as
    its duplicate; the node moves to the live copy, where it kept the
    retired one (the safety review of 2026-11-11)."""
    old = _doc(con, "Plan, first version. " * 20, "Plan")
    store.link(
        con,
        store.Edge("Plan", "paper", "authored_by", "Ada", "person"),
        source_doc=old,
        producer="test",
    )
    assert _doc_of(con, "Plan", "paper") == old
    new = _doc(con, "Plan, second version. " * 20, "Plan")
    store.retire_document(con, old, reason="synced again", duplicate_of=new)
    assert _doc_of(con, "Plan", "paper") == new
    assert store.node_document(con, "Plan", "paper") == new


def test_a_pictures_node_outlives_the_text_it_is_given(
    con: sqlite3.Connection,
) -> None:
    """A picture's node is named by its file and number while it has no
    text; once a reading gives it one, its node is still the one kept on
    the entity (migration 69), not a new one by its title (the quality
    review of 2026-11-11, finding 2: its facts dropped out of the
    document's reads)."""
    one = int(
        store.register(con, b"jpeg one " * 20, mime="image/jpeg", title="IMG_0001.JPG")[
            "doc_id"
        ]
    )
    two = int(
        store.register(con, b"jpeg two " * 20, mime="image/jpeg", title="IMG_0001.JPG")[
            "doc_id"
        ]
    )
    name, etype = store.document_node(con, one)
    assert name == f"IMG_0001.JPG (#{one})"
    store.link(
        con,
        store.Edge(name, etype, "taken_in", "Aldmouth", "place"),
        source_doc=one,
        producer="photos",
        run="t",
    )
    before = store.doc_node_ids(con, one)
    assert len(before) == 1

    store.index_text(con, one, "A harbour at dusk, two boats.")
    assert store.document_node(con, one) == (name, etype)
    assert store.doc_node_ids(con, one) == before
    # the other camera's picture of the same file name is still its own
    assert store.document_node(con, two)[0] == f"IMG_0001.JPG (#{two})"
