"""Browsing by module: a module holds the documents of the modules built
on it, and the documents no module was set for can be listed alone
(niggles.txt: "browse doesn't show all available documents … also the
unassigned label only")."""

from __future__ import annotations

import sqlite3

from prax import store
from prax.graph import ontology


def _doc(con: sqlite3.Connection, title: str, domains: list[str] | None) -> int:
    doc = int(
        store.ingest_text(con, f"{title}. " + "Words about it. " * 20, title=title)[
            "doc_id"
        ]
    )
    if domains is not None:
        store.set_domains(con, doc, domains)
    return doc


def _listed(con: sqlite3.Connection, domain: str) -> set[int]:
    return {d["id"] for d in store.list_documents(con, domain=domain)["items"]}


def test_a_module_holds_the_documents_of_the_modules_built_on_it(
    con: sqlite3.Connection,
) -> None:
    recipe = _doc(con, "Apple cake", ["kitchen"])
    build = _doc(con, "A pedal build", ["workshop"])
    joint = _doc(con, "Joints in wood", ["craft"])
    paper = _doc(con, "A paper", ["research"])
    loose = _doc(con, "Nobody said", None)
    assert ontology.current().within("craft") >= {"craft", "kitchen", "workshop"}
    assert _listed(con, "craft") == {recipe, build, joint}
    assert _listed(con, "kitchen") == {recipe}
    assert loose not in _listed(con, "research")
    assert paper not in _listed(con, "craft")


def test_the_unassigned_documents_alone(con: sqlite3.Connection) -> None:
    _doc(con, "Apple cake", ["kitchen"])
    loose = _doc(con, "Nobody said", None)
    assert _listed(con, "unassigned") == {loose}


def test_a_search_by_module_keeps_the_modules_built_on_it(
    con: sqlite3.Connection,
) -> None:
    recipe = _doc(con, "Apple cake with cinnamon", ["kitchen"])
    paper = _doc(con, "Apple cake statistics", ["research"])
    hits = {
        h["doc_id"] for h in store.search(con, "apple cake", mode="fts", domain="craft")
    }
    assert recipe in hits and paper not in hits


def test_a_module_alone_leaves_the_modules_built_on_it_out(
    con: sqlite3.Connection,
) -> None:
    # "craft is mostly cooking": the plain filter holds kitchen's recipes,
    # ``only:craft`` the module's own documents
    recipe = _doc(con, "Apple cake with cinnamon", ["kitchen"])
    joint = _doc(con, "Joints in wood and cinnamon", ["craft"])
    loose = _doc(con, "Nobody said", None)
    assert ontology.current().within("only:craft") == {"craft"}
    assert _listed(con, "only:craft") == {joint}
    assert loose not in _listed(con, "craft")
    hits = {
        h["doc_id"]
        for h in store.search(con, "cinnamon", mode="fts", domain="only:craft")
    }
    assert joint in hits and recipe not in hits


def test_the_domain_menu_counts_each_module_alone_and_with_those_built_on_it(
    con: sqlite3.Connection,
) -> None:
    _doc(con, "Apple cake", ["kitchen"])
    _doc(con, "Pear cake", ["kitchen"])
    _doc(con, "A pedal build", ["workshop"])
    _doc(con, "Joints in wood", ["craft"])
    _doc(con, "A paper", ["research", "kitchen"])
    _doc(con, "Nobody said", None)
    store.retire_document(con, _doc(con, "Gone", ["craft"]), reason="t")
    got = store.domain_counts(con)
    craft, kitchen = got["modules"]["craft"], got["modules"]["kitchen"]
    assert craft == {"own": 1, "within": 5}
    assert kitchen == {"own": 3, "within": 3}
    assert got["modules"]["research"]["own"] == 1
    assert got["unassigned"] == 1
