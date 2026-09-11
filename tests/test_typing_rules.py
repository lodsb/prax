"""The typing rules over the review queue: retyping the document, flipping
authored_by, renaming cites/about, dropping the hopeless, leaving the rest."""

from __future__ import annotations

import sqlite3

from prax import review, store


def _queue(
    con: sqlite3.Connection, doc: int, src: str, st: str, rel: str, dst: str, dt: str
) -> int:
    store.queue_review(
        con,
        src=src,
        src_type=st,
        rel=rel,
        dst=dst,
        dst_type=dt,
        reason=f"'{rel}' does not accept",
        source_doc=doc,
        evidence="q",
    )
    return con.execute("SELECT max(id) FROM review_queue").fetchone()[0]


def test_decide_rules() -> None:
    doc = ("A Manual", "paper")
    item = lambda src, st, rel, dst, dt: {
        "src": src,
        "src_type": st,
        "rel": rel,
        "dst": dst,
        "dst_type": dt,
    }
    # the document typed as a tool: retyped, then valid as is
    a, edges, rule = review.decide(
        item("A Manual", "tool", "about", "reverb", "concept"), doc
    )
    assert a == "link" and rule == "self-name" and edges[0].src_type == "paper"
    # authored_by backwards
    a, edges, rule = review.decide(
        item("Ann Author", "author", "authored_by", "A Manual", "paper"), doc
    )
    assert a == "link" and rule == "flip-authored_by"
    assert (edges[0].src, edges[0].dst) == ("A Manual", "Ann Author")
    # authors on both ends: both are authors of the document
    a, edges, rule = review.decide(
        item("Ann Author", "author", "authored_by", "Bob Author", "author"), doc
    )
    assert a == "link" and rule == "authors-both-ends" and len(edges) == 2
    a, edges, _ = review.decide(
        item("Ann Author", "author", "authored_by", "Ann Author", "author"), doc
    )
    assert len(edges) == 1
    # cites for a tool the paper uses; about for a claim it makes
    a, edges, rule = review.decide(
        item("A Manual", "paper", "cites", "Matlab", "tool"), doc
    )
    assert a == "link" and edges[0].rel == "uses" and rule == "cites->uses"
    a, edges, _ = review.decide(
        item("A Manual", "paper", "about", "X holds", "claim"), doc
    )
    assert edges[0].rel == "proposes"
    a, edges, _ = review.decide(
        item("Matlab", "tool", "about", "filtering", "concept"), doc
    )
    assert edges[0].rel == "implements"
    # drops
    a, _, rule = review.decide(item("A Manual", "paper", "cites", "Ann", "author"), doc)
    assert a == "drop" and rule.startswith("drop-cites")
    a, _, _ = review.decide(item("Ann", "author", "about", "reverb", "concept"), doc)
    assert a == "drop"
    a, _, rule = review.decide(
        item("A Manual", "paper", "about", "x dst_type=concept", "paper"), doc
    )
    assert a == "drop" and rule == "malformed-name"
    # an ontology gap stays open
    a, _, rule = review.decide(
        item("A Manual", "paper", "contrasts", "Matlab", "tool"), doc
    )
    assert a == "open" and rule is None
    # no document title: nothing to retype or flip
    a, _, _ = review.decide(item("Ann", "author", "authored_by", "Bob", "author"), None)
    assert a == "open"


def test_apply_links_drops_and_leaves(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "text " * 40, title="A Manual")["doc_id"]
    _queue(con, doc, "A Manual", "tool", "about", "reverb", "concept")
    _queue(con, doc, "Ann Author", "author", "authored_by", "A Manual", "paper")
    _queue(con, doc, "A Manual", "paper", "cites", "Matlab", "tool")
    _queue(con, doc, "A Manual", "paper", "cites", "Ann", "author")
    _queue(con, doc, "A Manual", "paper", "contrasts", "Matlab", "tool")
    dry = review.apply_typing_rules(con, commit=False)
    assert (dry.checked, dry.linked, dry.dropped, dry.still_open) == (5, 3, 1, 1)
    assert store.count_review(con) == 5  # nothing written
    rep = review.apply_typing_rules(con, commit=True)
    assert (rep.linked, rep.dropped, rep.still_open) == (3, 1, 1)
    assert rep.by_rule == {
        "self-name": 1,
        "flip-authored_by": 1,
        "cites->uses": 1,
        "drop-cites-paper-author": 1,
    }
    assert store.count_review(con) == 1
    edges = {
        (r["s"], r["rel"], r["t"], r["confidence"], r["producer"])
        for r in con.execute(
            "SELECT s.name s, x.rel, t.name t, x.confidence, x.producer FROM edges x"
            " JOIN entities s ON s.id = x.src JOIN entities t ON t.id = x.dst"
            " WHERE x.valid_to IS NULL AND x.producer = 'typing-rules'"
        )
    }
    assert edges == {
        ("A Manual", "about", "reverb", "INFERRED", "typing-rules"),
        ("A Manual", "authored_by", "Ann Author", "INFERRED", "typing-rules"),
        ("A Manual", "uses", "Matlab", "INFERRED", "typing-rules"),
    }
    # a second pass finds nothing new
    again = review.apply_typing_rules(con, commit=True)
    assert again.linked == 0 and again.dropped == 0 and again.still_open == 1


def test_decide_unmapped_rules() -> None:
    doc = ("A Paper Title Long Enough", "paper")
    item = lambda src, rel, dst, reason="": {
        "src": src,
        "src_type": None,
        "rel": rel,
        "dst": dst,
        "dst_type": None,
        "reason": reason,
    }
    a, edges, rule = review.decide_unmapped(
        item("Andrew Barker", "affiliation", "University of Birmingham"), doc
    )
    assert a == "link" and rule == "affiliation"
    assert (edges[0].src, edges[0].rel, edges[0].dst, edges[0].dst_type) == (
        "Andrew Barker",
        "affiliated_with",
        "University of Birmingham",
        "organization",
    )
    # either way round
    a, edges, _ = review.decide_unmapped(
        item("Chalmers University of Technology", "affiliation", "Anders Garder"), doc
    )
    assert edges[0].src == "Anders Garder" and edges[0].dst_type == "organization"
    # two people: open
    a, _, _ = review.decide_unmapped(
        item("Ann Author", "affiliation", "Bob Author"), doc
    )
    assert a == "open"
    a, edges, rule = review.decide_unmapped(
        item("Niklas Klugel", "supervised_by", "Johann Schlichter"), doc
    )
    assert a == "link" and edges[0].rel == "advised_by" and rule == "advised_by"
    a, edges, _ = review.decide_unmapped(
        item("The origins of music: A review of theories", "author", "Walter B Brown"),
        doc,
    )
    assert (
        a == "link" and edges[0].rel == "authored_by" and edges[0].src_type == "paper"
    )
    a, edges, _ = review.decide_unmapped(
        item("PHASERET", "funded_by", "Austrian Science Fund (FWF)"), doc
    )
    assert a == "link" and edges[0].dst_type == "organization"
    a, edges, _ = review.decide_unmapped(
        item("single-reed aerophone", "developed_by", "Johann Christoph Denner"), doc
    )
    assert edges[0].dst_type == "author" and edges[0].src_type == "tool"
    a, edges, rule = review.decide_unmapped(
        item("paper", "mentions", "SyncPlayer", "named tool discussed as related work"),
        doc,
    )
    assert a == "link" and rule == "mentions"
    assert (edges[0].src, edges[0].dst_type) == ("A Paper Title Long Enough", "tool")
    a, _, _ = review.decide_unmapped(
        item("paper", "mentions", "X", "no hint here"), doc
    )
    assert a == "open"
    a, _, _ = review.decide_unmapped(item("rhythm", "related_to", "melody"), doc)
    assert a == "open"


def test_apply_covers_unmapped_items(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "text " * 40, title="A Paper Title Long Enough")[
        "doc_id"
    ]
    store.queue_review(
        con,
        src="Andrew Barker",
        src_type=None,
        rel="affiliation",
        dst="University of Birmingham",
        dst_type=None,
        reason="unmapped: Affiliation is stated",
        source_doc=doc,
    )
    store.queue_review(
        con,
        src="rhythm",
        src_type=None,
        rel="related_to",
        dst="melody",
        dst_type=None,
        reason="unmapped: background",
        source_doc=doc,
    )
    rep = review.apply_typing_rules(con, commit=True)
    assert rep.linked == 1 and rep.still_open == 1 and rep.by_rule == {"affiliation": 1}
    assert store.count_review(con) == 1
