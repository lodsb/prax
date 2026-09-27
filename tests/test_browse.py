"""Browsing by module: a module holds the documents of the modules built
on it, and the documents no module was set for can be listed alone
(niggles.txt: "browse doesn't show all available documents … also the
unassigned label only")."""

from __future__ import annotations

import sqlite3

from prax import ontology, store


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
    assert _listed(con, "craft") == {recipe, build, joint, loose}
    assert _listed(con, "kitchen") == {recipe, loose}
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
