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
    # the affiliation on the first page read as a location: written there;
    # the institution's city is not where a document is
    a, edges, rule = review.decide(
        item(
            "A Manual",
            "paper",
            "located_in",
            "Harbin Institute of Technology",
            "organization",
        ),
        doc,
    )
    assert a == "link" and rule == "located_in->written_at"
    assert edges[0].rel == "written_at" and edges[0].dst_type == "organization"
    a, edges, rule = review.decide(
        item("A Manual", "paper", "located_in", "Harbin", "place"), doc
    )
    assert a == "drop" and rule == "drop-located_in-paper-place"
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
    # v6: a "cited" person is mentioned (the weak relation takes people now)
    a, edges, rule = review.decide(
        item("A Manual", "paper", "cites", "Ann", "author"), doc
    )
    assert a == "link" and edges[0].rel == "mentions" and rule == "cites->mentions"
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


def test_self_as_device() -> None:
    doc = ("Iridium User Manual", "manual", "Waldorf Iridium")
    item = lambda src, st, rel, dst, dt: {
        "src": src,
        "src_type": st,
        "rel": rel,
        "dst": dst,
        "dst_type": dt,
    }
    a, edges, rule = review.decide(
        item("Iridium User Manual", "manual", "has_feature", "xy pad", "feature"), doc
    )
    assert a == "link" and rule == "self-as-device"
    assert (edges[0].src, edges[0].src_type, edges[0].rel) == (
        "Waldorf Iridium",
        "device",
        "has_feature",
    )
    a, edges, rule = review.decide(
        item("Iridium User Manual", "schematic", "covers", "PIC18", "component"), doc
    )
    assert rule == "self-as-device" and edges[0].rel == "has_part"
    # untyped: the relation names the type of the other end
    a, edges, rule = review.decide_unmapped(
        item("Iridium User Manual", None, "has_spec", "128 voices", None), doc
    )
    assert rule == "self-as-device" and edges[0].dst_type == "spec"
    # without a known device, or for another subject, nothing changes
    a, _, rule = review.decide(
        item("Iridium User Manual", "manual", "has_feature", "xy pad", "feature"),
        ("Iridium User Manual", "manual"),
    )
    assert rule != "self-as-device"
    a, _, rule = review.decide(
        item("Waldorf Quantum", "device", "has_feature", "xy pad", "feature"), doc
    )
    assert rule != "self-as-device"


def test_apply_links_drops_and_leaves(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "text " * 40, title="A Manual")["doc_id"]
    _queue(con, doc, "A Manual", "tool", "about", "reverb", "concept")
    _queue(con, doc, "Ann Author", "author", "authored_by", "A Manual", "paper")
    _queue(con, doc, "A Manual", "paper", "cites", "Matlab", "tool")
    _queue(con, doc, "A Manual", "paper", "cites", "Ann", "author")
    _queue(con, doc, "A Manual", "paper", "contrasts", "Matlab", "tool")
    dry = review.apply_typing_rules(con, commit=False)
    assert (dry.checked, dry.linked, dry.dropped, dry.still_open) == (5, 4, 0, 1)
    assert store.count_review(con) == 5  # nothing written
    rep = review.apply_typing_rules(con, commit=True)
    assert (rep.linked, rep.dropped, rep.still_open) == (4, 0, 1)
    assert rep.by_rule == {
        "self-name": 1,
        "flip-authored_by": 1,
        "cites->uses": 1,
        "cites->mentions": 1,
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
        ("A Manual", "mentions", "Ann", "INFERRED", "typing-rules"),
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
        item("Erik Lindqvist", "supervised_by", "Maria Novak"), doc
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


def test_placeholders_and_near_misses() -> None:
    """The second batch of rules, written from the queue after the v5
    re-read: placeholder names are dropped on either side, a cited
    "document" or "work" is a paper, a listed "person" is the author, the
    document itself is published in a venue-shaped thing and cites a
    title-shaped thing; one-word documents and URL venues stay open."""
    doc = ("Polyphonic Piano Note Transcription With Recurrent Networks", "paper")
    typed = lambda src, st, rel, dst, dt: {
        "src": src,
        "src_type": st,
        "rel": rel,
        "dst": dst,
        "dst_type": dt,
        "reason": "",
    }
    untyped = lambda src, rel, dst: typed(src, None, rel, dst, None)
    title = doc[0]
    assert (
        review.decide(
            typed("source name", "author", "authored_by", "Ada", "person"), doc
        )[0]
        == "drop"
    )
    assert review.decide_unmapped(untyped(title, "published_in", "?"), doc)[0] == "drop"
    assert (
        review.decide_unmapped(untyped(title, "unknown", "R55"), doc)[2]
        == "no-relation"
    )
    assert (
        review.decide_unmapped(untyped("rhythm", "related_to", "melody"), doc)[0]
        == "open"
    )
    a, edges, rule = review.decide(
        typed(title, "paper", "cites", "The Scientist's Guide to DSP", "document"), doc
    )
    assert (a, rule, edges[0].dst_type) == (
        "link",
        "retype-cites-paper-document",
        "paper",
    )
    a, edges, rule = review.decide(
        typed(title, "paper", "cites", "Galton", "document"), doc
    )
    assert (a, edges[0].rel, edges[0].dst_type) == ("link", "mentions", "document")
    a, edges, rule = review.decide(
        typed(title, "paper", "authored_by", "Ben Mildenhall", "person"), doc
    )
    assert (a, edges[0].dst_type) == ("link", "author")
    a, edges, rule = review.decide_unmapped(
        untyped(title, "published_in", "Journal of Personality and Social Psychology"),
        doc,
    )
    assert (a, rule, edges[0].dst_type, edges[0].src_type) == (
        "link",
        "published_in",
        "venue",
        "paper",
    )
    assert (
        review.decide_unmapped(
            untyped(title, "published_in", "www4.in.tum.de/~rumpe/se"), doc
        )[0]
        == "open"
    )
    assert (
        review.decide_unmapped(
            untyped(title, "published_in", "[Venue Not Stated]"), doc
        )[0]
        == "drop"
    )
    assert (
        review.decide_unmapped(
            untyped("Some Other Paper", "published_in", "Leonardo"), doc
        )[0]
        == "open"
    )
    a, edges, rule = review.decide_unmapped(
        untyped(title, "cites", "A Territorial Survey of Oceanic Music and Dance"), doc
    )
    assert (a, rule, edges[0].dst_type) == ("link", "cites-title", "paper")
    assert (
        review.decide_unmapped(untyped(title, "cites", "Hans Boehm"), doc)[0] == "open"
    )
    a, edges, rule = review.decide_unmapped(untyped(title, "funded_by", "DFG"), doc)
    assert (a, edges[0].dst_type) == ("link", "organization")


def test_v7_relations_for_untyped_items() -> None:
    doc = ("Reaktor 5 Core Reference", "manual")
    item = lambda src, rel, dst: {
        "src": src,
        "src_type": None,
        "rel": rel,
        "dst": dst,
        "dst_type": None,
        "reason": "",
    }
    a, edges, rule = review.decide_unmapped(
        item("Native Instruments GmbH", "located_in", "Berlin"), doc
    )
    assert (a, rule, edges[0].src_type, edges[0].dst_type) == (
        "link",
        "located_in",
        "organization",
        "place",
    )
    assert (
        review.decide_unmapped(item("Ada Lovelace", "located_in", "London"), doc)[0]
        == "open"
    )
    a, edges, rule = review.decide_unmapped(
        item("Reaktor 5 Core Reference", "published_by", "Native Instruments GmbH"), doc
    )
    assert (a, edges[0].src_type, edges[0].rel, edges[0].dst_type) == (
        "link",
        "manual",
        "published_by",
        "organization",
    )
    a, edges, rule = review.decide_unmapped(
        item(
            "CCRMA Center for Computer Research",
            "affiliated_with",
            "Stanford University",
        ),
        doc,
    )
    assert (a, rule, edges[0].rel) == ("link", "affiliation->part_of", "part_of")
    a, edges, rule = review.decide_unmapped(
        item("Music and Audio Research Lab", "part_of", "New York University"), doc
    )
    assert (a, rule) == ("link", "part_of-organizations")
    # a university and its lecture course are not two organizations
    assert (
        review.decide_unmapped(
            item(
                "Technische Universität München",
                "affiliated_with",
                "Grundlagen Betriebssysteme",
            ),
            doc,
        )[0]
        == "open"
    )
