"""Pages: living Markdown documents with revisions, the agent rule, edges
to the documents they annotate, projects, and the context column."""

from __future__ import annotations

import sqlite3

import pytest

from prax import extraction, store


def test_write_read_and_revise(con: sqlite3.Connection) -> None:
    r = store.write_page(
        con, "Reverb Notes!", "# Reverb\n\nFirst thoughts.", kind="topic"
    )
    assert r["created"] and r["revision"] == 1 and r["slug"] == "reverb-notes"
    page = store.get_page(con, "reverb-notes")
    assert page["text"].startswith("# Reverb") and page["author"] == "human"
    assert page["kind"] == "topic" and page["title"] == "Reverb notes"
    doc = store.get_document(con, page["doc_id"], max_chars=100)
    assert doc["mime"] == "text/markdown" and doc["meta"]["source"] == "wiki"
    assert doc["meta"]["page"] == {
        "slug": "reverb-notes",
        "kind": "topic",
        "revision": 1,
        "author": "human",
    }
    # the page is a document: searchable, with a field
    assert store.search(con, "thoughts", mode="fts")[0]["doc_id"] == page["doc_id"]
    assert "wiki page" in store.document_field(con, page["doc_id"])
    r2 = store.write_page(
        con, "reverb-notes", "# Reverb\n\nSecond thoughts.", note="edit"
    )
    assert not r2["created"] and r2["revision"] == 2
    page = store.get_page(con, "reverb-notes")
    assert "Second" in page["text"] and [x["revision"] for x in page["revisions"]] == [
        1,
        2,
    ]
    assert store.page_revision_text(con, "reverb-notes", 1).endswith("First thoughts.")
    with pytest.raises(KeyError):
        store.page_revision_text(con, "reverb-notes", 9)
    assert store.get_page(con, "nope") is None
    with pytest.raises(ValueError):
        store.write_page(con, "x", "t", kind="diary")
    listed = store.list_pages(con)
    assert [(p["slug"], p["revision"], p["author"]) for p in listed] == [
        ("reverb-notes", 2, "human")
    ]


def test_agent_appends_but_does_not_overwrite(con: sqlite3.Connection) -> None:
    store.write_page(con, "fdn", "# FDN\n\nMy own words.", author="human")
    with pytest.raises(PermissionError):
        store.write_page(con, "fdn", "replaced", author="agent")
    r = store.append_page(
        con, "fdn", "What the library says.", heading="From the library", author="agent"
    )
    page = store.get_page(con, "fdn")
    assert r["revision"] == 2 and page["author"] == "agent"
    assert (
        page["text"]
        == "# FDN\n\nMy own words.\n\n## From the library\n\nWhat the library says.\n"
    )
    # a person may overwrite an agent revision; an agent may force
    store.write_page(con, "fdn", "# FDN\n\nRewritten by me.", author="human")
    assert (
        store.write_page(con, "fdn", "agent again", author="agent", force=True)[
            "revision"
        ]
        == 4
    )
    with pytest.raises(KeyError):
        store.append_page(con, "missing", "x")


def test_addendum_and_project_edges_reach_the_context(con: sqlite3.Connection) -> None:
    paper = store.ingest_text(con, "grains everywhere", title="Grain Paper")["doc_id"]
    other = store.ingest_text(con, "delays", title="Delay Paper")["doc_id"]
    proj = store.write_page(
        con, "Granular Study", "# Granular study\n\nOpen questions.", kind="project"
    )
    note = store.write_page(
        con,
        "note-grain-paper",
        "Interesting figure 3.",
        kind="addendum",
        annotates=[paper],
        part_of="granular-study",
    )
    assert store.add_to_project(con, "granular-study", paper) is not None
    assert store.add_to_project(con, "granular-study", paper) is None  # idempotent
    edges = store.traverse(con, "Grain Paper", hops=1)
    rels = {(e["src"], e["rel"], e["dst"]) for e in edges}
    assert ("Note grain paper", "annotates", "Grain Paper") in rels
    assert ("Grain Paper", "part_of", "Granular study") in rels
    assert ("Note grain paper", "part_of", "Granular study") in rels
    ctx = store.document_context(con, paper)
    assert [n["slug"] for n in ctx["notes"]] == ["note-grain-paper"] and ctx[
        "page"
    ] is None
    pctx = store.document_context(con, proj["doc_id"])
    assert pctx["page"] == {"slug": "granular-study", "kind": "project"}
    assert {(m["title"], m["type"]) for m in pctx["members"]} == {
        ("Grain Paper", "paper"),
        ("Note grain paper", "page"),
    }
    assert [m["doc_id"] for m in pctx["members"] if m["title"] == "Grain Paper"] == [
        paper
    ]
    with pytest.raises(KeyError):
        store.write_page(con, "n2", "x", annotates=[999])
    with pytest.raises(KeyError):
        store.write_page(con, "n3", "x", part_of="no-such-project")
    with pytest.raises(KeyError):
        store.add_to_project(con, "granular-study", 999)
    assert (
        store.search(con, "figure", mode="fts", doctype="page")[0]["doc_id"]
        == note["doc_id"]
    )
    assert store.search(con, "delays", mode="fts", doctype="page") == []
    assert other not in {m["doc_id"] for m in pctx["members"]}


def test_extraction_input_names_the_page_kind(con: sqlite3.Connection) -> None:
    proj = store.write_page(con, "thread", "# Thread\n\nNotes.", kind="project")
    inp = extraction.build_input(con, proj["doc_id"])
    assert "Kind: project" in inp.header
    note = store.write_page(con, "n", "Hello.", kind="addendum")
    assert "Kind: page" in extraction.build_input(con, note["doc_id"]).header
    paper = store.ingest_text(con, "t", title="P")["doc_id"]
    assert "Kind:" not in extraction.build_input(con, paper).header
    assert "page or project entity" in extraction.system_prompt(
        __import__("prax.ontology", fromlist=["current"]).current()
    )
