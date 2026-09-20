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


def test_keyword_side_leaves_the_stopwords_out(con: sqlite3.Connection) -> None:
    from prax.store import retrieval

    terms = store.expand_query(con, "the cache and a compiler in C")
    assert retrieval.keyword_terms(terms) == [["cache"], ["compiler"]]  # C alone: no
    assert retrieval._expr(retrieval.keyword_terms(terms), all_terms=False) == (
        '("cache") OR ("compiler")'
    )
    assert retrieval._fts_query("the cache and a compiler") == '"cache" OR "compiler"'
    # a query of stopwords alone keeps them (German too)
    assert retrieval.keyword_terms([["the"], ["and"]]) == [["the"], ["and"]]
    assert retrieval._fts_query("und der") == '"und" OR "der"'
    assert retrieval.keyword_terms([["der"], ["klang"]]) == [["klang"]]
    # the search still finds a document by its content words, with the
    # stopwords in the query, and one of stopwords alone still matches
    doc = store.ingest_text(
        con, "The cache lives in the compiler and a linker. " * 8, title="Caches"
    )["doc_id"]
    hits = store.search(con, "what is the cache in a compiler", mode="fts")
    assert hits and hits[0]["doc_id"] == doc
    assert store.search(con, "the and", mode="fts")


def test_a_lone_character_is_not_a_keyword(con: sqlite3.Connection) -> None:
    from prax.store import retrieval

    assert retrieval.keyword_terms([["rule"], ["110"], ["2"], ["c"]]) == [
        ["rule"],
        ["110"],
    ]
    assert retrieval._fts_query("a 2 c") == '"a" OR "2" OR "c"'  # nothing but
    doc = store.ingest_text(con, "Rule 110 is a cellular automaton. " * 8, title="R")[
        "doc_id"
    ]
    hits = store.search(con, "rule 110 in 2 steps", mode="fts")
    assert hits and hits[0]["doc_id"] == doc


def test_the_keyword_index_is_warmed_and_merged(con: sqlite3.Connection) -> None:
    for n in range(6):
        store.ingest_text(con, f"segment {n} of text " * 30, title=f"S{n}")
    warmed = store.warm_fts(con)
    assert warmed["blocks"] >= 1 and warmed["bytes"] > 0
    rep = store.fts_merge(con, seconds=5)
    assert rep["steps"] >= 1 and rep["segments_after"] <= rep["segments_before"]
    assert store.search(con, "segment text", mode="fts")
