"""Ask: the bundle (passages plus graph facts), backends, citations, and
keeping an answer on a page with annotates edges."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import ask, config, store


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
    store.link(
        con,
        store.Edge(
            "FDN reverb design", "paper", "proposes", "Householder FDN", "method"
        ),
        source_doc=a,
        producer="test",
    )
    store.link(
        con,
        store.Edge("FDN reverb design", "paper", "about", "reverberation", "concept"),
        source_doc=a,
        producer="test",
    )
    store.link(
        con,
        store.Edge("FDN reverb design", "paper", "cites", "Some other paper", "paper"),
        source_doc=a,
        producer="test",
    )
    return a, b


def test_gather_passages_and_facts(con: sqlite3.Connection) -> None:
    a, b = _library(con)
    bundle = ask.gather(con, "how does a feedback delay network make reverb?")
    ids = [p.doc_id for p in bundle.passages]
    assert a in ids and len(ids) == len(set(ids))  # one passage per document
    first = bundle.passages[0]
    assert first.n == 1 and first.chunk_id is not None and first.text
    assert all(len(p.text) <= ask.PASSAGE_CHARS for p in bundle.passages)
    facts = bundle.facts[a]
    assert {f["rel"] for f in facts} == {"proposes", "about"}  # cites left out
    msg = bundle.as_message()
    assert msg.startswith("Question: how does")
    assert "[1] " in msg and "proposes: Householder FDN" in msg
    assert bundle.facts.get(b) == []


def test_gather_empty_question(con: sqlite3.Connection) -> None:
    assert ask.gather(con, "   ").passages == []


def test_facts_use_canonical_names(con: sqlite3.Connection) -> None:
    a, _ = _library(con)
    store.link(
        con,
        store.Edge("FDN reverb design", "paper", "uses", "FDNs", "method"),
        source_doc=a,
        producer="test",
    )
    ids = {
        r["name"]: r["id"]
        for r in con.execute("SELECT id, name FROM entities WHERE type = 'method'")
    }
    store.merge_entities(con, ids["FDNs"], ids["Householder FDN"])
    facts = store.document_facts(con, [a])[a]
    assert {(f["rel"], f["name"]) for f in facts} == {
        ("proposes", "Householder FDN"),
        ("uses", "Householder FDN"),
        ("about", "reverberation"),
    }


class FakeRuntime:
    name = "local:fake"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def chat(
        self,
        system: str,
        user: str,
        *,
        grammar: str | None = None,
        max_tokens: int = 2000,
        temperature: float = 0.0,
        repeat_penalty: float = 1.0,
    ) -> tuple[str, dict[str, int]]:
        self.calls.append({"system": system, "user": user, "grammar": grammar})
        text = "Delay matrices make reverb [1]. Convolution uses impulse responses [2]"
        return text + " and nothing supports [9].", {
            "input_tokens": 100,
            "output_tokens": 20,
        }


def test_local_answer_and_citations(con: sqlite3.Connection) -> None:
    a, b = _library(con)
    rt = FakeRuntime()
    r = ask.ask(con, "reverb methods", answerer=ask.LocalAnswerer(rt))
    assert r["model"] == "local:fake" and r["cost_usd"] == 0
    assert rt.calls[0]["grammar"] is None
    assert "Passages (1 to 2):" in str(rt.calls[0]["user"])
    assert [c["n"] for c in r["citations"]] == [1, 2]  # [9] is no passage
    assert {c["doc_id"] for c in r["citations"]} == {a, b}
    assert r["usage"]["output_tokens"] == 20


def test_an_answer_too_long_for_the_slot_is_cut_to_fit(
    con: sqlite3.Connection,
) -> None:
    """The reading budget is an estimate in characters; when the passages
    kept overrun the server's slot, they are cut to the share it states
    and asked once more, with every passage still numbered."""
    _library(con)

    class Tight(FakeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.lengths: list[int] = []

        def chat(self, system: str, user: str, **kw: object):  # type: ignore[override]
            self.lengths.append(len(user))
            if len(self.lengths) == 1:
                raise RuntimeError(
                    "http://127.0.0.1:8080/v1/chat/completions: HTTP 400:"
                    " request (9000 tokens) exceeds the available context"
                    " size (8192 tokens)"
                )
            return super().chat(system, user, **kw)

    rt = Tight()
    r = ask.ask(con, "reverb methods", answerer=ask.LocalAnswerer(rt))
    assert r["usage"]["cut"] == 1 and len(rt.lengths) == 2
    assert rt.lengths[1] < rt.lengths[0]
    assert [c["n"] for c in r["citations"]] == [1, 2]  # still numbered


def test_bundle_only_without_backend(con: sqlite3.Connection) -> None:
    _library(con)
    r = ask.ask(con, "reverb", answerer=None)
    assert r["answer"] is None and r["passages"] and r["citations"] == []


def test_backend_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    from prax import models

    for var in ("PRAX_ASK", "PRAX_ASK_MODEL", "PRAX_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    models.reset()
    assert ask.current() is None and ask.describe()["default"] == "none"
    cfg = Path(config.data_dir()) / models.CONFIG_NAME
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "models: {srv: {kind: openai, base_url: http://127.0.0.1:1/v1, model: q}}\n"
        "steps: {ask: {model: srv}}\n",
        encoding="utf-8",
    )
    models.reset()
    d = ask.describe()
    assert d["default"] == "srv" and d["runtime"] == "q@127.0.0.1:1"
    assert isinstance(ask.current(), ask.LocalAnswerer)
    monkeypatch.setenv("PRAX_ASK", "stub")
    assert isinstance(ask.current(), ask.StubAnswerer)
    monkeypatch.setenv("PRAX_ASK", "claude")
    assert ask.current().name == ask.DEFAULT_CLAUDE_MODEL
    assert ask.answerer_named("none") is None
    with pytest.raises(ValueError):
        ask.answerer_named("gpt")


def test_a_note_reaches_the_model_beside_the_question_and_not_the_search(
    con: sqlite3.Connection,
) -> None:
    _library(con)
    runtime = FakeRuntime()
    r = ask.ask(
        con,
        "reverb methods",
        answerer=ask.LocalAnswerer(runtime),
        note="the library now also holds a newer paper; revise where it changes",
    )
    assert r["answer"]
    prompt = runtime.calls[-1]["user"]
    assert "Question: reverb methods\nNote: the library now also holds" in prompt
    # the surf carries it the same way (its own bundle at the answer)
    stub = ask.StubAnswerer()
    r = ask.ask(con, "reverb methods", answerer=stub, steps=1, note="a word")
    assert r["answer"] and any("Note: a word" in s["user"] for s in stub.steps)


def test_save_appends_with_sources_and_edges(con: sqlite3.Connection) -> None:
    a, b = _library(con)
    store.write_page(con, "reverb", "# Reverb\n\nMy notes.")
    r = ask.ask(con, "reverb methods", answerer=ask.LocalAnswerer(FakeRuntime()))
    saved = ask.save(con, r, "reverb")
    assert saved["revision"] == 2
    page = store.get_page(con, "reverb")
    assert page["text"].startswith("# Reverb\n\nMy notes.")
    assert "## reverb methods" in page["text"]
    assert f"[FDN reverb design](#doc/{a})" in page["text"]
    assert f"[Convolution reverb](#doc/{b})" in page["text"]
    assert page["revisions"][-1]["author"] == "agent"
    assert page["revisions"][-1]["note"] == "ask: local:fake"
    annotated = {
        r["dst_name"]
        for r in con.execute(
            "SELECT t.name AS dst_name FROM edges x JOIN entities t ON t.id = x.dst"
            " WHERE x.source_doc = ? AND x.rel = 'annotates' AND x.valid_to IS NULL",
            (page["doc_id"],),
        )
    }
    assert annotated == {"FDN reverb design", "Convolution reverb"}
    # human text stays; a second save is another revision
    ask.save(con, r, "reverb", heading="again")
    assert store.get_page(con, "reverb")["revision"] == 3


def test_save_can_start_a_synthesis_page(con: sqlite3.Connection) -> None:
    _library(con)
    r = ask.ask(con, "reverb methods", answerer=ask.LocalAnswerer(FakeRuntime()))
    saved = ask.save(con, r, "Reverb Survey", create="synthesis")
    assert saved["created"] and saved["slug"] == "reverb-survey"
    page = store.get_page(con, "reverb-survey")
    assert page["kind"] == "synthesis" and page["author"] == "agent"
    assert page["text"].startswith("# reverb methods\n\nDelay matrices")
    rels = {
        row["rel"]
        for row in con.execute(
            "SELECT rel FROM edges WHERE source_doc = ? AND valid_to IS NULL",
            (page["doc_id"],),
        )
    }
    assert rels == {"synthesizes"}
    # a second save appends (the page exists now)
    again = ask.save(con, r, "reverb-survey", create="synthesis", heading="more")
    assert not again["created"] and again["revision"] == 2


def test_save_needs_an_answer(con: sqlite3.Connection) -> None:
    store.write_page(con, "p", "x")
    with pytest.raises(ValueError):
        ask.save(con, {"answer": None, "passages": []}, "p")
    with pytest.raises(KeyError):
        ask.save(con, {"answer": "a", "question": "q"}, "missing")


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PRAX_ASK", "stub")
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_api_ask_and_save(client: TestClient) -> None:
    for title, text in (("A", "granular synthesis of clouds " * 30), ("B", "fm " * 50)):
        client.post("/ingest", json={"text": text, "title": title})
    assert client.get("/ask/config").json()["default"] == "stub"
    r = client.post("/ask", json={"question": "granular synthesis"})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "stub" and body["answer"].endswith("[1].")
    assert body["citations"][0]["title"] == "A"
    # bundle only, on request
    r = client.post("/ask", json={"question": "granular", "backend": "none"})
    assert r.json()["answer"] is None and r.json()["passages"]
    assert (
        client.post("/ask", json={"question": "x", "backend": "gpt"}).status_code == 400
    )

    client.put("/page/synthesis", json={"text": "# Synthesis\n"})
    r = client.post("/ask/save", json={"slug": "synthesis", "result": body})
    assert r.status_code == 200 and r.json()["revision"] == 2
    page = client.get("/page/synthesis").json()
    assert "## granular synthesis" in page["text"] and "[A](#doc/" in page["text"]
    r = client.post("/ask/save", json={"slug": "nope", "result": body})
    assert r.status_code == 404


def test_a_follow_up_carries_the_conversation(con: sqlite3.Connection) -> None:
    """Earlier turns reach the model before the new question, a short or
    pointing-back question widens the search with the previous one, and
    the citations are this turn's only."""
    _library(con)
    history = [
        {
            "question": "how does a feedback delay network make reverb?",
            "answer": "It feeds delays back [1].",
        },
        {"junk": True},
        {"question": "", "answer": "empty"},
    ]
    turns = ask.clean_history(history)
    assert turns == [history[0]]
    assert ask.search_query("what about its density?", turns).endswith(
        "how does a feedback delay network make reverb?"
    )
    assert ask.search_query("and the second one", turns).startswith(
        "and the second one how"
    )
    assert ask.search_query(
        "granular synthesis with many short grains of sound", turns
    ) == ("granular synthesis with many short grains of sound")
    assert ask.search_query("anything", []) == "anything"

    class Recorder:
        name = "recorder"
        seen = ""

        def answer(self, bundle: ask.Bundle) -> tuple[str, dict[str, int]]:
            self.seen = bundle.as_message()
            return "Denser with more delays [1].", {}

    rec = Recorder()
    out = ask.ask(con, "what about its density?", answerer=rec, history=history)
    assert out["turns_before"] == 1 and out["answer"].startswith("Denser")
    assert rec.seen.startswith(
        "Earlier in this conversation:\n\nQ: how does a feedback"
    )
    assert "A: It feeds delays back [1]." in rec.seen
    assert "\nQuestion: what about its density?\n" in rec.seen
    assert out["citations"] and out["citations"][0]["n"] == 1
    # a long, self-contained follow-up searches on its own words
    long = ask.clean_history([{"question": "q", "answer": "a"}] * 10)
    assert len(long) == ask.HISTORY_TURNS
