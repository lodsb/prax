"""Acronyms the library defines, and what the search does with them."""

from __future__ import annotations

import sqlite3

from prax import acronyms, store


def test_find_keeps_definitions_whose_letters_are_initials() -> None:
    text = (
        "We apply a method called anti-derivative anti aliasing (ADAA) to the"
        " hidden Markov model (HMM). The Short-Time Fourier Transform (STFT)"
        " and nonnegative matrix factorization (NMF) follow. Work for Computer"
        " Supported Collaborative Music (CSCM) is cited; see also the (2019)"
        " figure and Multiple Virtual Storage (MVS). A mismatch (XYZ) is dropped."
    )
    found = acronyms.find(text)
    assert ("adaa", "anti derivative anti aliasing") in found
    assert ("hmm", "hidden markov model") in found
    assert ("stft", "short time fourier transform") in found
    assert ("nmf", "nonnegative matrix factorization") in found
    assert ("cscm", "computer supported collaborative music") in found
    assert not any(a == "xyz" for a, _ in found)
    assert not any(a == "2019" for a, _ in found)
    counts = acronyms.tally([text, text, "nothing here"])
    assert counts[("hmm", "hidden markov model")] == 2


def test_acronym_table_and_expansion(con: sqlite3.Connection) -> None:
    store.replace_acronyms(
        con,
        [
            ("adaa", "antiderivative antialiasing", 3),
            ("adaa", "anti derivative anti aliasing", 1),
            ("hmm", "hidden markov model", 9),
        ],
    )
    assert store.acronym_expansions(con, "adaa", min_docs=3) == [
        "antiderivative antialiasing"
    ]
    assert store.acronym_expansions(con, "ADAA") == [
        "antiderivative antialiasing",
        "anti derivative anti aliasing",
    ]
    assert store.acronym_expansions(con, "iir") == []
    terms = store.expand_query(con, "ADAA iir algorithms")
    assert terms == [
        ["adaa", "antiderivative antialiasing", "anti derivative anti aliasing"],
        ["iir"],
        ["algorithms"],
    ]
    assert store.expanded_text(terms).startswith("adaa antiderivative antialiasing")
    # the second run replaces the table
    store.replace_acronyms(con, [("fdn", "feedback delay network", 2)])
    assert store.acronym_expansions(con, "adaa") == []


def test_search_expands_acronyms_and_ranks_all_terms_first(
    con: sqlite3.Connection,
) -> None:
    adaa = store.ingest_text(
        con,
        "Antiderivative antialiasing reduces aliasing in stateful systems; the"
        " method extends to IIR structures with memory. " * 8,
        title="Antiderivative antialiasing for stateful systems",
    )["doc_id"]
    iir = store.ingest_text(
        con,
        "IIR filter algorithms: design of infinite impulse response filters,"
        " algorithms for coefficient computation. " * 8,
        title="IIR filter design",
    )["doc_id"]
    store.ingest_text(con, "Unrelated text about tabletops. " * 8, title="Tables")
    # nothing in the store says "adaa" literally
    assert store.search(con, "adaa", mode="fts") == []
    store.replace_acronyms(con, [("adaa", "antiderivative antialiasing", 2)])
    hits = store.search(con, "adaa", mode="fts")
    assert hits and hits[0]["doc_id"] == adaa
    # fused (no vectors in tests): the document holding the expanded phrase
    # and "iir" ranks above the one with only the common term
    hits = store.search(con, "adaa iir")
    assert [h["doc_id"] for h in hits][:2] == [adaa, iir]
    assert hits[0]["fts_rank"] == 1
