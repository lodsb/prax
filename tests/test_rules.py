"""What follows from what is stated (stage AN): the ontology's relation
characteristics, its lint, and the rule pass with its premises."""

from __future__ import annotations

import itertools
import sqlite3

import pytest

from prax import store
from prax.graph import ontology

E = store.Edge


def _part_of(con: sqlite3.Connection, a: str, b: str, doc: int | None = None) -> int:
    return store.link(
        con,
        E(a, "organization", "part_of", b, "organization"),
        source_doc=doc,
        producer="test",
    )


def _live(con: sqlite3.Connection, rel: str) -> set[tuple[str, str, str]]:
    rows = con.execute(
        "SELECT s.name, t.name, e.producer FROM edges e JOIN entities s ON s.id = e.src"
        " JOIN entities t ON t.id = e.dst WHERE e.rel = ? AND e.valid_to IS NULL",
        (rel,),
    )
    return {(r[0], r[1], r[2]) for r in rows}


def test_the_characteristics_load_and_change_no_version() -> None:
    onto = ontology.current()
    assert onto.relations["part_of"].transitive
    assert onto.relations["compatible_with"].symmetric
    assert onto.relations["published_in"].functional
    assert onto.relations["authored_by"].same_as == ("schema:author",)
    assert onto.types["person"].same_as == ("schema:Person",)
    assert onto.version.startswith("core4+")


def test_the_lint_refuses_what_contradicts_itself() -> None:
    bad = ontology.parse_module(
        "module: core\nversion: 1\nentity_types: [thing]\n"
        "relation_types:\n"
        "  holds: {domain: [thing], range: [thing],"
        " transitive: true, functional: true}\n"
        "  mirrors: {domain: [thing], range: [thing], inverse_of: nothing}\n"
    )
    with pytest.raises(ValueError, match="transitive and functional") as got:
        ontology.compose([bad])
    assert "inverse_of 'nothing' is unknown" in str(got.value)


def test_a_chain_derives_its_closure_with_its_premises(con: sqlite3.Connection) -> None:
    _part_of(con, "Lab", "Department")
    _part_of(con, "Department", "Faculty")
    _part_of(con, "Faculty", "University")
    _part_of(con, "Lab", "Faculty")  # stated: never derived again
    report = store.derive_rules(con)
    assert report["rule:part_of"]["added"] == 2  # Lab and Department -> University
    live = _live(con, "part_of")
    assert ("Lab", "University", "rule:transitive") in live
    assert ("Department", "University", "rule:transitive") in live
    assert ("Lab", "Faculty", "test") in live
    assert not any(
        p == "rule:transitive" and (a, b) == ("Lab", "Faculty") for a, b, p in live
    )
    row = con.execute(
        "SELECT e.id, e.confidence, e.evidence FROM edges e"
        " JOIN entities s ON s.id = e.src"
        " JOIN entities t ON t.id = e.dst WHERE s.name = 'Department'"
        " AND t.name = 'University' AND e.producer = 'rule:transitive'"
    ).fetchone()
    assert row[1] == "INFERRED" and "Department part_of Faculty" in row[2]
    why = store.edge_premises(con, int(row[0]))
    assert [(w["src"], w["dst"]) for w in why] == [
        ("Department", "Faculty"),
        ("Faculty", "University"),
    ]
    # again: nothing new, everything kept
    again = store.derive_rules(con)["rule:part_of"]
    assert (again["added"], again["kept"], again["ended"]) == (0, 2, 0)


def test_a_derivation_ends_with_its_premise(con: sqlite3.Connection) -> None:
    _part_of(con, "Lab", "Department")
    middle = _part_of(con, "Department", "University")
    store.derive_rules(con)
    assert ("Lab", "University", "rule:transitive") in _live(con, "part_of")
    store.invalidate_edge(con, middle)
    out = store.derive_rules(con)["rule:part_of"]
    assert out["ended"] == 1
    assert ("Lab", "University", "rule:transitive") not in _live(con, "part_of")


def test_a_symmetric_relation_derives_its_converse(con: sqlite3.Connection) -> None:
    store.link(
        con,
        E("Synth A", "device", "compatible_with", "Pedal B", "device"),
        producer="t",
    )
    store.derive_rules(con)
    assert ("Pedal B", "Synth A", "rule:symmetric") in _live(con, "compatible_with")


def test_a_personal_documents_facts_derive_nothing(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "a private note on the lab " * 10)["doc_id"]
    store.set_sensitivity(con, doc, "personal")
    _part_of(con, "Lab", "Department", doc)
    _part_of(con, "Department", "University")
    report = store.derive_rules(con)
    assert report["rule:part_of"]["added"] == 0
    assert report["rule:part_of"]["premises"] == 1


def test_the_cap_holds(con: sqlite3.Connection) -> None:
    names = [f"Unit {i}" for i in range(8)]
    for a, b in itertools.pairwise(names):
        _part_of(con, a, b)
    out = store.derive_rules(con, cap=5)["rule:part_of"]
    assert out["added"] == 5 and out["capped"] == 1


def test_a_functional_relation_with_two_values_is_a_finding(
    con: sqlite3.Connection,
) -> None:
    """A constraint's breach is listed for a person, never an edge."""
    from prax.store import repair

    for venue in ("DAFx", "ICASSP"):
        store.link(
            con, E("A paper", "paper", "published_in", venue, "venue"), producer="t"
        )
    store.link(
        con, E("B paper", "paper", "published_in", "DAFx", "venue"), producer="t"
    )
    found = repair._functional_conflicts(con)
    assert [(f["relation"], f["subject"]) for f in found] == [
        ("published_in", "A paper")
    ]
    assert sorted(v["value"] for v in found[0]["values"]) == ["DAFx", "ICASSP"]
    assert any(
        a.name == "functional-conflicts" and not a.repairable for a in repair.AILMENTS
    )
