"""Ask blocks: a standing question inside a person's own page — the
grammar, the fill that touches nothing outside the markers, the hand
guard, the chunk it becomes, the pass that answers it and the door."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import ask, blocks, chunking, questions, store

PAGE = """# FDN notes

My own prose about delay networks, kept as it is.

<!-- prax:ask id=q1 steps=0 "how do feedback delay networks build reverb" -->
<!-- /prax:ask id=q1 -->

Between the blocks: [Convolution reverb](#doc/2) is the other way.

## Later

<!-- prax:ask id=q2 doctype=text "what does convolution reverb multiply" -->

an interior written by hand, never by the door

<!-- /prax:ask id=q2 sha=000000000000 asked=2026-09-20 run=x -->
"""


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
    return a, b


# ---------------------------------------------------------------- grammar


def test_blocks_are_read_with_their_question_options_and_state() -> None:
    q1, q2 = blocks.blocks(PAGE)
    assert (
        q1.id == "q1" and q1.question == "how do feedback delay networks build reverb"
    )
    assert q1.options == {"steps": 0} and not q1.filled
    assert (
        PAGE[q1.interior[0] : q1.interior[1]] == ""
    )  # unfilled: empty, after the head
    assert q2.id == "q2" and q2.options == {"doctype": "text"}
    assert q2.filled and q2.sha == "000000000000" and q2.asked == "2026-09-20"
    assert PAGE[q2.interior[0] : q2.interior[1]].strip() == (
        "an interior written by hand, never by the door"
    )
    assert not blocks.held(q1, PAGE) and blocks.held(q2, PAGE)
    # a head without an id or a question is not a block; a stray tail is nothing
    assert blocks.blocks('<!-- prax:ask "no id" -->\n<!-- prax:ask id=x -->\n') == []
    assert blocks.blocks("<!-- /prax:ask id=q9 -->\n") == []
    assert blocks.blocks("") == []


def test_fill_replaces_interiors_by_id_and_nothing_else() -> None:
    text, report = blocks.fill(
        PAGE,
        {"q1": "An answer [1].\n\nSources:\n\n- [1] [FDN reverb design](#doc/1)"},
        asked="2026-09-21",
        run="stub",
    )
    assert report == {"q1": "filled"}
    q1 = blocks.blocks(text)[0]
    interior = text[q1.interior[0] : q1.interior[1]]
    assert interior.startswith("\nAn answer [1].") and interior.endswith("#doc/1)\n\n")
    assert q1.tail_attrs == {
        "id": "q1",
        "sha": blocks.sha_of(interior),
        "asked": "2026-09-21",
        "run": "stub",
    }
    assert not blocks.held(q1, text)
    # everything outside the block is byte for byte what it was
    head_end = PAGE.index("<!-- /prax:ask id=q1 -->\n")
    assert text.startswith(PAGE[:head_end])
    assert text.endswith(PAGE[head_end + len("<!-- /prax:ask id=q1 -->\n") :])
    # a second fill replaces the interior again; the hand-edited block is held
    again, report = blocks.fill(
        text, {"q1": "Another.", "q2": "Door's."}, asked="2026-09-22", run="stub"
    )
    assert report == {"q1": "filled", "q2": "held"}
    assert "Another." in again and "An answer [1]." not in again
    assert "an interior written by hand" in again
    # released, the held block is replaced too; a block without markers is missing
    released, report = blocks.fill(
        again, {"q2": "Door's.", "q7": "x"}, asked="2026-09-22", run="s", release={"q2"}
    )
    assert report == {"q2": "filled", "q7": "missing"}
    assert "an interior written by hand" not in released and "Door's." in released


def test_a_newline_is_no_hand_but_a_word_is_and_keep_regions_are_the_persons() -> None:
    text, _ = blocks.fill(PAGE, {"q1": "The answer."}, asked="2026-09-21", run="s")
    q1 = blocks.blocks(text)[0]
    padded = text[: q1.interior[1]] + "\n\n" + text[q1.interior[1] :]
    assert not blocks.held(blocks.blocks(padded)[0], padded)
    worded = text.replace("The answer.", "The answer, I think.")
    assert blocks.held(blocks.blocks(worded)[0], worded)
    # a keep region added by hand inside the block does not hold it, and
    # survives the next fill, after the new answer
    kept = text.replace(
        "The answer.",
        "The answer.\n\n<!-- prax:keep -->\nMy remark.\n<!-- /prax:keep -->",
    )
    b = blocks.blocks(kept)[0]
    assert not blocks.held(b, kept)
    assert blocks.answer_of(kept[b.interior[0] : b.interior[1]]) == "The answer."
    refilled, report = blocks.fill(kept, {"q1": "A newer answer."}, asked="d", run="s")
    assert report == {"q1": "filled"}
    b = blocks.blocks(refilled)[0]
    interior = refilled[b.interior[0] : b.interior[1]]
    assert interior.index("A newer answer.") < interior.index("My remark.")
    assert "<!-- prax:keep -->" in interior and not blocks.held(b, refilled)
    assert blocks.head_line("q3", 'why "this"', steps=2) == (
        "<!-- prax:ask id=q3 steps=2 \"why 'this'\" -->\n<!-- /prax:ask id=q3 -->\n"
    )


# ------------------------------------------------------------------ chunks


def test_a_block_is_one_ask_chunk_set_aside(con: sqlite3.Connection) -> None:
    text, _ = blocks.fill(
        PAGE,
        {"q1": "An answer [1].\n\nSources:\n\n- [1] [T](#doc/1)"},
        asked="d",
        run="s",
    )
    chunks = chunking.chunk(text)
    kinds = [c.kind for c in chunks]
    assert kinds == ["text", "ask", "text", "ask"]
    q1, q2 = (c for c in chunks if c.kind == "ask")
    assert q1.text.startswith("<!-- prax:ask id=q1") and q1.text.endswith("-->")
    assert "An answer [1]." in q1.text and q1.heading == ["FDN notes"]
    assert q1.data == {
        "id": "q1",
        "question": "how do feedback delay networks build reverb",
        "filled": True,
        "held": False,
        "options": {"steps": 0},
        "asked": "d",
        "run": "s",
        "sha": blocks.blocks(text)[0].sha,
    }
    assert q2.data["held"] is True and q2.heading == ["FDN notes", "Later"]
    # the prose between the blocks is its own chunk, untouched
    assert chunks[2].text.startswith("Between the blocks")
    # in the store: out of a search, never handed out to embed
    _library(con)
    store.write_page(con, "fdn-notes", text, kind="topic")
    hits = store.search(con, "feedback delay networks reverb", 10, mode="fts")
    assert all(h["kind"] != "ask" for h in hits)
    assert not any(
        r["kind"] == "ask" for r in store.pending_embeddings(con, "any", limit=100)
    )
    asked = store.search(con, "feedback delay networks", 10, mode="fts", kind="ask")
    assert asked and asked[0]["kind"] == "ask"


# ------------------------------------------------------------------- store


def _annotated(con: sqlite3.Connection, doc_id: int) -> set[str]:
    return {
        r[0]
        for r in con.execute(
            "SELECT t.name FROM edges e JOIN entities t ON t.id = e.dst"
            " WHERE e.source_doc = ? AND e.rel = 'annotates' AND e.valid_to IS NULL",
            (doc_id,),
        )
    }


def test_the_page_reports_its_blocks_and_links_make_edges(
    con: sqlite3.Connection,
) -> None:
    a, _ = _library(con)
    store.write_page(con, "fdn-notes", PAGE, kind="topic")
    page = store.get_page(con, "fdn-notes")
    assert [x["id"] for x in page["blocks"]] == ["q1", "q2"]
    assert page["blocks"][0]["filled"] is False and page["blocks"][1]["held"] is True
    assert page["blocks"][0]["asked_at"] is None and page["blocks"][0]["sources"] == []
    # the link in the prose is an edge; it goes when the link goes
    assert store.linked_documents(PAGE) == [2]
    assert _annotated(con, page["doc_id"]) == {"Convolution reverb"}
    without = PAGE.replace("[Convolution reverb](#doc/2)", "convolution")
    store.write_page(con, "fdn-notes", without, kind="topic")
    assert _annotated(con, page["doc_id"]) == set()
    # the edge is history, not gone
    assert (
        con.execute(
            "SELECT count(*) FROM edges WHERE source_doc = ? AND valid_to IS NOT NULL",
            (page["doc_id"],),
        ).fetchone()[0]
        == 1
    )
    # an edge given as annotates is never retired by an edit
    store.write_page(con, "note", "# A note\n\n", kind="addendum", annotates=[a])
    store.write_page(con, "note", "# A note\n\nEdited.\n", kind="addendum")
    assert _annotated(con, store.get_page(con, "note")["doc_id"]) == {
        "FDN reverb design"
    }


def test_fill_blocks_is_an_agent_revision_over_a_persons_page(
    con: sqlite3.Connection,
) -> None:
    a, _ = _library(con)
    store.write_page(con, "fdn-notes", PAGE, kind="topic")
    written = store.fill_blocks(
        con,
        "fdn-notes",
        {"q1": f"An answer [1].\n\nSources:\n\n- [1] [FDN reverb design](#doc/{a})"},
        asked="2026-09-21",
        run="stub",
        note="ask stub; q1: not yet asked",
    )
    assert written["revision"] == 2 and written["report"] == {"q1": "filled"}
    page = store.get_page(con, "fdn-notes")
    assert page["author"] == "agent" and page["kind"] == "topic"
    assert page["text"].startswith("# FDN notes\n\nMy own prose")
    assert page["blocks"][0]["filled"] and page["blocks"][0]["asked"] == "2026-09-21"
    # the answer's source is an edge of the page, a link like any
    assert _annotated(con, page["doc_id"]) == {
        "FDN reverb design",
        "Convolution reverb",
    }
    # a held block fills nothing and makes no revision; missing markers raise
    same = store.fill_blocks(con, "fdn-notes", {"q2": "x"}, asked="d", run="s")
    assert same["revision"] == 2 and same["report"] == {"q2": "held"}
    with pytest.raises(ValueError, match="no ask block q9"):
        store.fill_blocks(con, "fdn-notes", {"q9": "x"}, asked="d", run="s")
    with pytest.raises(KeyError):
        store.fill_blocks(con, "nowhere", {"q1": "x"}, asked="d", run="s")


# -------------------------------------------------------------------- pass


def test_the_pass_fills_new_blocks_and_asks_again_when_the_library_learns(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _ = _library(con)
    store.write_page(con, "fdn-notes", PAGE, kind="topic")
    page = questions.block_pages(con)[0]
    assert page["slug"] == "fdn-notes"
    q1, q2 = blocks.blocks(page["text"])
    assert questions.block_check(con, page, q1)["why"] == "not yet asked"
    assert questions.block_check(con, page, q2)["held"]
    rep = questions.refresh_blocks(con, page, answerer=ask.StubAnswerer())
    assert rep["refreshed"] == 1 and rep["revision"] == 2
    assert rep["blocks"]["q1"]["filled"] and rep["blocks"]["q1"]["model"] == "stub"
    assert rep["blocks"]["q2"] == {"filled": False, "why": questions.HELD, "held": True}
    page = store.get_page(con, "fdn-notes")
    assert page["revisions"][-1]["note"] == "ask stub; q1: not yet asked"
    b1 = page["blocks"][0]
    assert b1["filled"] and b1["model"] == "stub" and b1["sources"] == [a]
    assert b1["run"] == "stub" and b1["history"] == 1 and b1["left"] is None
    assert page["blocks"][1]["left"]["why"] == questions.HELD
    interior = page["text"][q1.interior[0] :].split("<!-- /prax:ask")[0]
    assert interior.startswith("\nStub answer to 'how do feedback delay networks")
    assert "Sources:" in interior and "How it was found" not in interior
    kept = page["meta"]["asks"]["q1"]
    assert kept["question"] == q1.question and kept["source_hashes"][str(a)]
    assert kept["options"] == {"steps": 0} and kept["since_id"] >= page["doc_id"]
    # settled: nothing new
    page = questions.block_pages(con)[0]
    rep = questions.refresh_blocks(con, page, answerer=ask.StubAnswerer())
    assert rep["refreshed"] == 0 and rep["blocks"]["q1"]["why"] == "nothing new"
    assert store.get_page(con, "fdn-notes")["revision"] == 2
    # a paper arrives: the block is due, the model sees the earlier answer
    # and the change beside the question, the interior is replaced
    c = store.ingest_text(
        con,
        "Reverb built from feedback delay networks with a Householder delay matrix,"
        " lossless and dense. " * 20,
        title="A newer FDN reverb",
    )["doc_id"]
    page = questions.block_pages(con)[0]
    seen = questions.block_check(con, page, blocks.blocks(page["text"])[0])
    assert seen["due"] and [n["doc_id"] for n in seen["new"]] == [c]
    asked: dict = {}
    real = ask.ask

    def spy(con_: sqlite3.Connection, question: str, **kw: object) -> dict:
        asked.update(question=question, history=kw.get("history"), note=kw.get("note"))
        return real(con_, question, **kw)

    monkeypatch.setattr(ask, "ask", spy)
    rep = questions.refresh_blocks(con, page, answerer=ask.StubAnswerer())
    assert rep["refreshed"] == 1 and rep["blocks"]["q1"]["new"][0]["doc_id"] == c
    assert asked["question"] == q1.question
    assert asked["history"] == [
        {
            "question": q1.question,
            "answer": "Stub answer to 'how do feedback delay networks build reverb'"
            " [1].",
        }
    ]
    assert asked["note"].startswith("the library now also holds A newer FDN reverb.")
    page = store.get_page(con, "fdn-notes")
    assert page["revision"] == 3 and page["blocks"][0]["history"] == 2
    assert page["revisions"][-1]["note"] == "ask stub; q1: new: A newer FDN reverb"
    assert page["text"].count("Stub answer") == 1  # replaced, not appended
    # the person's prose and their hand-written block are as they were
    assert "My own prose about delay networks, kept as it is." in page["text"]
    assert "an interior written by hand, never by the door" in page["text"]


def test_a_released_block_a_changed_question_and_a_dropped_block(
    con: sqlite3.Connection,
) -> None:
    _library(con)
    store.write_page(con, "fdn-notes", PAGE, kind="topic")
    page = questions.block_pages(con)[0]
    questions.refresh_blocks(con, page, answerer=ask.StubAnswerer())
    # released: the hand's interior gives way to an answer
    page = questions.block_pages(con)[0]
    rep = questions.refresh_blocks(
        con, page, block="q2", answerer=ask.StubAnswerer(), release=True
    )
    assert rep["refreshed"] == 1 and rep["blocks"] == {
        "q2": {"filled": True, "revision": 3, "new": [], "reread": [], "model": "stub"}
    }
    page = store.get_page(con, "fdn-notes")
    assert "written by hand" not in page["text"]
    assert page["revisions"][-1]["note"] == "ask stub; q2: released"
    assert page["blocks"][1]["left"] is None and not page["blocks"][1]["held"]
    # the person rewrites the question in the head: asked again
    text = page["text"].replace(
        '"what does convolution reverb multiply"', '"what is an impulse response"'
    )
    store.write_page(con, "fdn-notes", text, kind="topic")
    page = questions.block_pages(con)[0]
    b2 = blocks.blocks(page["text"])[1]
    assert questions.block_check(con, page, b2)["why"] == "the question changed"
    rep = questions.refresh_blocks(con, page, answerer=ask.StubAnswerer())
    assert rep["blocks"]["q2"]["filled"] and not rep["blocks"]["q1"]["filled"]
    assert "impulse response" in store.get_page(con, "fdn-notes")["text"]
    # the person deletes a block: the page forgets it
    text = store.get_page(con, "fdn-notes")["text"]
    q1 = blocks.blocks(text)[0]
    text = text[: q1.head[0]] + text[q1.tail[1] :]  # type: ignore[index]
    store.write_page(con, "fdn-notes", text, kind="topic")
    page = questions.block_pages(con)[0]
    questions.refresh_blocks(con, page, answerer=ask.StubAnswerer())
    assert list(store.get_page(con, "fdn-notes")["meta"]["asks"]) == ["q2"]


def test_standing_lists_blocks_beside_question_pages_and_refresh_all_runs_both(
    con: sqlite3.Connection,
) -> None:
    _library(con)
    store.write_page(con, "fdn-notes", PAGE, kind="topic")
    result = ask.ask(con, "convolution reverb", answerer=ask.StubAnswerer())
    questions.create(con, result, options={"steps": 0})
    rows = questions.standing(con)
    assert [r["slug"] for r in rows] == [
        "q-convolution-reverb",
        "fdn-notes#q1",
        "fdn-notes#q2",
    ]
    assert (
        rows[1]["due"] and rows[1]["why"] == "not yet asked" and not rows[1]["filled"]
    )
    assert rows[2]["held"] and not rows[2]["due"]
    rep = questions.refresh_all(con, answerer=ask.StubAnswerer())
    assert rep["checked"] == 2 and rep["refreshed"] == 1
    assert rep["pages"]["fdn-notes"]["blocks"]["q1"]["filled"]
    assert rep["pages"]["q-convolution-reverb"]["why"] == "nothing new"
    # one block by name; the briefing names the block that moved
    rep = questions.refresh_all(
        con, only="fdn-notes#q1", force=True, answerer=ask.StubAnswerer()
    )
    assert rep["checked"] == 1 and rep["refreshed"] == 1
    b = questions.briefing(con)
    assert b["moved"] == 2
    text = store.get_page(con, b["slug"])["text"]
    assert "— in Fdn notes, revision 2" in text and "revision 3" in text


# -------------------------------------------------------------------- door


@pytest.fixture()
def client(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PRAX_ASK", "stub")
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _wait(client: TestClient, job: int) -> dict:
    for _ in range(200):
        row = client.get(f"/jobs/{job}").json()
        if row["status"] != "running":
            return row
        time.sleep(0.1)
    return row


def test_the_door_answers_a_saved_block_and_lists_it(client: TestClient) -> None:
    con = store.connect()
    _library(con)
    r = client.put("/page/fdn-notes", json={"text": PAGE, "kind": "topic"}).json()
    assert r["created"] and r["job"]
    row = _wait(client, r["job"])
    assert row["status"] == "done" and row["note"] == "1 of 1 asked again"
    page = client.get("/page/fdn-notes").json()
    assert page["revision"] == 2 and page["blocks"][0]["filled"]
    assert page["blocks"][0]["model"] == "stub"
    assert page["asking"] == [] and page["asking_page"] is False
    # while the pass has a block in hand, the page and the listing say so
    from prax import api as api_mod

    with api_mod._answering_lock:
        api_mod._answering.add("fdn-notes#q2")
    try:
        assert client.get("/page/fdn-notes").json()["asking"] == ["q2"]
        rows = {q["slug"]: q["asking"] for q in client.get("/questions").json()}
        assert rows == {"fdn-notes#q1": False, "fdn-notes#q2": True}
        with api_mod._answering_lock:
            api_mod._answering.add("")  # a run over everything
        assert client.get("/page/fdn-notes").json()["asking"] == ["q1", "q2"]
    finally:
        with api_mod._answering_lock:
            api_mod._answering.clear()
    # nothing to ask: no job; a page without blocks: none either
    r = client.put(
        "/page/fdn-notes", json={"text": page["text"], "kind": "topic"}
    ).json()
    assert "job" not in r
    r = client.put("/page/plain", json={"text": "# Plain\n\nText.\n"}).json()
    assert "job" not in r
    # the filled block pasted into another page, markers and all: that
    # page has not asked it yet, so it is asked there at once, the pasted
    # answer as the conversation so far
    b1 = blocks.blocks(page["text"])[0]
    moved = "# Moved\n\nMine.\n\n" + page["text"][b1.head[0] : b1.tail[1]]  # type: ignore[index]
    r = client.put("/page/moved", json={"text": moved, "kind": "topic"}).json()
    assert r["created"] and r["job"]
    assert _wait(client, r["job"])["note"] == "1 of 1 asked again"
    other = client.get("/page/moved").json()
    assert other["revision"] == 2 and other["blocks"][0]["asked_at"]
    assert other["blocks"][0]["history"] == 1 and other["text"].startswith("# Moved")
    listed = client.get("/questions").json()
    assert [q["slug"] for q in listed] == ["fdn-notes#q1", "fdn-notes#q2", "moved#q1"]
    assert listed[1]["held"] and listed[0]["asked_at"]
    # release the held block through the run
    job = client.post(
        "/questions/run", json={"slug": "fdn-notes#q2", "force": True, "release": True}
    ).json()["job"]
    assert _wait(client, job)["status"] == "done"
    page = client.get("/page/fdn-notes").json()
    assert page["revision"] == 4 and not page["blocks"][1]["held"]  # 3: the re-save
    assert "written by hand" not in page["text"]
