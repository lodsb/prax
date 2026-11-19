"""Under what licence a document is offered (AV, the BWS survey of
2026-11-17; ``prax.text.licences``, ``store.set_licence``, the
``licences`` pass): read from the line a text prints about itself and from
its records, the most trusted kept, and filterable in a search and a walk."""

from __future__ import annotations

import sqlite3

import pytest

from prax import store
from prax.importers import citations
from prax.text import licences as L

E = store.Edge


def test_a_licence_is_read_from_its_url_and_its_name() -> None:
    assert L.from_url("http://creativecommons.org/licenses/by-nc-sa/4.0/") == L.Licence(
        "CC-BY-NC-SA-4.0", "https://creativecommons.org/licenses/by-nc-sa/4.0/"
    )
    assert L.from_url("https://creativecommons.org/publicdomain/zero/1.0/").id == (
        "CC0-1.0"
    )
    assert L.from_url("http://arxiv.org/licenses/nonexclusive-distrib/1.0/").id == (
        L.ARXIV
    )
    assert L.from_url("https://www.elsevier.com/tdm/userlicense/1.0/").id == (
        L.PUBLISHER
    )
    for words, want in (
        ("Creative Commons Attribution 4.0 International License", "CC-BY-4.0"),
        ("Creative Commons Attribution-NonCommercial-NoDerivatives 4.0", None),
        ("Creative Commons AttributionNoncommercial-Share Alike 3.0", None),
        ("CC BY-SA 4.0", "CC-BY-SA-4.0"),
        ("the Creative Commons Attribution License", "CC-BY"),
        ("CC0", "CC0-1.0"),
        ("creativecommons.org/licenses/by/ 4.0/", "CC-BY-4.0"),
    ):
        got = L.from_name(words)
        assert got is not None, words
        if want:
            assert got.id == want, words
    assert L.from_name(
        "Creative Commons Attribution-NonCommercial-NoDerivatives 4.0"
    ) == L.Licence(
        "CC-BY-NC-ND-4.0", "https://creativecommons.org/licenses/by-nc-nd/4.0/"
    )
    assert L.from_name("Creative Commons AttributionNoncommercial-Share Alike 3.0") == (
        L.Licence(
            "CC-BY-NC-SA-3.0", "https://creativecommons.org/licenses/by-nc-sa/3.0/"
        )
    )
    assert L.from_name("all rights reserved") is None


def test_the_line_a_text_prints_about_itself_and_only_that() -> None:
    head = (
        "A Study of Onsets\n\nLicensed under a Creative Commons Attribution 4.0"
        " International License (CC BY 4.0). Attribution: A. Author.\n\n"
    )
    got = L.printed(head + "body text. " * 50)
    assert got is not None and got.id == "CC-BY-4.0"
    assert got.words and got.words.startswith("Licensed under")
    tail = "This is an open-access article distributed under the terms of the"
    tail += " Creative Commons Attribution 3.0 Unported License."
    assert L.printed("x. " * 5000 + tail).id == "CC-BY-3.0"  # type: ignore[union-attr]
    # a dataset the paper releases is not the paper's licence
    data = "We release the dataset under a CC BY-NC 4.0 licence. " + "y. " * 50
    assert L.printed(data) is None
    # the middle of a long text is not looked at
    middle = "z. " * 4000 + "This article is licensed under CC BY 4.0. " + "z. " * 4000
    assert L.printed(middle) is None


def test_what_a_filter_asks_for() -> None:
    assert L.matches("CC-BY-4.0", "open") and L.matches("CC0-1.0", "open")
    assert not L.matches("CC-BY-NC-4.0", "open") and L.matches("CC-BY-NC-4.0", "cc")
    assert L.matches("CC-BY-3.0", "cc-by") and not L.matches("CC-BY-SA-4.0", "CC-BY")
    assert L.matches("CC-BY-4.0", "CC-BY-4.0") and L.matches(L.ARXIV, "any")
    assert not L.matches(L.PUBLISHER, "cc")


def test_a_source_never_replaces_a_more_trusted_one(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "x " * 40, title="A paper")["doc_id"]
    store.set_licence(con, doc, "CC-BY-4.0", by="printed")
    held = store.set_licence(con, doc, "CC-BY-NC-4.0", by="crossref")
    assert held["id"] == "CC-BY-4.0" and held["by"] == "printed"
    mine = store.set_licence(con, doc, "CC-BY-SA-4.0")  # a person's word
    assert mine["by"] == "human"
    assert mine["url"] == "https://creativecommons.org/licenses/by-sa/4.0/"
    with pytest.raises(ValueError):
        store.set_licence(con, doc, " ")
    with pytest.raises(ValueError):
        store.set_licence(con, doc, "MIT", by="rumour")


def test_the_pass_reads_each_text_once_and_a_search_and_a_walk_filter(
    con: sqlite3.Connection,
) -> None:
    words = "spectral flux onset detection "
    printed = store.ingest_text(
        con,
        "This article is licensed under a Creative Commons Attribution 4.0"
        " International License.\n\n" + words * 20,
        title="Open paper",
    )["doc_id"]
    recorded = store.ingest_text(
        con,
        words * 20,
        title="Recorded paper",
        meta={"fields": {"rights": "CC BY-NC 4.0"}},
    )["doc_id"]
    silent = store.ingest_text(con, words * 21, title="Silent paper")["doc_id"]
    person = store.ingest_text(
        con,
        "This work is licensed under CC BY 4.0. " + words * 20,
        title="Decided paper",
    )["doc_id"]
    store.set_licence(con, person, "MIT")
    done = store.maintain(con, only=["licences"])["licences"]
    assert done["read"] == 4 and done["by"] == {"printed": 1, "record": 1}
    meta = store.get_meta(con, printed)
    assert meta["licence"]["id"] == "CC-BY-4.0"
    assert meta["licence"]["words"].startswith("This article is licensed")
    assert store.get_meta(con, recorded)["licence"]["id"] == "CC-BY-NC-4.0"
    assert "licence" not in store.get_meta(con, silent)
    assert store.get_meta(con, person)["licence"]["id"] == "MIT"  # a person's stays
    assert store.maintain(con, only=["licences"])["licences"]["read"] == 0

    hits = store.search(con, "spectral flux onset", 10, mode="fts")
    by_doc = {h["doc_id"]: h.get("licence") for h in hits}
    assert by_doc[printed] == "CC-BY-4.0" and by_doc[silent] is None
    kept = store.search(con, "spectral flux onset", 10, mode="fts", licence="open")
    assert {h["doc_id"] for h in kept} == {printed}
    kept = store.search(con, "spectral flux onset", 10, mode="fts", licence="cc")
    assert {h["doc_id"] for h in kept} == {printed, recorded}

    store.link(
        con, E("Open paper", "paper", "uses", "flux", "concept"), source_doc=printed
    )
    store.link(
        con, E("Silent paper", "paper", "uses", "flux", "concept"), source_doc=silent
    )
    scope = store.scope_documents(con, licence="open")
    walk = store.traverse_map(con, "flux", 1, within=scope)
    assert {e["source_doc"] for e in walk["edges"]} == {printed}


def test_the_bibliographic_records_name_their_licence() -> None:
    got = citations.crossref_licence(
        [
            {
                "URL": "https://www.elsevier.com/tdm/userlicense/1.0/",
                "content-version": "tdm",
            },
            {
                "URL": "http://creativecommons.org/licenses/by/4.0/",
                "content-version": "vor",
            },
        ]
    )
    assert got is not None and got.id == "CC-BY-4.0"
    assert citations.crossref_licence([{"URL": "x", "content-version": "tdm"}]) is None
    assert citations.openalex_licence("cc-by-nc-sa").id == "CC-BY-NC-SA"  # type: ignore[union-attr]
    assert citations.openalex_licence("publisher-specific-oa") is None


def test_a_person_sets_a_licence_and_takes_it_back(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    doc = store.ingest_text(con, "x " * 40, title="A paper")["doc_id"]
    store.set_licence(con, doc, "CC-BY-NC-4.0", by="crossref")
    # what a person writes is read as its id: a name, a short form, a URL
    assert L.given("CC BY 4.0").id == "CC-BY-4.0"
    assert L.given("MIT") == L.Licence("MIT", None)
    assert L.given("https://www.elsevier.com/tdm/userlicense/1.0/").id == L.PUBLISHER
    mine = store.set_licence(con, doc, "CC-BY-SA-4.0")
    assert mine["replaced"]["id"] == "CC-BY-NC-4.0"
    # a second word of the person's keeps the source's, not the first word
    mine = store.set_licence(con, doc, "MIT")
    assert mine["replaced"]["by"] == "crossref"
    held = store.clear_licence(con, doc)
    assert held is not None and held["id"] == "CC-BY-NC-4.0"
    assert store.clear_licence(con, doc) == held  # a source's is not a person's
    store.set_licence(con, doc, "MIT")
    assert store.clear_licence(con, doc)["by"] == "crossref"

    monkeypatch.setenv("PRAX_EMBED", "hash")
    from prax.api import app

    with TestClient(app) as client:
        con2 = client.app.state.con
        other = store.ingest_text(con2, "y " * 40, title="Another")["doc_id"]
        r = client.put(f"/doc/{other}/licence", json={"licence": "CC BY-SA 4.0"})
        assert r.status_code == 200 and r.json()["licence"]["id"] == "CC-BY-SA-4.0"
        assert store.get_meta(con2, other)["licence"]["by"] == "human"
        assert (
            client.put(f"/doc/{other}/licence", json={"licence": " "}).status_code
            == 400
        )
        assert (
            client.put("/doc/999999/licence", json={"licence": "MIT"}).status_code
            == 404
        )
        assert client.delete(f"/doc/{other}/licence").json() == {"licence": None}
        assert "licence" not in store.get_meta(con2, other)
