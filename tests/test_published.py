"""When a document was published: chosen from what says it, kept on the
document, shown on hits and passages, and a filter (``meta.published``;
the user, 2026-10-03: a person judges a source by when it was written)."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import store
from prax.answering import ask
from prax.capture import inbox

PAGE = b"""<html><head><title>Onsets</title>
<meta name="citation_publication_date" content="2018/04/02">
<meta property="article:published_time" content="2024-01-01T00:00:00Z">
</head><body><p>Spectral flux finds onsets in music.</p></body></html>"""


def test_the_most_trusted_source_that_says_it() -> None:
    page = {"citation": ("2018-04-02", "day"), "generic": ("2024-01-01", "day")}
    assert store.published_of({"date": "2017"}, page) == {
        "date": "2017",
        "precision": "year",
        "by": "record",
    }
    assert store.published_of({"paper": {"date": "March 2016"}}, page)["by"] == "paper"
    assert store.published_of({}, page)["date"] == "2018-04-02"  # citation
    assert store.published_of(
        {"arxiv": "2310.08560"}, {"generic": ("2024-01-01", "day")}
    ) == {
        "date": "2023-10",
        "precision": "month",
        "by": "arxiv",
    }
    assert store.published_of({}, {}) is None
    # a person's date is never replaced
    held = {
        "published": {"date": "2015", "precision": "year", "by": "human"},
        "date": "2017",
    }
    assert store.published_of(held) is None


def test_a_capture_says_it_and_the_pass_fills_the_rest(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    cap = inbox.ingest_bytes(
        con, PAGE, mime="text/html", source="capture", title="Onsets"
    )
    meta = store.get_meta(con, cap.doc_id)
    assert meta["published"]["date"] == "2018-04-02"
    assert meta["published"]["by"] == "citation"
    # an older document without one, a Zotero record's date, an arXiv id
    old = store.ingest_text(
        con, "an older note " * 10, title="old", meta={"date": "2011-05"}
    )
    arx = store.ingest_text(
        con, "a preprint " * 10, title="pre", meta={"arxiv": "1907.05242"}
    )
    bare = store.ingest_text(con, "nothing dated " * 10, title="bare")["doc_id"]
    report = store.maintain(con, only=["published"])["published"]
    assert report["dated"] == 2 and report["by"] == {"record": 1, "arxiv": 1}
    assert store.get_meta(con, old["doc_id"])["published"]["date"] == "2011-05"
    assert store.get_meta(con, arx["doc_id"])["published"]["date"] == "2019-07"
    assert "published" not in store.get_meta(con, bare)
    # a person's word, and what is not a date
    got = store.set_published(con, bare, "3 July 2020")
    assert got["date"] == "2020-07-03" and got["by"] == "human"
    with pytest.raises(ValueError, match="not a date"):
        store.set_published(con, bare, "soon")


def test_hits_and_passages_say_it_and_a_span_filters(
    con: sqlite3.Connection, client: TestClient
) -> None:
    def doc(title: str, date: str | None) -> int:
        meta: dict[str, Any] = {"date": date} if date else {}
        return int(
            store.ingest_text(
                con,
                f"spectral flux onset detection {title} " * 20,
                title=title,
                meta=meta,
            )["doc_id"]
        )

    early, late, undated = (
        doc("early", "2009"),
        doc("late", "2021-03"),
        doc("undated", None),
    )
    store.maintain(con, only=["published"])
    hits = store.search(con, "spectral flux onset", 10, mode="fts")
    by_doc = {h["doc_id"]: h["published"] for h in hits}
    assert (
        by_doc[early] == "2009"
        and by_doc[late] == "2021-03"
        and by_doc[undated] is None
    )
    kept = store.search(
        con, "spectral flux onset", 10, mode="fts", published_since="2015"
    )
    assert {h["doc_id"] for h in kept} == {late}  # the undated are left out
    older = store.search(
        con, "spectral flux onset", 10, mode="fts", published_before="2015"
    )
    assert {h["doc_id"] for h in older} == {early}
    with pytest.raises(ValueError, match="published_since"):
        store.search(con, "x", published_since="lately")
    # the door: the hit, the filter, documents
    got = client.get(
        "/search", params={"q": "spectral flux onset", "mode": "fts"}
    ).json()
    assert any(h.get("published") == "2021-03" for h in got)
    listed = client.get("/documents", params={"published_since": "2020"}).json()
    assert [r["id"] for r in listed["items"]] == [late]
    assert client.get("/documents", params={"published_since": "x"}).status_code == 400
    # a passage's label carries the year, for the answering model
    bundle = ask.gather(con, "spectral flux onset", limit=3)
    labels = {p.doc_id: p.label() for p in bundle.passages}
    assert "(2021)" in labels[late] and "(2009)" in labels[early]
