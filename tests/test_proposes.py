"""The polysemy that is not a duplicate: a paper and the thing named
after it are two things, and the edge between them is what was missing."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import store, work


@pytest.fixture()
def client() -> Iterator[TestClient]:
    work._leases.clear()
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _paper(
    client: TestClient, title: str, *, authors: bool = True, kind: str = "paper"
) -> int:
    """A document, and — unless told otherwise — the authorship edge that
    says it is a work with contributors. ``kind`` is the type its own
    entity carries, so a test about pages does not quietly make a paper."""
    doc_id = client.post(
        "/ingest", json={"text": f"{title} is described here. " * 30, "title": title}
    ).json()["doc_id"]
    if authors:
        store.link(
            client.app.state.con,
            store.Edge(title, kind, "authored_by", "Ann Author", "author"),
            source_doc=doc_id,
            producer="test",
        )
    return doc_id


def _entity(con, name: str, etype: str, doc_id: int) -> int:
    """An entity of that type, made by an edge the ontology accepts."""
    edge = (
        store.Edge(name, etype, "about", "something", "concept")
        if etype in ("paper", "page")
        else store.Edge("A Source", "paper", "about", name, etype)
    )
    store.link(con, edge, source_doc=doc_id, producer="test")
    return con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
    ).fetchone()["id"]


def test_a_paper_proposes_the_method_named_after_it(client: TestClient) -> None:
    con = client.app.state.con
    title = "Fractional Wavelet Transform"
    doc = _paper(client, title)
    _entity(con, title, "paper", doc)
    _entity(con, title, "method", doc)

    got = store.maintain(con, only=["proposes"])["proposes"]
    assert got["linked"] == 1
    edges = store.traverse(con, title, hops=1)
    assert any(
        e["rel"] == "proposes"
        and e["src_type"] == "paper"
        and e["dst_type"] == "method"
        for e in edges
    )
    # and neither entity was merged into the other: they are two things
    rows = con.execute(
        "SELECT canonical_id FROM entities WHERE name = ?", (title,)
    ).fetchall()
    assert all(r["canonical_id"] is None for r in rows)


def test_nothing_without_a_document_of_that_title(client: TestClient) -> None:
    """A title the library does not hold is the extractor's guess, not
    evidence: 3,006 clashes have a document side and 523 a document."""
    con = client.app.state.con
    doc = _paper(client, "Something Else")
    _entity(con, "An Unheld Title", "paper", doc)
    _entity(con, "An Unheld Title", "method", doc)
    assert store.maintain(con, only=["proposes"])["proposes"]["linked"] == 0


def test_a_page_of_your_own_proposes_nothing(client: TestClient) -> None:
    """ "Ableton Live 7 Reference Manual" does not propose Ableton Live."""
    con = client.app.state.con
    title = "Ableton Live 7 Reference Manual"
    doc = _paper(client, title, kind="page")
    _entity(con, title, "page", doc)
    _entity(con, title, "tool", doc)
    assert store.maintain(con, only=["proposes"])["proposes"]["linked"] == 0


def test_the_pass_is_idempotent(client: TestClient) -> None:
    con = client.app.state.con
    title = "Spectral Granular Synthesis"
    doc = _paper(client, title)
    _entity(con, title, "paper", doc)
    _entity(con, title, "method", doc)
    first = store.maintain(con, only=["proposes"])["proposes"]
    again = store.maintain(con, only=["proposes"])["proposes"]
    assert first["linked"] == 1
    assert again["linked"] == 0 and again["existing"] == 1


def test_an_author_of_the_same_name_is_left_alone(client: TestClient) -> None:
    """A name under a document type and an author is a different
    question — the relation has no room for it, and it stays open."""
    con = client.app.state.con
    title = "Niklas Klügel"
    doc = _paper(client, title)
    _entity(con, title, "paper", doc)
    _entity(con, title, "author", doc)
    assert store.maintain(con, only=["proposes"])["proposes"]["linked"] == 0


def test_a_document_with_no_authors_proposes_nothing(client: TestClient) -> None:
    """ "Expertise" and "Computer Graphics" are lecture handouts about the
    thing, not papers proposing it. Authorship is the evidence that this
    is a work with contributors; counting words is a proxy for nothing."""
    con = client.app.state.con
    title = "Expertise"
    doc = _paper(client, title, authors=False)
    _entity(con, title, "paper", doc)
    _entity(con, title, "concept", doc)
    assert store.maintain(con, only=["proposes"])["proposes"]["linked"] == 0
