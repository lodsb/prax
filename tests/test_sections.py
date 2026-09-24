"""What a long document's parts are about.

A book's summary is two or three sentences for five million characters;
the document field therefore says nothing about the middle of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import sections, store, work, worker


class Runtime:
    name = "stub"

    def __init__(self, answer: str = "") -> None:
        self.answer = answer
        self.asked: list[str] = []

    def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, Any]]:
        self.asked.append(user)
        return self.answer, {"input_tokens": 100, "output_tokens": 30}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    work._leases.clear()
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _book(client: TestClient, chapters: int = 3, size: int = 5000) -> int:
    body = []
    for i in range(chapters):
        body.append(
            f"# Chapter {i + 1}\n\n" + f"Some prose about topic {i}. " * (size // 25)
        )
    return client.post(
        "/ingest", json={"text": "\n\n".join(body), "title": "A Long Book"}
    ).json()["doc_id"]


# --------------------------------------------------------------- the answer


def test_a_section_is_read_into_a_sentence() -> None:
    runtime = Runtime("Delay lines and the algorithms built on them.")
    got = sections.summarize(runtime, "Chapter 2", "text " * 500, title="A Long Book")
    assert got is not None
    assert got.summary.startswith("Delay lines")
    assert got.chars == len("text " * 500)
    assert "Chapter 2" in runtime.asked[0]
    assert "A Long Book" in runtime.asked[0]


def test_the_opener_comes_off_rather_than_the_summary_being_thrown_away() -> None:
    """The model says "This section explores…" whatever the prompt asks,
    and the words after it are the answer. The first guard refused every
    real chapter of the Princeton Companion for its first two words."""
    runtime = Runtime(
        "This section explores the mathematical modeling of adaptation,"
        " drawing analogies between evolution and machine learning."
    )
    got = sections.summarize(runtime, "Part V", "text " * 500)
    assert got is not None
    assert got.summary.startswith("Explores the mathematical modeling")


def test_a_summary_about_itself_is_refused() -> None:
    runtime = Runtime("This section describes the contents of this section.")
    assert sections.summarize(runtime, "Chapter 2", "text " * 500) is None
    assert (
        sections.summarize(Runtime("This chapter is a chapter."), "C", "t" * 99) is None
    )


def test_the_heading_handed_back_is_refused() -> None:
    name = "Asymptotic analysis and perturbation theory"
    assert sections.acceptable(name, name) == "the heading again"
    # and a heading that is itself a structure word is refused too
    assert sections.acceptable("Chapter 2", "Chapter 2")


def test_a_summary_is_capped() -> None:
    runtime = Runtime("Delay lines. " * 200)
    got = sections.summarize(runtime, "Chapter 2", "text " * 500)
    assert got is not None and len(got.summary) <= sections.MAX_SUMMARY


# ------------------------------------------------------- what the store does


def test_only_sections_worth_a_sentence(client: TestClient) -> None:
    con = client.app.state.con
    doc = _book(client, chapters=3, size=5000)
    parts = store.document_sections(con, doc)
    assert [p["heading"] for p in parts] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    # a short section is not a chapter
    assert store.document_sections(con, doc, min_chars=10**6) == []


def test_a_short_document_is_never_offered(client: TestClient) -> None:
    con = client.app.state.con
    client.post("/ingest", json={"text": "# A\n\nshort. " * 20, "title": "A Note"})
    assert store.sections_needed(con) == []


def test_a_long_document_is_offered_until_it_is_read(client: TestClient) -> None:
    con = client.app.state.con
    doc = _book(client, chapters=4, size=20000)
    assert doc in store.sections_needed(con)
    store.set_sections(
        con,
        doc,
        [{"heading": "Chapter 1", "chars": 20000, "summary": "x" * 60}],
        source="test",
    )
    assert store.sections_needed(con) == []


def test_the_summaries_reach_the_document_field(client: TestClient) -> None:
    con = client.app.state.con
    doc = _book(client, chapters=2, size=20000)
    store.set_sections(
        con,
        doc,
        [
            {
                "heading": "Chapter 1",
                "chars": 20000,
                "summary": "Delay lines and reverb.",
            }
        ],
        source="test",
    )
    field = store.document_field(con, doc)
    assert "Delay lines and reverb." in field
    assert "Chapter 1" in field


def test_the_field_is_capped_for_a_book_of_many_chapters(client: TestClient) -> None:
    """Ninety chapters would put thirty thousand characters in one FTS
    row, where BM25's length normalization buries it."""
    con = client.app.state.con
    doc = _book(client, chapters=2, size=20000)
    store.set_sections(
        con,
        doc,
        [
            {"heading": f"Chapter {i}", "chars": 9000, "summary": "y" * 300}
            for i in range(90)
        ],
        source="test",
    )
    field = store.document_field(con, doc)
    assert len(field) < store.SECTION_FIELD_CHARS + 2000


# ------------------------------------------------------- the step, end to end


def test_the_step_reads_a_book_and_the_door_keeps_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    monkeypatch.setenv("PRAX_SECTIONS", "stub")
    doc = _book(client, chapters=3, size=25000)

    batch = client.get("/work/sections", params={"scope": "all"}).json()
    assert [i["doc_id"] for i in batch["items"]] == [doc]
    assert len(batch["items"][0]["sections"]) == 3

    results = worker.do_sections(
        batch["items"], Runtime("Delay lines and the algorithms built on them.")
    )
    rep = client.post("/work/sections", json={"results": results}).json()
    assert rep["applied"] == 1

    held = store.get_meta(con, doc)["sections"]
    assert len(held["items"]) == 3
    assert held["items"][0]["summary"].startswith("Delay lines")
    work._leases.clear()
    assert client.get("/work/sections", params={"scope": "all"}).json()["items"] == []


def test_a_document_the_model_could_not_read_is_not_asked_again(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty answer is an answer: recorded against the text it was read
    from, so the document comes back only when that text changes."""
    con = client.app.state.con
    monkeypatch.setenv("PRAX_SECTIONS", "stub")
    doc = _book(client, chapters=2, size=35000)
    batch = client.get("/work/sections", params={"scope": "all"}).json()
    results = worker.do_sections(batch["items"], Runtime("no"))  # too short to keep
    client.post("/work/sections", json={"results": results})
    assert store.get_meta(con, doc)["sections"]["items"] == []
    work._leases.clear()
    assert client.get("/work/sections", params={"scope": "all"}).json()["items"] == []


def test_a_heading_loses_the_extractor_s_markup() -> None:
    """A PDF extractor leaves its own markup in a heading, and the
    document field would carry it into the index."""
    assert sections.clean_heading("<mark>12</mark> KNOWLEDGE") == "12 KNOWLEDGE"
    assert sections.clean_heading("24<sup>PERCEPTION</sup>") == "24 PERCEPTION"
    assert sections.clean_heading("**Part V** Modeling") == "Part V Modeling"
