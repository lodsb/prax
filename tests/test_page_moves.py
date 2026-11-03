"""A page moved to another project or place in the tree, renamed, read in
windows and written against the hash it was read at (``store.move_page``,
``store.rename_page``, migration 67): bookkeeping that never touches a
page's text, and never lifts the rule that an agent does not write over a
person's words."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.store import pages


def _projects(con: sqlite3.Connection) -> tuple[int, int]:
    old = store.write_page(con, "project-old", "# Old\n\nThe old one.", kind="project")
    new = store.write_page(con, "project-new", "# New\n\nThe new one.", kind="project")
    return int(old["doc_id"]), int(new["doc_id"])


def _part_of(con: sqlite3.Connection, doc_id: int) -> list[tuple[str, bool]]:
    """Every part_of edge from the page, its project's name and whether it
    is live."""
    name = con.execute("SELECT title FROM documents WHERE id = ?", (doc_id,))
    title = name.fetchone()[0]
    return [
        (str(r[0]), r[1] is None)
        for r in con.execute(
            "SELECT t.name, x.valid_to FROM edges x JOIN entities s ON s.id = x.src"
            " JOIN entities t ON t.id = x.dst WHERE s.name = ? AND x.rel = 'part_of'"
            " ORDER BY x.id",
            (title,),
        )
    ]


def test_a_move_changes_the_project_and_the_tree_and_not_the_text(
    con: sqlite3.Connection,
) -> None:
    old, new = _projects(con)
    log = store.write_page(
        con, "research-log", "# Log\n\n§1 first.", part_of="project-old", author="agent"
    )
    page = store.get_page(con, "research-log")
    assert page["ancestors"][0]["doc_id"] == old  # its project's page is its parent
    before = page["text_hash"]

    moved = store.move_page(con, "research-log", part_of="project-new", note="nlbank's")
    assert moved["changed"] and moved["part_of"] == {
        "from": ["project-old"],
        "to": ["project-new"],
    }
    assert moved["parent"] == {"from": "project-old", "to": "project-new"}
    page = store.get_page(con, "research-log")
    assert page["text_hash"] == before and page["text"] == "# Log\n\n§1 first."
    last = page["revisions"][-1]
    assert (last["revision"], last["change"], last["note"]) == (2, "move", "nlbank's")
    assert last["text_hash"] == before
    assert [p["slug"] for p in page["projects"]] == ["project-new"]
    assert [a["doc_id"] for a in page["ancestors"]] == [new]
    # the old membership is ended, not deleted: a walk at an earlier moment
    # still finds it
    assert _part_of(con, int(log["doc_id"])) == [
        ("Project old", False),
        ("Project new", True),
    ]
    old_page = store.get_page(con, "project-old", text=False)
    new_page = store.get_page(con, "project-new", text=False)
    assert "Research log" not in [m["title"] for m in old_page["members"]["items"]]
    assert "Research log" in [m["title"] for m in new_page["members"]["items"]]
    assert [c["slug"] for c in new_page["children"]] == ["research-log"]
    assert old_page["children"] == []


def test_a_move_ends_a_membership_linked_by_hand(con: sqlite3.Connection) -> None:
    _projects(con)
    log = store.write_page(con, "log", "# Log\n\nText.", part_of="project-old")
    # what the nlbank agent did with `link` before a move existed
    store.link(
        con,
        store.Edge("Log", "page", "part_of", "Project new", "project"),
        confidence="EXTRACTED",
        source_doc=int(log["doc_id"]),
        evidence="by hand",
        producer="agent",
        run="by-hand",
    )
    assert [p["slug"] for p in store.get_page(con, "log")["projects"]] == [
        "project-old",
        "project-new",
    ]
    moved = store.move_page(con, "log", part_of="project-new")
    assert moved["part_of"]["to"] == ["project-new"] and len(moved["ended_edges"]) == 1
    # the hand-made edge to the new project is kept, the old one ended
    assert _part_of(con, int(log["doc_id"])) == [
        ("Project old", False),
        ("Project new", True),
    ]
    left = store.move_page(con, "log", part_of="")
    assert left["part_of"]["to"] == [] and store.get_page(con, "log")["projects"] == []
    assert left["parent"]["to"] is None  # it sat under its project; now the top


def test_the_live_shape_a_tree_parent_and_a_link_to_the_project_page(
    con: sqlite3.Connection,
) -> None:
    # the nlbank log on 2026-11-03: under the old project's page in the
    # tree with no part_of edge, and linked by hand to the new project's
    # page, which `link` on a document id names a `page`, not a `project`
    _projects(con)
    log = store.write_page(con, "log", "# Log\n\nText.", parent="project-old")
    store.link(
        con,
        store.Edge("Log", "page", "part_of", "Project new", "page"),
        confidence="EXTRACTED",
        source_doc=int(log["doc_id"]),
        evidence="by hand",
        producer="agent",
        run="by-hand",
    )
    page = store.get_page(con, "log", text=False)
    assert [p["slug"] for p in page["projects"]] == ["project-new"]
    assert "Log" in [
        m["title"]
        for m in store.get_page(con, "project-new", text=False)["members"]["items"]
    ]
    moved = store.move_page(con, "log", part_of="project-new")
    assert not moved["ended_edges"]  # the hand-made edge is the membership
    assert moved["parent"] == {"from": "project-old", "to": "project-new"}


def test_a_person_s_page_is_moved_and_stays_theirs(con: sqlite3.Connection) -> None:
    _projects(con)
    store.write_page(
        con,
        "plan",
        "# Plan\n\n## Status\n\nThe person's status.\n",
        part_of="project-old",
        author="human",
    )
    store.move_page(con, "plan", part_of="project-new", author="agent")
    page = store.get_page(con, "plan")
    assert page["author"] == "human" and page["revisions"][-1]["author"] == "agent"
    # the move wrote no one's words: an agent's write is refused as before
    with pytest.raises(PermissionError):
        store.write_page(con, "plan", "# Plan\n\nThe agent's.", author="agent")
    with pytest.raises(PermissionError):
        store.update_section(con, "plan", "Status", "The agent's status.")
    assert [p["author"] for p in store.list_pages(con) if p["slug"] == "plan"] == [
        "human"
    ]


def test_a_move_to_where_the_page_is_and_a_dry_run_write_nothing(
    con: sqlite3.Connection,
) -> None:
    _projects(con)
    store.write_page(con, "log", "# Log\n\nText.", part_of="project-old")
    same = store.move_page(con, "log", part_of="project-old")
    assert not same["changed"] and same["why"]
    plan = store.move_page(con, "log", part_of="project-new", dry_run=True)
    assert plan["changed"] and plan["dry_run"]
    page = store.get_page(con, "log", text=False)
    assert [p["slug"] for p in page["projects"]] == ["project-old"]
    assert len(page["revisions"]) == 1


def test_a_move_is_refused_before_anything_is_written(con: sqlite3.Connection) -> None:
    _projects(con)
    store.write_page(con, "top", "# Top\n\nText.", part_of="project-old")
    store.write_page(con, "below", "# Below\n\nText.", parent="top")
    store.write_page(con, "loose", "# Loose\n\nText.")
    with pytest.raises(KeyError):
        store.move_page(con, "nowhere", parent="")
    with pytest.raises(KeyError):
        store.move_page(con, "top", part_of="project-none")
    with pytest.raises(KeyError):
        store.move_page(con, "top", parent="no-such-page")
    with pytest.raises(ValueError, match="cycle"):
        store.move_page(con, "top", parent="below")
    # a parent outside the page's project, and the reason
    with pytest.raises(ValueError, match="not in project"):
        store.move_page(con, "top", parent="loose")
    with pytest.raises(ValueError):
        store.move_page(con, "project-old", part_of="project-old")
    assert len(store.get_page(con, "top")["revisions"]) == 1
    # a page under a page of the project is in it
    store.write_page(con, "deeper", "# Deeper\n\nText.", part_of="project-old")
    assert store.move_page(con, "deeper", parent="below")["changed"]


def test_a_renamed_page_answers_to_its_old_slugs(con: sqlite3.Connection) -> None:
    store.write_page(con, "physmod-log", "# Log\n\n§1.", author="agent")
    doc = store.page_id(con, "physmod-log")
    renamed = store.rename_page(con, "physmod-log", "nlbank-log", note="its project")
    assert renamed["slug"] == "nlbank-log" and renamed["aliases"] == ["physmod-log"]
    page = store.get_page(con, "physmod-log")
    assert page["doc_id"] == doc and page["slug"] == "nlbank-log"
    assert page["asked_as"] == "physmod-log"
    assert page["revisions"][-1]["change"] == "rename"
    # an append and a write under the old slug reach the same page
    store.append_page(con, "physmod-log", "§2.")
    store.write_page(con, "physmod-log", "# Log\n\n§1. §2. §3.", author="agent")
    assert store.page_id(con, "physmod-log") == doc
    assert con.execute("SELECT count(*) FROM pages").fetchone()[0] == 1
    assert store.get_page(con, "nlbank-log")["text"].endswith("§3.")
    # a chain of renames: every old slug still finds it
    store.rename_page(con, "nlbank-log", "nlbank-research-log")
    assert store.get_page(con, "physmod-log")["slug"] == "nlbank-research-log"
    assert store.page_revision_text(con, "physmod-log", 1) == "# Log\n\n§1."
    # back to an old slug of its own: that alias goes
    back = store.rename_page(con, "nlbank-research-log", "physmod-log")
    assert back["aliases"] == ["nlbank-log", "nlbank-research-log"]


def test_a_rename_is_refused_where_it_would_take_a_name(
    con: sqlite3.Connection,
) -> None:
    _projects(con)
    store.write_page(con, "a", "# A\n\nText.")
    store.write_page(con, "b", "# B\n\nText.")
    store.rename_page(con, "a", "a-two")
    with pytest.raises(ValueError, match="exists"):
        store.rename_page(con, "a-two", "b")
    with pytest.raises(ValueError, match="old name"):
        store.rename_page(con, "b", "a")  # a's old slug is a's for ever
    with pytest.raises(ValueError, match="project"):
        store.rename_page(con, "project-old", "project-older")
    with pytest.raises(ValueError):
        store.rename_page(con, "b", "  ")
    with pytest.raises(KeyError):
        store.rename_page(con, "missing", "c")
    assert not store.rename_page(con, "b", "b")["changed"]
    # the database itself refuses a page under an old name
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO pages (doc_id, slug, kind) VALUES (?, 'a', 'topic')",
            (store.ingest_text(con, "plain " * 20, title="Plain")["doc_id"],),
        )
    con.rollback()


def test_a_long_page_read_in_windows_adds_up_to_its_hash(
    con: sqlite3.Connection,
) -> None:
    rows = (f"## §{i}\n\nRow {i}, ünïcode.\n\n" for i in range(300))
    text = "# Log\n\n" + "".join(rows)
    written = store.write_page(con, "log", text, author="agent")
    meta = store.get_page(con, "log", text=False)
    assert "text" not in meta and "blocks" not in meta
    assert meta["text_len"] == written["text_len"] and meta["revisions"]
    got, at = "", 0
    while at is not None:
        part = store.get_page(con, "log", offset=at, limit=1000)
        got += part["text"]
        at = part["next_offset"]
    assert hashlib.sha256(got.encode("utf-8")).hexdigest() == meta["text_hash"]
    assert meta["text_hash"] == written["text_hash"]


def test_a_write_against_a_hash_it_did_not_read_is_refused(
    con: sqlite3.Connection,
) -> None:
    first = store.write_page(con, "log", "# Log\n\nOne.", author="agent")
    store.append_page(con, "log", "Two.")  # someone else's revision between
    with pytest.raises(PermissionError, match="changed since"):
        store.write_page(
            con,
            "log",
            "# Log\n\nOne, rewritten.",
            author="agent",
            expected_text_hash=first["text_hash"],
        )
    now = store.get_page(con, "log", text=False)["text_hash"]
    done = store.write_page(
        con, "log", "# Log\n\nOne. Two.", author="agent", expected_text_hash=now
    )
    assert done["revision"] == 3


def test_a_page_past_the_size_says_so(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pages.writes, "PAGE_LARGE", 50)
    store.write_page(con, "log", "# Log\n\nShort.", author="agent")
    assert "large" not in store.append_page(con, "log", "A bit.")
    grown = store.append_page(con, "log", "A section long enough to pass the line.")
    assert "split" in grown["large"] or "child page" in grown["large"]
    assert "large" in store.update_section(con, "log", "Later", "Under a heading.")


def test_the_door_and_the_tool_shapes(client: TestClient) -> None:
    con = client.app.state.con
    _projects(con)
    store.write_page(con, "log", "# Log\n\nText.", part_of="project-old")
    got = client.post("/page/log/move", json={"part_of": "project-new"}).json()
    assert got["changed"] and got["part_of"]["to"] == ["project-new"]
    assert client.post("/page/log/move", json={"part_of": "nope"}).status_code == 404
    assert client.post("/page/log/move", json={"parent": "log"}).status_code == 409
    renamed = client.post("/page/log/rename", json={"slug": "nlbank-log"}).json()
    assert renamed["slug"] == "nlbank-log"
    taken = client.post("/page/nlbank-log/rename", json={"slug": "project-new"})
    assert taken.status_code == 409
    meta = client.get("/page/log", params={"text": "false"}).json()
    assert meta["slug"] == "nlbank-log" and "text" not in meta
    window = client.get("/page/log", params={"offset": 2, "limit": 3}).json()
    assert window["text"] == "Log" and window["next_offset"] == 5
    stale = client.put(
        "/page/log",
        json={"text": "new", "author": "agent", "expected_text_hash": "0" * 64},
    )
    assert stale.status_code == 409


def _as_guest() -> object:
    return store.VIEWER.set(store.Viewer("guest", role="contributor"))


def test_a_move_names_no_page_its_mover_may_not_see(con: sqlite3.Connection) -> None:
    diary = store.write_page(con, "secret-diary", "# Diary\n\nMine.")
    store.write_page(con, "open-note", "# Note\n\nOpen.", parent="secret-diary")
    store.set_sensitivity(con, int(diary["doc_id"]), "personal")
    token = _as_guest()
    try:
        plan = store.move_page(con, "open-note", parent="", dry_run=True)
    finally:
        store.VIEWER.reset(token)  # type: ignore[arg-type]
    assert plan["parent"]["from"] != "secret-diary" and plan["changed"]
    # the note is read by everyone who sees the page: a personal page is not
    # named in it, whoever moved it
    store.move_page(con, "open-note", parent="")
    note = store.get_page(con, "open-note", text=False)["revisions"][-1]["note"]
    assert "secret-diary" not in note


def test_a_move_leaves_a_membership_its_mover_may_not_see(
    con: sqlite3.Connection,
) -> None:
    old, _ = _projects(con)
    note = store.write_page(con, "note", "# Note\n\nText.", part_of="project-old")
    store.set_sensitivity(con, old, "personal")
    token = _as_guest()
    try:
        moved = store.move_page(con, "note", part_of="project-new")
    finally:
        store.VIEWER.reset(token)  # type: ignore[arg-type]
    assert moved["part_of"]["from"] == [] and moved["ended_edges"] == []
    assert (
        "project-old"
        not in store.get_page(con, "note", text=False)["revisions"][-1]["note"]
    )
    assert _part_of(con, int(note["doc_id"])) == [
        ("Project old", True),
        ("Project new", True),
    ]


def test_a_projects_members_from_a_hidden_document_are_not_listed(
    con: sqlite3.Connection,
) -> None:
    _projects(con)
    diary = store.write_page(con, "secret-diary", "# Diary\n\nMine.")
    store.link(
        con,
        store.Edge("Aunt plan", "page", "part_of", "Project new", "project"),
        confidence="EXTRACTED",
        source_doc=int(diary["doc_id"]),
        evidence="the diary says so",
        producer="agent",
        run="by-hand",
    )
    store.set_sensitivity(con, int(diary["doc_id"]), "personal")
    seen = store.get_page(con, "project-new", text=False)["members"]["items"]
    assert "Aunt plan" in [m["title"] for m in seen]
    token = _as_guest()
    try:
        seen = store.get_page(con, "project-new", text=False)["members"]["items"]
    finally:
        store.VIEWER.reset(token)  # type: ignore[arg-type]
    assert "Aunt plan" not in [m["title"] for m in seen]


def test_an_old_slug_of_a_hidden_page_gives_away_nothing(
    con: sqlite3.Connection,
) -> None:
    page = store.write_page(con, "plain-name", "# Plain\n\nText.")
    store.rename_page(con, "plain-name", "aunt-diagnosis")
    store.set_sensitivity(con, int(page["doc_id"]), "personal")
    mine = store.write_page(con, "mine", "# Mine\n\nText.")
    token = _as_guest()
    try:
        with pytest.raises(KeyError) as absent:
            store.write_page(con, "plain-name", "# Over\n\nIt.")
        assert "aunt-diagnosis" not in str(absent.value)
        refusals = []
        for taken in ("aunt-diagnosis", "plain-name"):
            with pytest.raises(ValueError) as refused:
                store.rename_page(con, "mine", taken)
            refusals.append(str(refused.value).replace(taken, "…"))
    finally:
        store.VIEWER.reset(token)  # type: ignore[arg-type]
    # a hidden page's slug and its old one are refused alike, saying no more
    assert refusals[0] == refusals[1] and "exists" not in refusals[0]
    assert int(mine["doc_id"]) != int(page["doc_id"])


def test_the_cores_write_nothing_until_their_caller_commits(
    con: sqlite3.Connection,
) -> None:
    """A page split composes the cores: a failure after the first child
    takes the child back with it (the review of 2026-11-03, quality 3)."""
    store.write_page(con, "index", "# Index\n\nThe whole.")
    pages._write_page(con, "child-a", "# A\n\nThe first.", parent="index")
    pages._move_page(con, "child-a", parent="")
    pages._rename_slug(con, "child-a", "child-one")
    with pytest.raises(KeyError):
        pages._move_page(con, "child-one", parent="nowhere")
    con.rollback()
    assert store.page_id(con, "child-a") is None
    assert store.page_id(con, "child-one") is None
    assert store.page_id(con, "index") is not None
