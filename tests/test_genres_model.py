"""Asking a local model what a document is and what it is about
(`prax.writing.genres`): the view it reads, the questions, the gate
from level to label, and the list alternative. The server is faked."""

from __future__ import annotations

from typing import Any

import pytest

from prax.graph import ontology
from prax.writing import genres as gw


def test_the_view_carries_what_says_what_a_document_is() -> None:
    meta = {
        "source": "capture",
        "lang": "de",
        "summary": "A recipe for apple cake.",
        "sections": {"items": [{"summary": "The dough."}, {"summary": "Baking."}]},
    }
    v = gw.view("Apfelkuchen", meta, "Zutaten\n- 6 Äpfel\n" * 200, where="https://x/y")
    assert v.startswith("Title: Apfelkuchen\nSource: capture (https://x/y)")
    assert "Language: de" in v and "Summary: A recipe for apple cake." in v
    assert "Sections: The dough. / Baking." in v
    assert "The beginning of the text:\nZutaten - 6 Äpfel" in v
    assert len(v) < gw.OPENING + 400
    alone = gw.view("Apfelkuchen", meta, "Zutaten", opening=0)
    assert "beginning of the text" not in alone


def test_a_question_asks_what_a_label_means() -> None:
    G, S = ontology.genres(), ontology.subjects()
    assert gw.question(G, "datasheet", "a part's sheet", about=False) == (
        "Is this document a datasheet, that is: a part's sheet?"
    )
    assert gw.question(G, "opinion", "argues a view", about=False).startswith(
        "Is this document opinion, that is: it argues"
    )
    assert gw.question(S, "politics", "government", about=True) == (
        "Is this document about politics (government)?"
    )


def test_labels_are_asked_only_under_a_likely_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recipe is not asked whether it is a thesis: the labels under a
    level are asked only where the level is at least GATE likely."""
    asked: list[str] = []
    yes = {"instructional": 0.9, "recipe": 0.95, "informational": 0.1}

    def fake(base_url: str, model: str, document: str, q: str) -> float | None:
        asked.append(q)
        for label, p in yes.items():
            if f" {label}," in q or f" {label} " in q:
                return p
        return 0.02

    monkeypatch.setattr(gw, "ask", fake)
    G = ontology.genres()
    got = gw.probabilities("http://x/v1", "m", "doc", G, about=False, slots=1)
    assert got["recipe"] == 0.95 and got["instructional"] == 0.9
    assert "thesis" not in got and "paper" not in got  # informational was 0.1
    assert {lv.name for lv in G.levels} <= set(got)
    under_instructional = {g for g, _ in G.levels[1].genres}
    assert under_instructional <= set(got)
    assert len(asked) == len(G.levels) + len(under_instructional)


def test_the_list_is_held_to_the_vocabulary() -> None:
    G = ontology.genres()
    grammar = gw.list_grammar(G)
    assert grammar.startswith('root ::= label (", " label)*')
    assert '"datasheet"' in grammar and '"blog"' not in grammar

    class Fake:
        def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, int]]:
            assert "- datasheet:" in system and kw["grammar"] == grammar
            return "datasheet, instructional", {}

    assert gw.listed(Fake(), "doc", G, about=False) == ["instructional", "datasheet"]


def test_label_asks_what_the_list_proposes_and_keeps_the_likely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The list proposes, each proposal and its level is asked, and a
    calibrated probability under ``keep`` is dropped."""
    G, S = ontology.genres(), ontology.subjects()
    monkeypatch.setattr(
        gw,
        "listed",
        lambda rt, doc, facet, about: ["electronics"] if about else ["datasheet"],
    )
    asked: list[str] = []

    def fake(base_url: str, model: str, document: str, q: str) -> float | None:
        asked.append(q)
        return 0.9 if "datasheet" in q or "electronics" in q else 0.2

    monkeypatch.setattr(gw, "ask", fake)
    got = gw.label(None, "http://x/v1", "m", "doc", G, S, slots=1)
    assert got == {"g": {"datasheet": 0.9}, "s": {"electronics": 0.9}}
    assert len(asked) == 4  # datasheet, instructional, electronics, technology
    # a Platt map that lowers labels drops the datasheet under keep
    low = {"genres labels": [1.0, -5.0]}
    got = gw.label(None, "http://x/v1", "m", "doc", G, S, platt=low, slots=1)
    assert got["g"] == {} and got["s"] == {"electronics": 0.9}


def test_calibrated_reads_the_map_of_the_labels_kind() -> None:
    G = ontology.genres()
    maps = {"genres levels": {"a": 1.0, "b": 0.0}, "genres labels": [2.0, 0.0]}
    assert gw.calibrated(0.7, "informational", G, "genres", maps) == pytest.approx(0.7)
    assert gw.calibrated(0.7, "paper", G, "genres", maps) > 0.8
    assert gw.calibrated(0.7, "paper", G, "genres", None) == 0.7


def test_the_step_labels_through_the_door(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hand-out, worker, take-in: the open documents go out with their
    view, the model's labels come back with the run, a document with no
    genre kept is not handed out again, and a person's labels are never
    handed out or written over."""
    from fastapi.testclient import TestClient

    from prax import store, work
    from prax.api import app
    from prax.steps import writing as step_writing

    monkeypatch.setenv("PRAX_GENRES", "stub")
    work._leases.clear()
    with TestClient(app) as client:
        con = client.app.state.con

        def doc(text: str, title: str) -> int:
            return int(
                client.post("/ingest", json={"text": text, "title": title}).json()[
                    "doc_id"
                ]
            )

        paper = doc("We propose a reverb. Results show it works. " * 30, "A reverb")
        nothing = doc("lorem ipsum " * 50, "Lorem")
        mine = doc("A recipe for apple cake. " * 30, "Apfelkuchen")
        store.set_genres(con, mine, ["recipe"], subjects=["cooking"])

        batch = client.get("/work/genres", params={"scope": "all"}).json()
        ids = sorted(i["doc_id"] for i in batch["items"])
        assert ids == sorted([paper, nothing])
        assert batch["items"][0]["view"].startswith("Title: ")

        answers = {
            paper: {"g": {"paper": 0.92}, "s": {"audio": 0.7}},
            nothing: {"g": {}, "s": {}},
        }
        monkeypatch.setattr(
            step_writing.genres,
            "label",
            lambda rt, base, model, view, G, S, **kw: answers[
                paper if "reverb" in view else nothing
            ],
        )

        class Runtime:
            base_url = "http://x/v1"
            model = "m"
            name = "m@x"

        results = step_writing.do_genres(batch["items"], Runtime())
        rep = client.post("/work/genres", json={"results": results}).json()
        assert rep["applied"] == 1 and rep["skipped"] == 1

        m = store.get_meta(con, paper)
        assert m["genres_by"] == "m@x" and m["genres_run"].startswith("genres-")
        assert m["genres"] == [
            {"genre": "informational", "p": 0.92},
            {"genre": "paper", "p": 0.92},
        ]
        assert m["subjects"][-1] == {"subject": "audio", "p": 0.7}
        assert store.get_meta(con, nothing)["genres_tried"]["why"] == "no genre kept"
        assert store.get_meta(con, mine)["genres_by"] == "human"
        work._leases.clear()
        again = client.get("/work/genres", params={"scope": "all"}).json()
        assert again["items"] == []
