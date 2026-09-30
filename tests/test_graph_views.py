"""The graph by module and a document's own graph (niggles.txt: "graph
view per module", "per document maybe a link into its subgraph")."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from prax import store


def _doc(con: sqlite3.Connection, title: str, domains: list[str] | None) -> int:
    doc = int(store.ingest_text(con, f"{title}. " * 20, title=title)["doc_id"])
    if domains is not None:
        store.set_domains(con, doc, domains)
    return doc


def _edge(con: sqlite3.Connection, e: store.Edge, doc: int) -> None:
    store.link(con, e, confidence="EXTRACTED", source_doc=doc, producer="t", run="t")


def _library(con: sqlite3.Connection) -> dict[str, int]:
    cake = _doc(con, "Apple cake", ["kitchen"])
    pie = _doc(con, "Apple pie", ["kitchen"])
    paper = _doc(con, "A paper", ["research"])
    loose = _doc(con, "Nobody said", None)
    for doc, dish in ((cake, "Apple cake"), (pie, "Apple pie")):
        _edge(con, store.Edge(dish, "dish", "calls_for", "apple", "ingredient"), doc)
        _edge(con, store.Edge(dish, "dish", "calls_for", "butter", "ingredient"), doc)
    for doc in (paper, loose):
        _edge(con, store.Edge("A paper", "paper", "uses", "entropy", "concept"), doc)
        _edge(con, store.Edge("A paper", "paper", "uses", "HMM", "method"), doc)
    return {"cake": cake, "paper": paper}


def test_one_modules_graph_is_drawn_from_its_documents(
    con: sqlite3.Connection,
) -> None:
    _library(con)
    kitchen = {n["name"] for n in store.hub_graph(con, domain="kitchen")["nodes"]}
    # its own kinds of thing are hubs, and the research concepts are not in
    # it, not even through the document no module was set for
    assert {"apple", "butter"} <= kitchen
    assert not {"entropy", "HMM"} & kitchen
    whole = {n["name"] for n in store.hub_graph(con)["nodes"]}
    assert {"entropy", "HMM"} <= whole and "apple" not in whole
    # a module holds the modules built on it
    craft = {n["name"] for n in store.hub_graph(con, domain="craft")["nodes"]}
    assert "apple" in craft


def test_a_documents_graph_puts_its_own_entity_first(
    con: sqlite3.Connection,
) -> None:
    docs = _library(con)
    other = _doc(con, "Notes", ["research"])
    _edge(con, store.Edge("HMM", "method", "uses", "entropy", "concept"), docs["paper"])
    _edge(con, store.Edge("Notes", "paper", "uses", "HMM", "method"), other)
    got = store.document_edges(con, docs["paper"])
    assert len(got) == 3 and {e["source_doc"] for e in got} == {docs["paper"]}
    assert [e["src"] for e in got[:2]] == ["A paper", "A paper"]
    assert got[-1]["src"] == "HMM"
    assert len(store.document_edges(con, docs["paper"], limit=1)) == 1


def test_the_door_draws_both(client: TestClient) -> None:
    docs = _library(client.app.state.con)
    nodes = client.get("/graph/overview", params={"domain": "kitchen"}).json()["nodes"]
    assert "apple" in {n["name"] for n in nodes}
    got = client.get(f"/graph/document/{docs['cake']}").json()
    assert got["doc_id"] == docs["cake"] and len(got["edges"]) == 2


def test_an_entitys_walk_keeps_one_modules_documents(
    con: sqlite3.Connection, client: TestClient
) -> None:
    """``traverse`` with a domain keeps what that module's documents say,
    and those of the modules built on it; the unassigned are left out."""
    docs = _library(con)
    _edge(
        con,
        store.Edge("Apple cake", "document", "covers", "entropy", "concept"),
        docs["cake"],
    )
    everywhere = store.traverse_map(con, "entropy")
    assert {e["source_doc"] for e in everywhere["edges"]} >= {
        docs["cake"],
        docs["paper"],
    }
    research = store.traverse_map(con, "entropy", domain="research")
    assert {e["source_doc"] for e in research["edges"]} == {docs["paper"]}
    craft = store.traverse_map(con, "entropy", domain="craft")  # kitchen is built on it
    assert {e["source_doc"] for e in craft["edges"]} == {docs["cake"]}
    got = client.get("/traverse", params={"entity": "entropy", "domain": "research"})
    assert {e["source_doc"] for e in got.json()["edges"]} == {docs["paper"]}
