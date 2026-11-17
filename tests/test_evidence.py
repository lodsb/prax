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


def test_a_models_note_is_no_quote_for_any_relation() -> None:
    for q in (
        "[Cox, 1946] (Note: Title inferred from standard citation)",
        "ZOOM is a Japanese company, but the text only says ZOOM",
        "Title not explicitly given as standalone paper title in text",
    ):
        assert "commentary" in _flags(q, rel="cites"), q
    assert _flags("A note on the sampling theorem, as the paper calls it") == []


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
