"""Whether a quote can carry its fact (AV, the BWS survey of 2026-11-17;
``prax.text.evidence``): the four flags, which relations they speak
against, heal's look-only ``doubtful-evidence``, and an extraction that
sends such a triple to the review queue."""

from __future__ import annotations

import sqlite3

from prax import store
from prax.graph import extraction, ontology
from prax.text import evidence


def _flags(quote: str, src: str = "a", rel: str = "uses", dst: str = "b") -> list[str]:
    return evidence.flags(quote, src, rel, dst, ontology.evidence_cues())


def test_a_contents_line_speaks_against_what_asserts_not_what_is_about() -> None:
    for line in (
        "2.2.6 Tao . . . . . . . . . . 21",
        "9.1 Aparapi 197",
        "Kulturkontakte: Der Orbis Humboldtianus ......... 9",
        "reverb, 12, 45, 101",
    ):
        assert _flags(line, rel="uses") == ["contents"], line
        assert _flags(line, rel="covers") == [], line
    assert _flags("Chapter 3 introduces additive synthesis in depth", rel="uses") == []


def test_a_negation_before_an_end_speaks_against_an_assertion() -> None:
    q = "this technique is unrelated to wavetable synthesis"
    assert _flags(q, "APO", "uses", "wavetable synthesis") == ["negated"]
    q = "Unlike radial basis function schemes, our approach is local"
    assert _flags(q, "Poisson", "extends", "radial basis function") == ["negated"]
    # "unlike" is how contrasts is said, and about stays about
    assert _flags(q, "Poisson", "contrasts", "radial basis function") == []
    q = "The CVC does not use the standard Midi signals at all"
    assert _flags(q, "CVC", "about", "Midi") == []
    # "not only" adds; a negation far from the end, or inside a word, is none
    q = "it uses not only granular synthesis but also additive synthesis"
    assert _flags(q, "x", "uses", "granular synthesis") == []
    q = "Renee Friess and Georg Groh use granular synthesis throughout"
    assert _flags(q, "x", "uses", "granular synthesis") == []


def test_a_number_a_prefix_and_another_sentence_negate_nothing() -> None:
    """What the reopened review items of 2026-11-27 were flagged for and
    were right: a numbered example, a negation that is a word's prefix, a
    negation that ends before the name's sentence begins."""
    assert _flags("No. 302. Sadko.", "Sadko", "scored_for", "organ") == []
    assert _flags("outline no.1 used over G dorian", "x", "uses", "G dorian") == []
    q = "Der Kernel-Scheduler arbeitet nach dem nicht-unterbrechbaren Round-Robin"
    assert _flags(q, "Blatt 3", "uses", "Round-Robin") == []
    q = "someone who is no longer with us; Ada Lind, whose encouragement"
    assert _flags(q, "x", "influenced_by", "Ada Lind") == []
    # a model's ellipsis joins two pieces of one sentence
    q = "Our argument requires no identification... with natural parametrization"
    assert _flags(q, "x", "uses", "natural parametrization") == ["negated"]


def test_a_models_note_is_no_quote_for_any_relation() -> None:
    for q in (
        "[Cox, 1946] (Note: Title inferred from standard citation)",
        "ZOOM is a Japanese company, but the text only says ZOOM",
        "Title not explicitly given as standalone paper title in text",
    ):
        assert "commentary" in _flags(q, rel="cites"), q
    assert _flags("A note on the sampling theorem, as the paper calls it") == []
    q = "252. Lee Morgan: (implied by context of jazz saxophonist)"
    assert "commentary" in _flags(q, rel="plays", dst="saxophone")
    assert _flags("the moods implied by the two versions differ", rel="about") == []
    # a cue is a whole word: "i cannot" is not in "a GUI cannot"
    assert _flags("a delay occurs when a GUI cannot process events") == []


def test_a_quote_its_text_holds_is_the_texts_words(con: sqlite3.Connection) -> None:
    """A cue for a model's note said in the text itself ("had so far only
    been inferred from certain analogies") is the author's: all 13 such
    among the 133 reopened items the cue flagged on 2026-11-27."""
    q = "Its stability had so far only been inferred from certain analogies."
    cues = ontology.evidence_cues()
    assert evidence.flags(q, "filter", "uses", "analogies", cues) == ["commentary"]
    assert evidence.flags(q, "filter", "uses", "analogies", cues, in_text=True) == []
    doc_id = _doc(con, "Wave digital filters are old. " + q)
    note = "uses analogies (Note: inferred from context)"
    triples = [
        extraction.Triple("filter", "method", "uses", d, "concept", "EXTRACTED", e)
        for d, e in (("analogies", q), ("unit elements", note))
    ]
    report = extraction.apply(
        con, doc_id, extraction.Extraction(triples=triples), extractor="test"
    )
    assert (report.linked, report.queued) == (1, 1)
    queued = con.execute("SELECT dst FROM review_queue").fetchall()
    assert [r[0] for r in queued] == ["unit elements"]


def test_who_plays_what_is_what_the_quote_names() -> None:
    """A model fills an instrument in from what it knows: the quote is
    the document's word for the player and none for the instrument (the
    pilot's trumpeters given saxophones, 2026-11-26). Made-up names."""
    q = "Anna Lind uses diminution of part of the outline."
    assert _flags(q, "Anna Lind", "plays", "saxophone") == ["unnamed"]
    for q, dst in (
        ("the bass line by Anna Lind, from her second record", "electric bass"),
        ("Master of Music in jazz piano performance", "jazz piano"),
        ("Lind, the saxophonist of the quartet", "saxophone"),
    ):
        assert _flags(q, "Anna Lind", "plays", dst) == [], q
    # another relation is not asked to name its target
    assert _flags(q, "Anna Lind", "exemplifies", "outline") == []


def test_noise_is_a_quote_of_no_words() -> None:
    assert _flags("qwrt7x zxv9 plk3 trs8 mnb") == ["noise"]
    assert _flags("K��� �� ��� ��") == ["noise"]
    # another script, broken accents, Markdown and numbers are not noise
    for q in (
        "ايقاع دور كبير المكون من وحدات",
        "Ren´ee Frieß, Dr. Georg Groh, Niklas Kl¨ugel",
        "_Valis_ [10], _Brain Opera_ [15], and _Toy Symphony_ [9]",
        "y = a * x + b",  # maths is left alone
    ):
        assert "noise" not in _flags(q), q


def _doc(con: sqlite3.Connection, text: str) -> int:
    return int(store.ingest_text(con, text, title="Polygon oscillators")["doc_id"])


def test_heal_names_the_edges_and_extraction_sends_them_to_review(
    con: sqlite3.Connection,
) -> None:
    text = (
        "The arbitrary polygon oscillator draws a waveform from a polygon. "
        "This technique is unrelated to wavetable synthesis."
    )
    doc_id = _doc(con, text)
    store.link(
        con,
        store.Edge(
            "polygon oscillator", "method", "uses", "wavetable synthesis", "method"
        ),
        source_doc=doc_id,
        evidence="This technique is unrelated to wavetable synthesis.",
        producer="test",
    )
    found = store.health(con, only=["doubtful-evidence"])["ailments"][0]
    assert found["count"] == 1 and not found["repairable"]
    assert found["examples"][0]["flags"] == ["negated"]
    # an extraction writes the good triple and queues the doubtful one
    triples = [
        extraction.Triple(
            "polygon oscillator",
            "method",
            "extends",
            "wavetable synthesis",
            "method",
            "EXTRACTED",
            "This technique is unrelated to wavetable synthesis.",
        ),
        extraction.Triple(
            "polygon oscillator",
            "method",
            "uses",
            "polygon",
            "concept",
            "EXTRACTED",
            "draws a waveform from a polygon",
        ),
    ]
    report = extraction.apply(
        con, doc_id, extraction.Extraction(triples=triples), extractor="test"
    )
    assert (report.linked, report.queued) == (1, 1)
    reason = con.execute(
        "SELECT reason FROM review_queue ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert reason == "the quote is no evidence: negated"


def test_a_cited_work_the_quote_names_stands_though_the_model_was_unsure() -> None:
    cues = ontology.evidence_cues()
    q = "Lind (2001) showed that a loop drifts"
    assert evidence.stands_unsure(q, "cites", "Lind (2001)", cues)
    assert not evidence.stands_unsure("a loop drifts", "cites", "Lind (2001)", cues)
    assert not evidence.stands_unsure(q, "uses", "Lind (2001)", cues)


def test_the_replay_leaves_a_refused_quote_to_a_person_and_heal_undoes_its_links(
    con: sqlite3.Connection,
) -> None:
    """Until 2026-11-27 the nightly replay linked every open item that fit
    the ontology, the ones refused for their quote too (415 of 434). Now
    it leaves them, and heal ends what it wrote and opens them again."""
    from prax.graph import review

    doc_id = _doc(con, "This technique is unrelated to wavetable synthesis. " * 3)
    quote = "This technique is unrelated to wavetable synthesis."
    triple = extraction.Triple(
        "polygon oscillator", "method", "extends", "wavetable synthesis",
        "method", "EXTRACTED", quote,
    )  # fmt: skip
    extraction.apply(
        con, doc_id, extraction.Extraction(triples=[triple]), extractor="test"
    )
    (item,) = store.list_review(con)
    assert review.replay(con).linked == 0
    assert store.list_review(con)[0]["id"] == item["id"]
    # what the replay did before: the item linked as it stood
    edge = store.Edge(
        "polygon oscillator", "method", "extends", "wavetable synthesis", "method"
    )
    store.link(
        con, edge, source_doc=doc_id, evidence=quote, producer="replay", run="r1"
    )
    store.resolve_review(con, item["id"], "linked")
    found = store.health(con, only=["replayed-refusals"])["ailments"][0]
    assert found["count"] == 1 and found["repairable"]
    assert found["examples"][0]["flags"] == ["negated"]
    out = store.heal(con, only=["replayed-refusals"])
    assert out["replayed-refusals"]["repaired"] == 1
    assert not store.find_edges(con, edge, source_doc=doc_id)
    assert [r["id"] for r in store.list_review(con)] == [item["id"]]
    assert store.health(con, only=["replayed-refusals"])["ailments"][0]["count"] == 0


def test_an_importers_note_is_no_quote_and_is_not_looked_for(
    con: sqlite3.Connection,
) -> None:
    """AV, 2026-11-20: a citation record, a page's link or a rule's
    premises is a note of where a fact came from. The quote matcher had
    stamped some 69,000 of them "not found"; it no longer looks for one,
    heal does not read one as a quote, and ``why`` says what it is."""
    doc_id = _doc(con, "The arbitrary polygon oscillator draws a polygon. " * 3)
    note = store.link(
        con,
        store.Edge("Polygon oscillators", "paper", "cites", "Some Paper", "paper"),
        source_doc=doc_id,
        evidence="crossref W1 references doi:10.1/x (not here, not nowhere)",
        producer="crossref",
    )
    quote = store.link(
        con,
        store.Edge("Polygon oscillators", "paper", "about", "polygon", "concept"),
        source_doc=doc_id,
        evidence="draws a polygon",
        producer="test",
    )
    out = store.maintain(con, only=["places"])["places"]
    assert (out["placed"], out["not_in_text"]) == (1, 0)
    assert store.evidence_place(con, note) == {
        "stands": "a note, not a quote",
        "note": "crossref",
    }
    assert store.evidence_place(con, quote)["stands"] == "where it stood"
    # the same words from a model are a doubtful quote; from the importer, none
    assert _flags("Note: the text only says so", rel="cites") == ["commentary"]
    store.link(
        con,
        store.Edge("Polygon oscillators", "paper", "cites", "Other Paper", "paper"),
        source_doc=doc_id,
        evidence="Note: the text only says so",
        producer="references",
    )
    found = store.health(con, only=["doubtful-evidence"])["ailments"]
    assert not found or found[0]["count"] == 0


def test_every_writer_of_notes_is_on_the_list() -> None:
    """``graph.edges.NOTED`` names the writers' own constants, so a renamed
    producer cannot fall off it."""
    from prax.graph import resolution
    from prax.importers import citations
    from prax.store.graph import citations as cited
    from prax.store.graph import edges, geo, schemaorg

    for producer in (
        citations.CrossrefSource.name,
        citations.OpenAlexSource.name,
        cited.REFERENCES_PRODUCER,
        schemaorg.MARKUP_PRODUCER,
        geo.PLACE_PRODUCER,
        resolution.PRODUCER,
        "rule:transitive",
        "page",
        "polysemy",
    ):
        assert edges.noted(producer), producer
    for producer in ("test", "human", "qwen3.6-35b/prompt1", "typing-rules", "heal"):
        assert not edges.noted(producer), producer
