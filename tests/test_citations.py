"""Citation importer: DOI and title normalization, both sources through
fake fetchers, edges from references, library references named by their
document, stamps, idempotence, exact title resolution."""

from __future__ import annotations

import sqlite3
from typing import Any, ClassVar
from urllib.parse import unquote

import pytest

from prax import store
from prax.importers import citations

W = "https://openalex.org/W"


def test_normalization() -> None:
    n = citations.normalize_doi
    assert (
        n("https://doi.org/10.1109/ICASSP.2018.8461434")
        == "10.1109/icassp.2018.8461434"
    )
    assert n("doi:10.1000/ABC.") == "10.1000/abc"
    assert n("no doi here") is None and n(None) is None and n("10.1/x") is None
    t = citations.normalize_title
    assert t("Tree-Structured Gaussian Process Approximations!") == t(
        "tree structured gaussian process approximations"
    )
    with pytest.raises(ValueError):
        citations.source_named("scholar", lambda url: {})


class FakeOpenAlex:
    """Answers the request shapes OpenAlexSource makes."""

    works: ClassVar[dict[str, dict[str, Any]]] = {
        W + "1": {
            "id": W + "1",
            "title": "Paper One",
            "doi": "https://doi.org/10.1000/one",
            "publication_year": 2018,
            "cited_by_count": 42,
            "referenced_works": [W + "2", W + "3", W + "9"],
        },
        W + "2": {
            "id": W + "2",
            "title": "Paper Two",
            "doi": "https://doi.org/10.1000/two",
            "publication_year": 2015,
            "cited_by_count": 7,
            "referenced_works": [],
        },
        W + "3": {
            "id": W + "3",
            "title": "External Work",
            "doi": None,
            "publication_year": 2010,
            "cited_by_count": 3,
            "referenced_works": [],
        },
        W + "4": {
            "id": W + "4",
            "title": "Titled Only",
            "doi": None,
            "publication_year": 2020,
            "cited_by_count": 1,
            "referenced_works": [W + "1"],
        },
    }

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        url = unquote(url)
        if "/works/doi:" in url:
            doi = url.split("/works/doi:")[1].split("?")[0]
            for w in self.works.values():
                if w["doi"] and w["doi"].lower().endswith(doi):
                    return w
            return {}
        if "filter=openalex_id:" in url:
            ids = url.split("openalex_id:")[1].split("&")[0].split("|")
            return {
                "results": [
                    self.works[W + i[1:]] for i in ids if W + i[1:] in self.works
                ]
            }
        if "search=" in url:
            q = url.split("search=")[1].split("&")[0]
            return {
                "results": [
                    w for w in self.works.values() if w["title"].lower() == q.lower()
                ]
            }
        raise AssertionError(url)


class FakeCrossref:
    """Answers the request shapes CrossrefSource makes: the same little
    world as the OpenAlex fake, with one untitled reference carrying a DOI."""

    items: ClassVar[dict[str, dict[str, Any]]] = {
        "10.1000/one": {
            "DOI": "10.1000/one",
            "title": ["Paper One"],
            "is-referenced-by-count": 40,
            "published-print": {"date-parts": [[2018, 4]]},
            "reference": [
                {"key": "r1", "DOI": "10.1000/TWO"},  # library document, no title given
                {"key": "r2", "article-title": "External Work", "year": "2010"},
                {"key": "r3", "DOI": "10.1000/ext2"},  # title fetched on demand
                {"key": "r4", "unstructured": "Smith 2001"},  # nothing usable
            ],
        },
        "10.1000/two": {"DOI": "10.1000/two", "title": ["Paper Two"], "reference": []},
        "10.1000/ext2": {"DOI": "10.1000/ext2", "title": ["Second External"]},
    }

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        url = unquote(url)
        if "/works?query.title=" in url:
            q = url.split("query.title=")[1].split("&")[0].lower()
            hits = [m for m in self.items.values() if m["title"][0].lower() == q]
            return {"message": {"items": hits}}
        doi = url.split("/works/")[1]
        m = self.items.get(doi)
        return {"message": m} if m else {}


@pytest.fixture(params=["openalex", "crossref"])
def world(request: pytest.FixtureRequest) -> tuple[citations.Source, Any]:
    fake = FakeOpenAlex() if request.param == "openalex" else FakeCrossref()
    return citations.source_named(request.param, fake), fake


def test_import_links_references_and_stamps(
    con: sqlite3.Connection, world: tuple[citations.Source, Any]
) -> None:
    source, fake = world
    one = store.ingest_text(con, "one", title="Paper One", meta={"doi": "10.1000/one"})[
        "doc_id"
    ]
    two = store.ingest_text(con, "two", title="Paper Two", meta={"doi": "10.1000/TWO"})[
        "doc_id"
    ]
    four = store.ingest_text(con, "four", title="Titled Only")["doc_id"]
    rep = citations.import_citations(con, [one, two, four], source=source)
    # one: a reference that is a library document (named by its title) and
    # external ones; references with nothing usable are skipped
    assert (rep.documents, rep.unresolved, rep.library_refs) == (2, 1, 1)
    edges = store.traverse(con, "Paper One", hops=1)
    cited = {
        (e["dst"], e["confidence"], e["source_doc"])
        for e in edges
        if e["rel"] == "cites"
    }
    expected = {("Paper Two", "EXTRACTED", one), ("External Work", "EXTRACTED", one)}
    if source.name == "crossref":
        expected.add(("Second External", "EXTRACTED", one))  # title fetched by DOI
    assert cited == expected and rep.linked == len(expected)
    # unusable references (no title, no DOI; an id the source cannot resolve)
    # are not counted
    assert store.get_meta(con, one)["citations"]["references"] == len(expected)
    assert all(e["evidence"].startswith(f"{source.name} ") for e in edges)
    meta = store.get_meta(con, one)["citations"]
    assert meta["resolved"] and meta["source"] == source.name and meta["year"] == 2018
    assert meta["cited_by_count"] in (42, 40) and meta["linked"] == len(expected)
    assert store.get_meta(con, four)["citations"]["resolved"] is False
    # idempotent: fetched documents are skipped, nothing linked twice
    calls = len(fake.calls)
    rep2 = citations.import_citations(con, [one, two, four], source=source)
    assert rep2.skipped == 3 and rep2.linked == 0 and len(fake.calls) == calls
    assert citations.candidates(con) == []
    assert citations.candidates(con, refresh=True) == [one, two, four]  # DOIs first
    assert citations.candidates(con, refresh=True, doi_only=True) == [one, two]


def test_openalex_title_resolution_is_exact(con: sqlite3.Connection) -> None:
    four = store.ingest_text(con, "four", title="Titled Only")["doc_id"]
    near = store.ingest_text(con, "near", title="Titled Only Revisited")["doc_id"]
    source = citations.source_named("openalex", FakeOpenAlex())
    rep = citations.import_citations(
        con, [four, near], source=source, resolve_titles=True
    )
    assert (rep.documents, rep.unresolved) == (1, 1)
    assert store.get_meta(con, four)["citations"]["id"] == W + "4"
    assert [e["dst"] for e in store.traverse(con, "Titled Only", hops=1)] == [
        "Paper One"
    ]


def test_crossref_title_resolution(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "t", title="Paper Two")["doc_id"]
    source = citations.source_named("crossref", FakeCrossref())
    rep = citations.import_citations(con, [doc], source=source, resolve_titles=True)
    assert rep.documents == 1
    assert store.get_meta(con, doc)["citations"]["doi"] == "10.1000/two"
