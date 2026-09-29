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
