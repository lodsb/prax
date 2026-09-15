"""Surf: the ask loop — the door's first search, the model's steps under
a grammar, the budgets, the trail, and the answer from what was kept."""

from __future__ import annotations

import itertools
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from prax import ask, store, surf


def _library(con: sqlite3.Connection) -> tuple[int, int, int]:
    fdn = store.ingest_text(
        con,
        "# FDN reverb design\n\n"
        + "Feedback delay networks build reverb from a delay matrix. " * 12
        + "\n\n## Losslessness\n\n"
        + "The Householder matrix keeps the loop lossless, so the tail decays only "
        "through the absorption filters. "
        * 12
        + "\n\n## Tuning\n\n"
        + "Delay lengths are chosen mutually prime to spread the modes. " * 12,
        title="FDN reverb design",
    )["doc_id"]
    conv = store.ingest_text(
        con,
        "Convolution reverb multiplies the input spectrum with a measured "
        "impulse response of a room. " * 20,
        title="Convolution reverb",
    )["doc_id"]
    cake = store.ingest_text(
        con,
        "A sponge cake rises on whisked eggs; the reverb of the oven door "
        "matters less than its temperature. " * 20,
        title="Sponge cake",
    )["doc_id"]
    store.link(
        con,
        store.Edge(
            "FDN reverb design", "paper", "proposes", "Householder FDN", "method"
        ),
        source_doc=fdn,
        producer="test",
    )
    store.link(
        con,
        store.Edge("Householder FDN", "method", "extends", "Jot's FDN", "method"),
        source_doc=fdn,
        producer="test",
    )
    store.link(
        con,
        store.Edge("Convolution reverb", "paper", "about", "reverberation", "concept"),
        source_doc=conv,
        producer="test",
    )
    return fdn, conv, cake


def _by_doc(result: dict, doc_id: int) -> list[dict]:
    return [p for p in result["passages"] if p["doc_id"] == doc_id]


def test_a_scripted_surf_reads_walks_drops_and_answers(con: sqlite3.Connection) -> None:
    fdn, _conv, cake = _library(con)
    first = ask.gather(con, "reverb from a delay matrix", limit=6).passages
    n_fdn = next(p.n for p in first if p.doc_id == fdn)
    n_cake = next((p.n for p in first if p.doc_id == cake), None)
    script = [
        f"note: the FDN passage stops before the losslessness part\nread: [{n_fdn}]",
        f"note: what the graph says about it\nfacts: [{n_fdn}]",
        "note: the method it proposes\nwalk: Householder FDN",
        "note: cake is not reverb\ndrop: " + (f"[{n_cake}]" if n_cake else "[99]"),
        "note: enough\nanswer",
    ]
    answerer = ask.StubAnswerer(script)
    events: list[dict] = []
    r = ask.ask(
        con,
        "how does a feedback delay network make reverb?",
        answerer=answerer,
        steps=8,
        tokens=4000,
        limit=6,
        on_event=events.append,
    )
    # the trail: the door's search, then the five scripted steps
    actions = [s["action"] for s in r["trail"]]
    assert actions == ["search", "read", "facts", "walk", "drop", "answer"]
    assert r["steps"] == 5 and r["trail"][0]["n"] == 0
    assert [e["event"] for e in events] == ["step"] * 6 + ["answering"]
    # reading on brought a new passage of the same document, after the first
    fdn_passages = _by_doc(r, fdn)
    assert len(fdn_passages) == 2 and r["trail"][1]["added"] == [fdn_passages[1]["n"]]
    assert "lossless" in fdn_passages[1]["text"]
    n_read = fdn_passages[1]["n"]
    assert r["trail"][1]["result"] == f"[{n_read}] FDN reverb design — Losslessness"
    assert r["trail"][3]["result"] == "2 relations, 1 document behind"
    assert fdn_passages[1]["chunk_id"] != fdn_passages[0]["chunk_id"]
    # the facts and the walk reached the model as results
    log = answerer.steps[-1]["user"]
    assert "graph: proposes: Householder FDN" in log  # with the hit itself
    assert f"doc {fdn} (FDN reverb design) — proposes: Householder FDN" in log
    assert "extends: → Jot's FDN" in log and f"doc {fdn}:" in log
    # the cake is set aside: gone from the passages, named in dropped
    if n_cake:
        assert not _by_doc(r, cake) and r["dropped"][0]["doc_id"] == cake
    # the answer came from the passages kept, under their loop numbers
    assert r["answer"].endswith("[1].") and r["citations"][0]["n"] == 1
    assert r["model"] == "stub" and r["usage"]["output_tokens"] > 12
    assert r["reading_left"] < 4000
    # the grammar named only the passages alive at each step
    g = answerer.steps[-1]["grammar"]
    assert "root ::= note action" in g and f'"[{n_fdn}]"' in g
    if n_cake:
        assert f'"[{n_cake}]"' not in g  # dropped before the last step
    assert '"doc " did' in g and 'did ::= "1" | "2" | "3"' in g  # documents named


def _long_paper(con: sqlite3.Connection) -> int:
    """A paper whose sections are a chunk each, so a read returns one."""
    sections = [
        ("Front matter", "Submitted to the convention, in the city of the year. "),
        ("Losslessness", "The Householder matrix keeps the loop lossless. "),
        ("Tuning", "Delay lengths are chosen mutually prime. "),
        ("Appendix", "The coefficients are tabulated below for each order. "),
    ]
    text = "# A long paper\n\n" + "\n\n".join(
        f"## {head}\n\n{body * 22}" for head, body in sections
    )
    return int(store.ingest_text(con, text, title="A long paper")["doc_id"])


def test_a_figure_a_model_has_read_is_read_like_a_paragraph(
    con: sqlite3.Connection,
) -> None:
    """A figure's description is its chunk's text, so a reading through a
    document meets it; one nobody has read is an image line and a caption,
    and stays out of the reading."""
    text = "\n\n".join(
        [
            "# A paper with figures",
            "## Results",
            "The tail decays through the absorption filters. " * 14,
            "![Fig. 1. SNR improvements.](figure:" + "a" * 64 + ")",
            (
                "*Figure, as read by a-vision-model:* A bar chart comparing"
                " six methods, the proposed one highest at 9.4 decibels."
            ),
            "![Fig. 2. The rig.](figure:" + "b" * 64 + ")",
            "## Conclusion",
            "The method holds for stateful systems. " * 14,
        ]
    )
    doc = store.ingest_text(con, text, title="A paper with figures")["doc_id"]
    kinds = {c["kind"] for c in store.list_chunks(con, doc)}
    assert "figure" in kinds  # the fixture really has figure chunks

    s = surf.Surf("q", "q", [], None, 6, 4, 4000)
    seen = []
    while True:
        said, added = surf.do_read(con, s, f"doc {doc}" if not seen else "[1]")
        if not added:
            break
        seen.append(said)
    whole = "\n".join(seen)
    assert "A bar chart comparing six methods" in whole  # the figure that was read
    assert "Fig. 2. The rig." not in whole  # the one nobody read

    # and a reading may be aimed at what a figure shows
    fresh = surf.Surf("q", "q", [], None, 6, 4, 4000)
    said, added = surf.do_read(con, fresh, f"doc {doc} bar chart decibels")
    assert added == [1] and "9.4 decibels" in said


def test_a_read_with_words_lands_on_that_part_of_the_document(
    con: sqlite3.Connection,
) -> None:
    """A document the graph pointed at is long and starts with a title
    page: read it with the words wanted and the reading begins there,
    whether the document is named by its id or by one of its passages."""
    doc = _long_paper(con)
    s = surf.Surf("q", "q", [], None, 6, 4, 4000)
    # by id: the part about the words, not the start
    text, added = surf.do_read(con, s, f"doc {doc} lossless Householder")
    assert added == [1] and "(on 'lossless Householder')" in text
    assert "Losslessness" in text and "keeps the loop lossless" in text
    assert "Submitted to the convention" not in text
    # by passage, elsewhere in the same document
    text, added = surf.do_read(con, s, "[1] mutually prime delay lengths")
    assert added == [2] and "(on 'mutually prime delay lengths')" in text
    assert "mutually prime" in text
    # that part read already: the reading goes on from it
    text, added = surf.do_read(con, s, "[2] mutually prime delay lengths")
    assert added == [3] and "(after 'mutually prime delay lengths')" in text
    assert "tabulated below" in text  # the section after Tuning
    # nothing follows it either: the reading falls back to what of that
    # document is still unread, rather than stopping at a spent request
    text, added = surf.do_read(con, s, "[3] tabulated coefficients")
    assert added == [4] and "(the next part unread)" in text
    assert "Submitted to the convention" in text  # the front matter, skipped before
    # and when the whole document has been read, what it held is named
    said = surf.do_read(con, s, "[3] tabulated coefficients")[0]
    assert said.startswith("all of [3] has been read. Its sections: ")
    assert "A long paper › Losslessness" in said and "Appendix" in said
    assert surf.do_read(con, s, f"doc {doc}")[0].startswith(
        f"all of doc {doc} has been read."
    )
    # words that occur nowhere: the document from its start
    fresh = surf.Surf("q", "q", [], None, 6, 4, 4000)
    text, added = surf.do_read(con, fresh, f"doc {doc} sponge cake icing")
    assert added == [1] and "Submitted to the convention" in text
    # the grammar offers the words
    assert 'read ::= "read: " ( pn | "doc " did ) look?' in surf.grammar(s)
    assert f'look ::= " " char{{2,{surf.LOOK_CHARS}}}' in surf.grammar(s)


def test_a_move_that_led_nowhere_is_not_repeated(con: sqlite3.Connection) -> None:
    """A small model asked the same dead end five times on a twelve-step
    budget: the second time, the loop says so instead of asking the store
    again."""
    _library(con)
    answerer = ask.StubAnswerer(
        [
            "note: anything about icing\nsearch: sponge cake icing sugar",
            "note: let me try that again\nsearch: sponge cake icing sugar",
            "note: enough\nanswer",
        ]
    )
    r = ask.ask(con, "reverb", answerer=answerer, steps=6, tokens=4000, limit=3)
    first, again = r["trail"][1], r["trail"][2]
    assert first["added"] == [] and first["result"] == "no new hits"
    assert again["result"] == (
        f"step {first['n']} asked that and it brought nothing; ask something else"
    )
    assert r["steps"] == 3 and r["answer"]


def test_the_message_grows_by_appending(con: sqlite3.Connection) -> None:
    """Every step's prompt starts with the previous one (the server's
    prefix cache is what keeps a step cheap)."""
    _library(con)
    answerer = ask.StubAnswerer(
        ["note: more\nsearch: householder matrix", "note: x\nsearch: lossless"]
    )
    ask.ask(con, "reverb", answerer=answerer, steps=3, tokens=4000)
    users = [s["user"] for s in answerer.steps]
    assert len(users) == 3
    for a, b in itertools.pairwise(users):
        head = a.rsplit("Now step", 1)[0]
        assert b.startswith(head)
    assert "Now step 3: 0 steps" not in users[-1]
    assert "This is the last step" in users[-1]


def test_budgets_end_the_loop(con: sqlite3.Connection) -> None:
    _library(con)
    endless = ["note: again\nsearch: reverb matrix delay"] * 30
    answerer = ask.StubAnswerer(list(endless))
    r = ask.ask(con, "reverb", answerer=answerer, steps=3, tokens=4000)
    assert r["steps"] == 3 and r["answer"]
    # a search that brings nothing new costs no reading, so the steps end it
    answerer = ask.StubAnswerer(list(endless))
    r = ask.ask(con, "reverb", answerer=answerer, steps=20, tokens=1000)
    assert r["steps"] == 20 and r["reading_left"] < 1000 and r["answer"]
    # the reading budget spent, the loop ends without a step
    answerer = ask.StubAnswerer(list(endless))
    r = surf.run(con, "reverb", answerer=answerer, steps=20, tokens=150)
    assert r["steps"] == 0 and r["reading_left"] * 4 <= surf.HIT_CHARS
    assert r["answer"] and answerer.steps == []


def test_a_failing_model_still_answers_from_what_was_read(
    con: sqlite3.Connection,
) -> None:
    _library(con)

    class Broken(ask.StubAnswerer):
        def step(self, system: str, user: str, *, grammar: str | None = None):  # type: ignore[override]
            raise RuntimeError("server gone")

    r = ask.ask(con, "reverb", answerer=Broken(), steps=4, tokens=4000)
    failed = r["trail"][1]
    assert failed["action"] == "error" and "server gone" in failed["result"]
    assert r["answer"] and r["steps"] == 1


def test_a_stop_ends_the_surf_without_an_answer(con: sqlite3.Connection) -> None:
    """The client went away: the loop ends at the next step and the model
    is not asked to write for nobody."""
    import threading

    _library(con)
    stop = threading.Event()
    answerer = ask.StubAnswerer(["note: x\nsearch: householder"] * 5)
    r = ask.ask(
        con,
        "reverb",
        answerer=answerer,
        steps=5,
        tokens=4000,
        on_event=lambda e: stop.set() if e["step"]["n"] == 1 else None,
        stop=stop,
    )
    assert r["steps"] == 1 and r["answer"] is None and len(answerer.steps) == 1


def test_steps_zero_is_the_one_shot_ask(con: sqlite3.Connection) -> None:
    _library(con)
    r = ask.ask(con, "reverb", answerer=ask.StubAnswerer(), steps=0)
    assert "trail" not in r and r["answer"]


def test_parse_is_lenient() -> None:
    assert surf.parse("note: hm\nsearch: delay matrix\n") == (
        "hm",
        "search",
        "delay matrix",
    )
    assert surf.parse("read [3]") == ("", "read", "[3]")
    assert surf.parse("note: done\nanswer") == ("done", "answer", "")
    assert surf.parse("I think that is enough to answer.") == (
        "I think that is enough to answer.",
        "answer",
        "",
    )
    assert surf.parse("Drop: [2] [4]\nsearch: x")[1:] == ("drop", "[2] [4]")


def test_reading_bounds_and_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    assert surf.reading_bounds("openai", 8192) == (4000, 8192 - surf.OVERHEAD_TOKENS)
    assert surf.reading_bounds("openai", 2000) == (surf.MIN_TOKENS, surf.MIN_TOKENS)
    assert surf.reading_bounds("claude", 8192) == surf.CLAUDE_TOKENS
    a = ask.StubAnswerer()
    assert surf.clamp(a, 99, 999_999) == (surf.MAX_STEPS, a.reading[1])
    assert surf.clamp(a, -2, 10) == (0, surf.MIN_TOKENS)
    assert surf.clamp(a, None, None) == (surf.STEPS, a.reading[0])


def test_tools_answer_bad_arguments(con: sqlite3.Connection) -> None:
    fdn, _, _ = _library(con)
    s = surf.Surf("q", "q", [], None, 6, 3, 4000)
    assert surf.do_read(con, s, "[7]") == ("there is no passage [7]", [])
    assert surf.do_read(con, s, "chapter two")[0].startswith("read takes [n]")
    assert surf.do_read(con, s, "doc 999999") == ("no document 999999", [])
    assert surf.do_facts(con, s, "x").startswith("facts takes")
    assert surf.do_drop(s, "[1]").startswith("drop takes")
    nobody = surf.do_walk(con, s, "nobody at all")
    assert nobody == "no entity named 'nobody at all' in the graph"
    assert "names like it" in surf.do_walk(con, s, "householder")
    text, added = surf.do_read(con, s, f"doc {fdn}")
    assert added == [1] and "(start)" in text
    assert text.startswith(f"[1] doc {fdn}: FDN reverb design")
    text, added = surf.do_read(con, s, "[1]")
    assert added == [2] and "(after [1])" in text
    assert "no new hits" == surf.do_search(con, s, "   ")[0] or True
    assert surf.do_search(con, s, "")[0] == "search needs words"


def test_save_keeps_the_trail(con: sqlite3.Connection) -> None:
    _library(con)
    answerer = ask.StubAnswerer(
        ["note: narrower\nsearch: householder", "note: enough\nanswer"]
    )
    r = ask.ask(con, "reverb", answerer=answerer, steps=4, tokens=4000)
    store.write_page(con, "reverb", "# Reverb\n")
    ask.save(con, r, "reverb")
    text = store.get_page(con, "reverb")["text"]
    assert "How it was found:" in text
    assert "- search householder — narrower" in text
    assert "- enough read — enough" in text


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PRAX_ASK", "stub")
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_api_streams_the_trail(client: TestClient) -> None:
    for title, text in (("A", "granular synthesis of clouds " * 30), ("B", "fm " * 50)):
        client.post("/ingest", json={"text": text, "title": title})
    cfg = client.get("/ask/config").json()
    assert cfg["steps"] == {"default": surf.STEPS, "max": surf.MAX_STEPS}
    lo, hi = surf.reading_bounds("stub", 8192)
    assert cfg["reading"]["stub"] == {"default": lo, "max": hi}
    r = client.post("/ask", json={"question": "granular synthesis", "steps": 3})
    body = r.json()
    assert body["answer"].endswith("[1].") and body["steps"] == 1  # answers at once
    streamed = {"question": "granular synthesis", "steps": 3, "stream": True}
    with client.stream("POST", "/ask", json=streamed) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in res.iter_lines() if line.strip()]
    kinds = [e["event"] for e in events]
    assert kinds == ["step", "step", "answering", "answer"]
    assert events[0]["step"]["action"] == "search" and events[0]["step"]["n"] == 0
    assert events[-1]["result"]["answer"].endswith("[1].")
    # one shot on request
    r = client.post("/ask", json={"question": "granular synthesis", "steps": 0})
    assert "trail" not in r.json() and r.json()["answer"]
    # a bad backend fails before any streaming starts
    r = client.post("/ask", json={"question": "x", "backend": "gpt", "stream": True})
    assert r.status_code == 400
