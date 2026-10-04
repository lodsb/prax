"""The fixes of the review of 2026-10-04: each test fails on the code the
review read, and holds the behaviour the finding asked for."""

from __future__ import annotations

import sqlite3

from prax import store
from prax.graph import resolution
from prax.store import repair

E = store.Edge


def _live(con: sqlite3.Connection, edge_id: int) -> bool:
    row = con.execute("SELECT valid_to FROM edges WHERE id = ?", (edge_id,)).fetchone()
    return row is not None and row[0] is None


# ----------------------------------------------------------- data safety


def test_a_derivation_a_merge_doubled_ends_with_its_premise(
    con: sqlite3.Connection,
) -> None:
    """Finding 4: two derived edges folded onto one pair by a merge; the
    one the pass did not track used to outlive its premises."""
    org = "organization"
    lab = store.link(con, E("Lab", org, "part_of", "Dept", org), producer="t")
    store.link(con, E("Dept", org, "part_of", "Faculty", org), producer="t")
    store.link(con, E("Dept", org, "part_of", "Fac", org), producer="t")
    store.derive_rules(con)  # Lab->Faculty and Lab->Fac
    fac, faculty = (
        int(con.execute("SELECT id FROM entities WHERE name = ?", (n,)).fetchone()[0])
        for n in ("Fac", "Faculty")
    )
    store.merge_entities(con, fac, faculty)
    store.derive_rules(con)
    store.invalidate_edge(con, lab)
    store.derive_rules(con)
    derived = con.execute(
        "SELECT count(*) FROM edges WHERE producer LIKE 'rule:%' AND rel = 'part_of'"
        " AND valid_to IS NULL"
    ).fetchone()[0]
    assert derived == 0


def test_a_heal_run_is_undone_whole(con: sqlite3.Connection) -> None:
    """Finding 5: retiring a heal run ended its replacements but not what
    it had ended; restore_run states those again."""
    doc = store.ingest_text(con, "a monograph on counterpoint " * 20)["doc_id"]
    old = store.link(
        con,
        E("A monograph", "paper", "published_in", "Sommersemester 2009", "venue"),
        source_doc=doc,
        evidence="on the title page",
        producer="extractor",
        run="x1",
    )
    repair.heal(con, only=["not-venues"])
    assert not _live(con, old)
    out = store.restore_run(con, repair.NOT_VENUE_PRODUCER)
    assert out["restated"] == 1
    row = con.execute(
        "SELECT producer, run, evidence, source_doc FROM edges"
        " WHERE rel = 'published_in' AND valid_to IS NULL"
    ).fetchone()
    assert tuple(row) == ("extractor", "x1", "on the title page", doc)
    assert not _live(con, old)  # the ended edge stays ended: history
    assert store.restore_run(con, repair.NOT_VENUE_PRODUCER)["restated"] == 0


def test_undoing_a_resolve_round_ends_its_edition_links(
    con: sqlite3.Connection,
) -> None:
    """Finding 16: unmerge_run took the merges back and left the venue
    tier's part_of edges of the same run."""
    for paper, venue in (("P1", "NIME"), ("P2", "NIME 2010"), ("P3", "NIME2010")):
        store.link(con, E(paper, "paper", "published_in", venue, "venue"), producer="t")
    resolution.apply(con, resolution.plan(con, etype="venue", likely=False), run="r1")
    links = "SELECT count(*) FROM edges WHERE run = 'r1' AND valid_to IS NULL"
    assert con.execute(links).fetchone()[0] == 1
    store.unmerge_run(con, "r1")
    assert con.execute(links).fetchone()[0] == 0


# --------------------------------------------------------------- the wall


def _as_restricted(domains: frozenset[str] | None = None) -> object:
    return store.VIEWER.set(store.Viewer(name="t", domains=domains, personal=False))


def _private_doc(con: sqlite3.Connection, text: str = "my own notes " * 20) -> int:
    doc = int(store.ingest_text(con, text)["doc_id"])
    store.set_sensitivity(con, doc, "personal")
    return doc


def test_a_derivation_from_a_hidden_premise_is_hidden(con: sqlite3.Connection) -> None:
    """Finding 2: a rule edge quoted a premise only a hidden document
    states; it is hidden with that premise (here: a premise from a
    document the viewer's modules do not reach)."""
    org = "organization"
    doc = int(store.ingest_text(con, "lab notes of the department " * 20)["doc_id"])
    store.set_domains(con, doc, ["research"])
    store.link(con, E("Lab", org, "part_of", "Dept", org), source_doc=doc, producer="t")
    store.link(con, E("Dept", org, "part_of", "Uni", org), producer="t")
    store.derive_rules(con)
    derived = int(
        con.execute("SELECT id FROM edges WHERE producer LIKE 'rule:%'").fetchone()[0]
    )
    token = _as_restricted(frozenset({"kitchen", "unassigned"}))
    try:
        walk = store.traverse_map(con, "Uni")
        assert all(e["edge_id"] != derived for e in walk["edges"])
        assert store.edge_hidden(con, derived)
        got = store.changes(con, "2000", derived=True)
        assert all(f["edge_id"] != derived for f in got["added"]["facts"])
    finally:
        store.VIEWER.reset(token)  # type: ignore[arg-type]
    assert not store.edge_hidden(con, derived)


def test_a_name_only_hidden_documents_know_is_unknown(con: sqlite3.Connection) -> None:
    """Finding 8: changes told a hidden-only name from an unknown one."""
    doc = _private_doc(con)
    store.link(
        con,
        E("Bank statement", "document", "mentions", "Landlord X", "person"),
        source_doc=doc,
    )
    token = _as_restricted()
    try:
        assert (
            store.changes(con, "2000", entity="Landlord X")["unknown_entity"]
            == "Landlord X"
        )
    finally:
        store.VIEWER.reset(token)  # type: ignore[arg-type]
    assert "unknown_entity" not in store.changes(con, "2000", entity="Landlord X")


def test_a_hidden_fact_makes_no_rereading(con: sqlite3.Connection) -> None:
    """Finding 18: a hidden edge held before the period hid the visible
    one written in it, as a re-reading."""
    doc = _private_doc(con)
    old = store.link(
        con, E("Paper A", "paper", "uses", "Method B", "method"), source_doc=doc
    )
    con.execute(
        "UPDATE edges SET valid_from = '2026-01-01T00:00:00Z' WHERE id = ?", (old,)
    )
    con.commit()
    new = store.link(con, E("Paper A", "paper", "uses", "Method B", "method"))
    day = str(
        con.execute("SELECT valid_from FROM edges WHERE id = ?", (new,)).fetchone()[0]
    )[:10]
    token = _as_restricted()
    try:
        assert store.changes(con, day)["added"]["count"] == 1
    finally:
        store.VIEWER.reset(token)  # type: ignore[arg-type]
    assert store.changes(con, day)["added"]["count"] == 0


def test_staleness_from_a_hidden_document_is_not_said(con: sqlite3.Connection) -> None:
    """Finding 7: a personal note's supersedes edge made a visible hit
    stale for a restricted token."""
    old = int(
        store.ingest_text(con, "the plan of record " * 20, title="Plan A")["doc_id"]
    )
    note = _private_doc(con)
    store.link(
        con,
        E("Plan B", "document", "supersedes", "Plan A", "document"),
        source_doc=note,
    )
    token = _as_restricted()
    try:
        assert old not in store.retrieval.staleness(
            con, [{"doc_id": old, "title": "Plan A"}]
        )
    finally:
        store.VIEWER.reset(token)  # type: ignore[arg-type]
    assert old in store.retrieval.staleness(con, [{"doc_id": old, "title": "Plan A"}])


def test_a_path_cost_counts_only_visible_documents(con: sqlite3.Connection) -> None:
    """Finding 17: a hop's cost fell with hidden documents behind it."""
    from prax.graph import paths

    open_doc = int(store.ingest_text(con, "wavelets for audio " * 20)["doc_id"])
    hidden = [_private_doc(con, f"note {i} on wavelets " * 20) for i in range(3)]
    for d in [open_doc, *hidden]:
        store.link(
            con,
            E("Paper A", "paper", "uses", "wavelet transform", "method"),
            source_doc=d,
        )
    store.link(
        con,
        E("Paper B", "paper", "uses", "wavelet transform", "method"),
        source_doc=open_doc,
    )
    ix = store.path_index(con)
    a, b = (
        int(con.execute("SELECT id FROM entities WHERE name = ?", (n,)).fetchone()[0])
        for n in ("Paper A", "Paper B")
    )
    full = paths.connect(ix, [a], [b])[0].cost
    seen = paths.connect(ix, [a], [b], hidden=frozenset(hidden))[0].cost
    assert seen > full  # the three hidden witnesses no longer lower it
    one = paths.hop_cost("uses", "EXTRACTED", 1)
    assert abs(seen - 2 * one - paths.HUB * __import__("math").log1p(2)) < 0.01


def test_a_capture_does_not_touch_a_hidden_document(con: sqlite3.Connection) -> None:
    """Finding 10: a named token sending the bytes of a hidden document
    learned its id and could give it domains."""
    from prax.capture import inbox

    data = b"the same private letter, byte for byte " * 20
    first = inbox.ingest_bytes(con, data, mime="text/plain", source="drop")
    store.set_sensitivity(con, first.doc_id, "personal")
    token = _as_restricted()
    try:
        again = inbox.ingest_bytes(
            con, data, mime="text/plain", source="drop", domains=["kitchen"]
        )
    finally:
        store.VIEWER.reset(token)  # type: ignore[arg-type]
    assert again.created is False and again.domains is None
    assert "kitchen" not in (store.document_domains(con, first.doc_id) or [])


# ------------------------------------------------------------- contracts


def test_a_reference_list_is_capped_and_in_its_numbers_order(
    con: sqlite3.Connection,
) -> None:
    """Finding 3 (and the client's N3): one call returned a book's whole
    list, 2 MB; a list read in two columns came back out of order."""
    from fastapi.testclient import TestClient

    from prax.api import app

    entries = [
        f"[{n}] A. Author. Title number {n}. Some Journal, 2001." for n in range(1, 121)
    ]
    order = entries[:16] + entries[30:41] + entries[16:30] + entries[41:]
    text = "A paper.\n\n## References\n\n" + "\n\n".join(order) + "\n"
    doc = int(store.ingest_text(con, text, title="Two columns")["doc_id"])
    with TestClient(app) as client:
        got = client.get(f"/doc/{doc}/references").json()
        assert got["entries"] == 120 and len(got["references"]) == 50
        assert got["left_out"] == 70
        assert [e.get("n") for e in got["references"][:20]] == list(range(1, 21))
        more = client.get(f"/doc/{doc}/references", params={"offset": 100}).json()
        assert [e.get("n") for e in more["references"]] == list(range(101, 121))


# ----------------------------------------------------------------- logic


def test_a_path_is_simple_and_none_is_lost_to_a_cheaper_longer_way() -> None:
    """Findings 11 and 12: a path crossed one fact twice; a node reached
    more cheaply in more hops made the shorter way vanish."""
    from prax.graph import paths

    ix = paths.build(
        [
            (10, 1, "cites", 2, "EXTRACTED", None),
            (11, 2, "cites", 3, "EXTRACTED", None),
            (12, 2, "cites", 4, "EXTRACTED", None),
        ]
    )
    found = paths.connect(ix, [1], [3], k=3)
    assert [len(p.facts) for p in found] == [2]
    ix = paths.build(
        [
            (1, 1, "mentions", 2, "AMBIGUOUS", None),
            (2, 1, "cites", 5, "EXTRACTED", None),
            (3, 5, "cites", 2, "EXTRACTED", None),
            (4, 2, "cites", 6, "EXTRACTED", None),
            (5, 6, "cites", 7, "EXTRACTED", None),
            (6, 7, "cites", 8, "EXTRACTED", None),
        ]
    )
    assert paths.connect(ix, [1], [8], max_hops=4)
    assert paths.connect(ix, [1], [7], max_hops=3)


def test_two_editions_of_one_series_conflict(con: sqlite3.Connection) -> None:
    """Finding 13: siblings under one parent were taken for one answer."""
    for venue in ("ISMIR 2008", "ISMIR 2009"):
        store.link(
            con, E("A paper", "paper", "published_in", venue, "venue"), producer="t"
        )
        store.link(con, E(venue, "venue", "part_of", "ISMIR", "venue"), producer="t")
    assert store.find_conflicts(con)["published_in"]["open"] == 1


def test_venue_reading_slips() -> None:
    """Findings 14, 19 and 22."""
    from prax.graph import venues

    assert (
        venues.plan(
            [
                (1, "IEEE MultiMedia", 5),
                (2, "ACM Multimedia", 9),
                (3, "ACM Multimedia 2019", 2),
            ],
            {},
        ).merges
        == []
    )
    assert (
        venues.read("IEEE 24th Workshop on Multimedia Signal Processing (MMSP)").edition
        == "#24"
    )
    assert venues.read("Proceedings of the 22nd symposium - SOSP '09").edition == "2009"
    assert venues.not_a_venue("Acme Corp.") == "company"
    assert venues.not_a_venue("Smith & Sons") == "publisher"


def test_a_fraction_is_an_amount() -> None:
    """Finding 20: '1/2 cup milk' became the ingredient '/2 cup milk'."""
    from prax.text import schemaorg

    assert schemaorg.ingredient_name("1/2 cup milk") == ["milk"]
    assert schemaorg.ingredient_name("1 1/2 tbsp olive oil") == ["olive oil"]


def test_traverse_is_a_guarded_read() -> None:
    """Finding 21: the guard had slid onto a row helper."""
    from prax.store.graph import traversal

    assert hasattr(traversal.traverse, "__wrapped__")
    assert not hasattr(traversal._world_said, "__wrapped__")


def test_an_edge_written_at_since_is_news(con: sqlite3.Connection) -> None:
    """Finding 23: an edge written in the very second of ``since`` was its
    own re-reading."""
    eid = store.link(con, E("Paper A", "paper", "uses", "Method B", "method"))
    at = con.execute("SELECT valid_from FROM edges WHERE id = ?", (eid,)).fetchone()[0]
    assert store.changes(con, at)["added"]["count"] == 1


# --------------------------------------------------------- docs and tests


def test_a_refused_write_leaves_nothing_behind(con: sqlite3.Connection) -> None:
    """Finding 15: a refused ingest or page write still wrote its document,
    open, though it asked to be personal."""
    from fastapi.testclient import TestClient

    from prax.api import app

    count = "SELECT count(*) FROM documents"
    before = con.execute(count).fetchone()[0]
    with TestClient(app) as client:
        bad = client.post(
            "/ingest",
            json={
                "text": "a colleague's notes " * 20,
                "domains": ["nope"],
                "sensitivity": "personal",
            },
        )
        assert bad.status_code == 400
        bad = client.post(
            "/ingest", json={"text": "notes " * 20, "sensitivity": "open"}
        )
        assert bad.status_code == 400
        bad = client.put("/page/p1", json={"text": "a page", "sensitivity": "secret"})
        assert bad.status_code == 400
    assert con.execute(count).fetchone()[0] == before
