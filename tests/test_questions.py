"""Standing questions: a page that is asked again when the library learns
something, keeping a person's sections; and the day's briefing."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import ask, questions, schedule, store


def _library(con: sqlite3.Connection) -> tuple[int, int]:
    a = store.ingest_text(
        con,
        "Feedback delay networks build reverb from a delay matrix. "
        "The Householder matrix keeps the loop lossless. " * 20,
        title="FDN reverb design",
    )["doc_id"]
    b = store.ingest_text(
        con,
        "Convolution reverb multiplies the input spectrum with a measured "
        "impulse response of a room. " * 20,
        title="Convolution reverb",
    )["doc_id"]
    for name, typ in (("Householder FDN", "method"), ("reverberation", "concept")):
        store.link(
            con,
            store.Edge("FDN reverb design", "paper", "about", name, typ),
            source_doc=a,
            producer="test",
        )
    return a, b


def _ask(
    con: sqlite3.Connection,
    question: str = "how do feedback delay networks build reverb",
) -> dict:
    return ask.ask(con, question, answerer=ask.StubAnswerer())


def test_a_question_page_is_kept_with_what_it_was_built_from(
    con: sqlite3.Connection,
) -> None:
    a, b = _library(con)
    result = _ask(con)
    kept = questions.create(con, result, options={"steps": 0, "doctype": None})
    assert (
        kept["created"]
        and kept["slug"] == "q-how-do-feedback-delay-networks-build-reverb"
    )
    page = store.get_page(con, kept["slug"])
    assert page["kind"] == "question" and page["author"] == "agent"
    assert page["text"].startswith(
        "# how do feedback delay networks build reverb\n\nStub answer"
    )
    q = page["meta"]["question"]
    assert (
        q["question"] == "how do feedback delay networks build reverb"
        and q["model"] == "stub"
    )
    assert q["sources"] == [a] and q["options"] == {"steps": 0}
    assert set(q["top"]) >= {a, b} and q["since_id"] >= b
    assert q["source_hashes"][str(a)] and q["history"] == []
    # the page annotates its source
    assert (
        store.traverse(con, "how do feedback delay networks build reverb", hops=1)[0][
            "rel"
        ]
        == "annotates"
    )
    # nothing has changed: not due
    listed = questions.question_pages(con)
    assert [p["slug"] for p in listed] == [kept["slug"]]
    seen = questions.check(con, listed[0])
    assert seen == {"due": False, "new": [], "reread": [], "why": ""}
    with pytest.raises(ValueError, match="exists already"):
        questions.create(con, result)
    with pytest.raises(ValueError, match="no answer"):
        questions.create(con, {"question": "x", "answer": ""})


def test_a_new_document_makes_the_question_due_and_it_is_asked_again(
    con: sqlite3.Connection,
) -> None:
    a, _ = _library(con)
    kept = questions.create(con, _ask(con), options={"steps": 0})
    slug = kept["slug"]
    # a person adds a section under the answer
    store.append_page(con, slug, "My own thoughts on reverb.", heading="Notes")
    assert store.get_page(con, slug)["revision"] == 2
    # a paper on the subject arrives: it ranks in the search now
    c = store.ingest_text(
        con,
        "Reverb built from feedback delay networks with a Householder delay matrix,"
        " lossless and dense. " * 20,
        title="A newer FDN reverb",
    )["doc_id"]
    page = questions.question_pages(con)[0]
    seen = questions.check(con, page)
    assert seen["due"] and [n["doc_id"] for n in seen["new"]] == [c]
    assert seen["new"][0]["why"] == "ranks in the search now"
    assert seen["why"] == "1 new document"
    rep = questions.refresh(con, page, answerer=ask.StubAnswerer())
    assert rep["refreshed"] and rep["revision"] == 3
    assert [n["title"] for n in rep["new"]] == ["A newer FDN reverb"]
    after = store.get_page(con, slug)
    assert after["author"] == "agent"
    assert after["revisions"][-1]["note"] == "ask: stub; new: A newer FDN reverb"
    # the answer is new, the person's section is where it was
    assert after["text"].startswith(
        "# how do feedback delay networks build reverb\n\nStub answer"
    )
    assert after["text"].rstrip().endswith("## Notes\n\nMy own thoughts on reverb.")
    q = after["meta"]["question"]
    assert q["revision"] == 3 and len(q["history"]) == 1
    assert q["history"][0]["new"] == [{"doc_id": c, "title": "A newer FDN reverb"}]
    assert c in q["top"] and q["since_id"] >= c
    # settled again; a forced re-ask says so in its note
    page = questions.question_pages(con)[0]
    assert not questions.check(con, page)["due"]
    assert questions.refresh(con, page, answerer=ask.StubAnswerer()) == {
        "slug": slug,
        "refreshed": False,
        "why": "nothing new",
    }
    forced = questions.refresh(con, page, force=True, answerer=ask.StubAnswerer())
    assert forced["refreshed"] and forced["revision"] == 4
    assert (
        store.get_page(con, slug)["revisions"][-1]["note"] == "ask: stub; asked again"
    )
    assert a  # the first source is still one


def test_a_re_ask_shows_the_model_the_earlier_answer_and_what_is_new(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _library(con)
    questions.create(con, _ask(con), options={"steps": 0})
    store.append_page(
        con, questions.question_pages(con)[0]["slug"], "Mine.", heading="Notes"
    )
    store.ingest_text(
        con,
        "Reverb built from feedback delay networks with a Householder delay matrix. "
        * 20,
        title="A newer FDN reverb",
    )
    asked: dict = {}
    real = ask.ask

    def spy(con_: sqlite3.Connection, question: str, **kw: object) -> dict:
        asked.update(question=question, history=kw.get("history"), note=kw.get("note"))
        return real(con_, question, **kw)

    monkeypatch.setattr(ask, "ask", spy)
    page = questions.question_pages(con)[0]
    rep = questions.refresh(con, page, answerer=ask.StubAnswerer())
    assert rep["refreshed"]
    # the question itself is what is searched for; what is new, and the
    # kind of change asked for, is a note beside it for the model alone
    assert asked["question"] == "how do feedback delay networks build reverb"
    assert asked["note"].startswith(
        "the library now also holds A newer FDN reverb. Your earlier answer is"
        " above. Where the new evidence changes it, replace;"
    )
    assert asked["history"] == [
        {
            "question": "how do feedback delay networks build reverb",
            "answer": (
                "Stub answer to 'how do feedback delay networks build reverb' [1]."
            ),
        }
    ]
    # the page keeps the question as asked, and the person's section
    text = store.get_page(con, page["slug"])["text"]
    assert text.startswith("# how do feedback delay networks build reverb\n\n")
    assert text.rstrip().endswith("## Notes\n\nMine.")


def test_a_source_read_again_and_shared_entities_make_it_due(
    con: sqlite3.Connection,
) -> None:
    a, _ = _library(con)
    questions.create(con, _ask(con), options={"steps": 0})
    page = questions.question_pages(con)[0]
    # a document that shares two of the answer's entities, off the search's top
    d = store.ingest_text(con, "Unrelated words about tabletops. " * 20, title="T")[
        "doc_id"
    ]
    for name, typ in (("Householder FDN", "method"), ("reverberation", "concept")):
        store.link(
            con,
            store.Edge("T", "paper", "about", name, typ),
            source_doc=d,
            producer="t",
        )
    seen = questions.check(con, page)
    assert seen["due"] and seen["new"][0]["doc_id"] == d
    assert seen["new"][0]["why"] == "shares 2 of the answer's entities"
    questions.refresh(con, page, answerer=ask.StubAnswerer())
    # the source's text read again
    page = questions.question_pages(con)[0]
    assert not questions.check(con, page)["due"]
    store.index_text(con, a, "FDN reverb design, re-read: " + "delay matrix. " * 40)
    seen = questions.check(con, page)
    assert (
        seen["due"] and seen["reread"] == [a] and seen["why"] == "1 source read again"
    )
    rep = questions.refresh(con, page, answerer=ask.StubAnswerer())
    assert rep["refreshed"]
    assert store.get_page(con, page["slug"])["revisions"][-1]["note"] == (
        "ask: stub; a source was read again"
    )


def test_refresh_all_and_the_briefing(con: sqlite3.Connection) -> None:
    a, b = _library(con)
    questions.create(con, _ask(con), options={"steps": 0})
    questions.create(con, _ask(con, "what is convolution reverb"), options={"steps": 0})
    rep = questions.refresh_all(con, answerer=ask.StubAnswerer())
    assert rep["checked"] == 2 and rep["refreshed"] == 0
    c = store.ingest_text(
        con,
        "Reverb built from feedback delay networks with a Householder delay matrix. "
        * 20,
        title="A newer FDN reverb",
        meta={"summary": "A denser FDN. It keeps the loop lossless."},
    )["doc_id"]
    rep = questions.refresh_all(con, answerer=ask.StubAnswerer())
    assert (
        rep["refreshed"] >= 1
        and rep["pages"]["q-how-do-feedback-delay-networks-build-reverb"]["refreshed"]
    )
    # one question by slug, forced
    rep = questions.refresh_all(
        con,
        only="q-what-is-convolution-reverb",
        force=True,
        answerer=ask.StubAnswerer(),
    )
    assert rep["checked"] == 1 and rep["refreshed"] == 1
    # the briefing: the documents that came, the questions that moved
    b1 = questions.briefing(con, day="2026-09-21")
    assert b1["created"] and b1["slug"] == "briefing-2026-09-21"
    text = store.get_page(con, "briefing-2026-09-21")["text"]
    assert text.startswith("# What arrived, 2026-09-21\n\n")
    assert f"[A newer FDN reverb](#doc/{c})" in text and ": A denser FDN." in text
    assert f"[FDN reverb design](#doc/{a})" in text and f"#doc/{b}" in text
    assert "## Questions that moved" in text
    assert (
        "[how do feedback delay networks build reverb](#doc/" in text
        and "new: A newer FDN reverb" in text
    )
    assert "[what is convolution reverb](#doc/" in text
    meta = store.get_page(con, "briefing-2026-09-21")["meta"]["briefing"]
    # every question moved once for the newcomer (three documents are all in
    # a search's top twenty), the second once more when forced
    assert meta["documents"] == 3 and meta["moved"] == 3
    # the next briefing starts where this one ended: nothing new
    b2 = questions.briefing(con, day="2026-09-22")
    assert b2["documents"] == 0
    assert "Nothing arrived since" in store.get_page(con, "briefing-2026-09-22")["text"]


def test_the_schedule_knows_the_questions_entry() -> None:
    entries = schedule.entries({"questions": "06:30", "maintain": "03:30"})
    assert [(e.name, e.at.hour, e.at.minute) for e in entries] == [
        ("maintain", 3, 30),
        ("questions", 6, 30),
    ]


@pytest.fixture()
def client(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PRAX_ASK", "stub")
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_the_door_keeps_lists_and_runs_the_questions(client: TestClient) -> None:
    con = store.connect()
    _library(con)
    result = client.post(
        "/ask",
        json={"question": "how do feedback delay networks build reverb", "steps": 0},
    ).json()
    kept = client.post(
        "/questions", json={"result": result, "options": {"steps": 0}}
    ).json()
    assert kept["slug"] == "q-how-do-feedback-delay-networks-build-reverb"
    assert client.post("/questions", json={"result": result}).status_code == 400
    listed = client.get("/questions").json()
    assert (
        len(listed) == 1 and listed[0]["due"] is False and listed[0]["model"] == "stub"
    )
    store.ingest_text(
        con,
        "Reverb built from feedback delay networks with a Householder delay matrix. "
        * 20,
        title="A newer FDN reverb",
    )
    listed = client.get("/questions").json()
    assert listed[0]["due"] and listed[0]["new"][0]["title"] == "A newer FDN reverb"
    job = client.post("/questions/run", json={"briefing": True}).json()["job"]
    for _ in range(200):
        row = client.get(f"/jobs/{job}").json()
        if row["status"] != "running":
            break
        time.sleep(0.1)
    assert row["status"] == "done" and row["note"].startswith("1 of 1 asked again")
    assert "briefing: 3 documents" in row["note"]  # the whole library came today
    assert (
        store.get_page(con, "q-how-do-feedback-delay-networks-build-reverb")["revision"]
        == 2
    )
    assert (
        client.get("/pages", params={"kind": "briefing"})
        .json()[0]["slug"]
        .startswith("briefing-")
    )
