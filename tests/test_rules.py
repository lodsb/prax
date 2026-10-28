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
    assert onto.version.startswith("core8+")


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
    # both are premises; the personal one stands alone (one document, one
    # edge), and with the open one would make a fact of two documents
    assert report["rule:part_of"]["added"] == 0
    assert report["rule:part_of"]["premises"] == 2


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


# ------------------------------------------------- which end is the part


@pytest.mark.parametrize(
    ("src", "src_type", "dst", "dst_type", "verdict"),
    [
        # the higher organization is not part of the lower
        (
            "Technische Universität München",
            "organization",
            "Chair for Informatics IX",
            "organization",
            "reversed",
        ),
        (
            "Lab of Acoustics",
            "organization",
            "Helsinki University",
            "organization",
            None,
        ),
        # the destination is the source and a number more, or its exam
        ("Dalil al Angham", "paper", "Dalil al Angham - 3.pdf", "paper", "reversed"),
        (
            "Grundlagen Betriebssysteme",
            "paper",
            "Klausur zur Vorlesung Grundlagen Betriebssysteme",
            "paper",
            "reversed",
        ),
        # but "Chapter 32- X" is the chapter X itself
        (
            "The Laplace Transform",
            "paper",
            "Chapter 32- The Laplace Transform",
            "page",
            None,
        ),
        # neither way round
        (
            "Technische Universität München",
            "organization",
            "Erasmus",
            "project",
            "misfit",
        ),
        (
            "Handmade electronic music",
            "document",
            "Taylor & Francis Group",
            "organization",
            "misfit",
        ),
        ("Aufgabe 1 – Round Robin", "paper", "course", "project", "misfit"),
        (
            "Aufgabe 1 – Round Robin",
            "paper",
            "the research project",
            "project",
            "misfit",
        ),
        # a cue alone is a doubt
        ("Diskrete Strukturen II", "paper", "Übungsblatt 07", "paper", "doubtful"),
        ("C++ International Standard", "paper", "N3337", "paper", "doubtful"),
        # an article is in a journal's volume; a section shares words with its chapter
        (
            "Gordon Mumma: Mograph 1962",
            "paper",
            "Leonardo Music Journal Vol 21",
            "paper",
            None,
        ),
        ("Adaptive Quadrature", "paper", "Chapter 6. Quadrature", "paper", None),
        # "mit" is German for "with", not MIT
        (
            "Synchronisation mit Semaphoren",
            "paper",
            "Grundlagen Betriebssysteme",
            "paper",
            None,
        ),
        ("Übungsblatt 07", "paper", "Diskrete Strukturen II", "paper", None),
    ],
)
def test_the_names_say_which_end_is_the_part(
    src: str, src_type: str, dst: str, dst_type: str, verdict: str | None
) -> None:
    said = ontology.part_of_suspect(ontology.current(), src, src_type, dst, dst_type)
    assert (said[0] if said else None) == verdict


def test_a_backwards_part_of_goes_to_the_review_queue(con: sqlite3.Connection) -> None:
    from prax.graph import extraction

    doc = store.ingest_text(con, "exercise sheet seven of the course " * 10)["doc_id"]
    ex = extraction.Extraction(
        summary="s",
        triples=[
            extraction.Triple(
                "Diskrete Strukturen II",
                "paper",
                "part_of",
                "Übungsblatt 07",
                "paper",
                "EXTRACTED",
                "e",
            ),
            extraction.Triple(
                "Übungsblatt 07",
                "paper",
                "part_of",
                "Diskrete Strukturen II",
                "paper",
                "EXTRACTED",
                "e",
            ),
        ],
    )
    rep = extraction.apply(con, doc, ex, extractor="stub")
    assert (rep.linked, rep.queued) == (1, 1)
    queued = store.list_review(con)
    assert any(q["reason"].startswith("part_of doubtful") for q in queued)


def test_the_rule_pass_stands_on_no_suspect(con: sqlite3.Connection) -> None:
    """One backwards fact would make every sheet part of the exam."""
    for a, b in (
        ("Blatt 1", "Grundlagen Betriebssysteme"),
        (
            "Grundlagen Betriebssysteme",
            "Klausur zur Vorlesung Grundlagen Betriebssysteme",
        ),
    ):
        store.link(con, E(a, "paper", "part_of", b, "paper"), producer="t")
    out = store.derive_rules(con)["rule:part_of"]
    assert (out["premises"], out["added"]) == (1, 0)


def test_heal_turns_a_reversed_part_of_and_ends_a_misfit(
    con: sqlite3.Connection,
) -> None:
    from prax.store import repair

    _part_of(con, "Technische Universität München", "Fakultät für Informatik")
    store.link(con, E("Aufgabe 4", "paper", "part_of", "course", "paper"), producer="t")
    store.link(
        con,
        E("Normen von Funktionen", "paper", "part_of", "SS 2009 Blatt 2", "paper"),
        producer="t",
    )
    found = {f["verdict"] for f in repair._backwards_part_of(con)}
    assert found == {"reversed", "misfit", "doubtful"}
    out = repair.heal(con, only=["backwards-part-of"])
    assert out["backwards-part-of"] == {"found": 3, "repaired": 2, "left alone": 1}
    live = _live(con, "part_of")
    assert (
        "Fakultät für Informatik",
        "Technische Universität München",
        repair.HEAL_PRODUCER,
    ) in live
    assert not any(b == "course" for _, b, _ in live)
    assert ("Normen von Funktionen", "SS 2009 Blatt 2", "t") in live


# ------------------------------------------------- a chain (AT step 6)


def _picture(con: sqlite3.Connection, title: str, *, personal: bool = True) -> int:
    doc = int(store.ingest_text(con, f"{title} " * 40, title=title)["doc_id"])
    if personal:
        store.set_sensitivity(con, doc, "suspected")
    return doc


def _says(con: sqlite3.Connection, doc: int, rel: str, dst: str, dtype: str) -> int:
    node = store.document_node(con, doc)
    return store.link(
        con,
        E(node[0], node[1], rel, dst, dtype),
        source_doc=doc,
        producer="human",
        run="t",
    )


def _attended(con: sqlite3.Connection) -> dict[tuple[str, str], int | None]:
    rows = con.execute(
        "SELECT s.name, t.name, e.source_doc FROM edges e"
        " JOIN entities s ON s.id = e.src JOIN entities t ON t.id = e.dst"
        " WHERE e.rel = 'attended' AND e.valid_to IS NULL"
    )
    return {(r[0], r[1]): r[2] for r in rows}


def test_who_a_picture_of_an_event_depicts_attended_it(
    con: sqlite3.Connection,
) -> None:
    """``attended`` follows from ``~depicts`` then ``recorded_at``
    (core.yaml's chain): sourced to the picture that shows it, so hidden
    wherever the picture is, and ended when the face's name is."""
    assert ontology.current().relations["attended"].chain == ("~depicts", "recorded_at")
    wedding = _picture(con, "IMG one")
    ada = _says(con, wedding, "depicts", "Ada", "person")
    _says(con, wedding, "recorded_at", "The wedding", "event")
    # a second picture of it showing her again: still one fact
    again = _picture(con, "IMG two")
    _says(con, again, "depicts", "Ada", "person")
    _says(con, again, "recorded_at", "The wedding", "event")
    # her in one picture, an event of another's: no one document says so
    lone = _picture(con, "IMG three")
    _says(con, lone, "depicts", "Grace", "person")
    other = _picture(con, "IMG four")
    _says(con, other, "recorded_at", "The wedding", "event")
    report = store.derive_rules(con)["rule:attended"]
    assert report["added"] == 1
    got = _attended(con)
    assert list(got) == [("Ada", "The wedding")]
    assert got[("Ada", "The wedding")] in (wedding, again)
    (edge,) = con.execute(
        "SELECT id, producer, confidence FROM edges WHERE rel = 'attended'"
    ).fetchall()
    assert edge[1] == "rule:chain" and edge[2] == "INFERRED"
    assert len(store.edge_premises(con, int(edge[0]))) == 2
    # a viewer who may not see the picture does not see the fact
    token = store.VIEWER.set(store.Viewer(name="t", domains=None, personal=False))
    try:
        assert store.edge_hidden(con, int(edge[0]))
    finally:
        store.VIEWER.reset(token)
    # a pass again keeps it; the name taken back from both pictures ends it
    assert store.derive_rules(con)["rule:attended"]["kept"] == 1
    store.end_edge(con, ada, run="t")
    for eid in con.execute(
        "SELECT id FROM edges WHERE rel = 'depicts' AND source_doc = ?"
        " AND valid_to IS NULL",
        (again,),
    ).fetchall():
        store.end_edge(con, int(eid[0]), run="t")
    assert store.derive_rules(con)["rule:attended"]["ended"] == 1
    assert _attended(con) == {}


def test_a_chain_across_two_open_documents(con: sqlite3.Connection) -> None:
    """Two open documents may make a chain between them: nothing about
    them is hidden, and the fact is sourced to neither."""
    one = _picture(con, "open one", personal=False)
    two = _picture(con, "open two", personal=False)
    node = store.document_node(con, one)
    store.link(con, E(node[0], node[1], "depicts", "Ada", "person"), source_doc=two)
    store.link(
        con, E(node[0], node[1], "recorded_at", "A trip", "event"), source_doc=one
    )
    store.derive_rules(con)
    assert _attended(con) == {("Ada", "A trip"): None}


def test_no_chain_across_a_hidden_and_an_open_document(
    con: sqlite3.Connection,
) -> None:
    """One premise from a suspected picture and one from an open document
    make nothing: no one document would hide the fact, and with no source
    every token would read what the picture says."""
    shown = _picture(con, "the picture", personal=True)
    told = _picture(con, "a note", personal=False)
    node = store.document_node(con, shown)
    store.link(con, E(node[0], node[1], "depicts", "Ada", "person"), source_doc=shown)
    store.link(
        con, E(node[0], node[1], "recorded_at", "A trip", "event"), source_doc=told
    )
    store.derive_rules(con)
    assert _attended(con) == {}


def test_a_chain_names_two_known_relations() -> None:
    bad = ontology.parse_module(
        "module: m\nversion: 1\nrelation_types:\n"
        "  went: {domain: [thing], range: [thing], chain: [~nothing, went]}\n"
    )
    problems = ontology.lint({}, bad.relations)
    assert any("'nothing', unknown" in p for p in problems)
    assert any("a chain through itself" in p for p in problems)
