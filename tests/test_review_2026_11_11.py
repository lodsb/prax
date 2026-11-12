"""The safety and correctness review of 2026-11-11
(``docs/eval/safety-review-2026-11-11.md``): each fix with a test that
fails on the code the review read. Numbers are the findings' there; 1, 2,
3, 4, 9 and 12 are tested beside the code they touch (``test_schema``,
``test_unretire``, ``test_models``, ``test_document_nodes``)."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from prax import store
from prax.ml import usage


def _doc(con: sqlite3.Connection, text: str, title: str) -> int:
    doc = int(
        store.register(con, text.encode(), mime="text/plain", title=title)["doc_id"]
    )
    store.index_text(con, doc, text)
    return doc


def _entity(con: sqlite3.Connection, name: str, etype: str) -> int:
    doc = _doc(con, f"{name} is mentioned here. " * 20, f"About {name}")
    store.link(
        con,
        store.Edge(f"About {name}", "paper", "mentions", name, etype),
        source_doc=doc,
        producer="test",
    )
    row = con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
    ).fetchone()
    return int(row[0])


# 5 -------------------------------------------------------------------------


def test_a_rename_is_undone_with_a_variant_of_its_old_name(
    con: sqlite3.Connection,
) -> None:
    """A rename marked every label of the old name, any case and language,
    as what the entity was called; the undo made each preferred again and
    the second preferred label of a language was refused."""
    ent = _entity(con, "Apple", "organization")
    store.add_label(con, ent, "APPLE", lang="en", kind="alt", producer="test")
    store.add_label(con, ent, "apple", lang="de", kind="alt", producer="test")
    assert store.rename_entity(con, ent, "Apple Inc.", run="rename-x") == "renamed"
    store.unmerge_run(con, "rename-x")
    name = con.execute("SELECT name FROM entities WHERE id = ?", (ent,)).fetchone()
    assert name[0] == "Apple"
    kinds = dict(
        con.execute(
            "SELECT label || '/' || coalesce(lang, ''), kind FROM entity_labels"
            " WHERE entity_id = ?",
            (ent,),
        ).fetchall()
    )
    assert kinds.get("APPLE/en") == "alt" and kinds.get("apple/de") == "alt"


# 6 -------------------------------------------------------------------------


class _Claude:
    """A Claude client whose answers are given in turn; an exception in the
    list is raised in its turn."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = answers
        self.messages = self

    def create(self, **params: Any) -> Any:
        got = self.answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return got


def _message(text: str, stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=text)] if text else [],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=50),
    )


def test_a_refused_extraction_is_paid_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal or an answer cut short is billed: what it used goes home
    with the error, where it was never counted."""
    from prax.graph import extraction

    ex = extraction.ClaudeExtractor(
        model="claude-sonnet-5", client=_Claude([_message("", stop="refusal")])
    )
    monkeypatch.setattr(ex, "params", lambda doc: {})
    usage.clear()
    with pytest.raises(RuntimeError, match="refused"):
        ex.extract(None)  # type: ignore[arg-type]
    assert usage.take() == {
        "claude-sonnet-5": {"input_tokens": 1000, "output_tokens": 50}
    }


def test_the_batches_before_an_adjudicators_failure_are_paid_for() -> None:
    from prax.graph import resolution

    answer = json.dumps({"answers": [{"pair": 1, "same": True}]})
    judge = resolution.ClaudeAdjudicator(
        model="claude-sonnet-5",
        client=_Claude([_message(answer), RuntimeError("overloaded")]),
        batch=1,
    )
    pair = resolution.Candidate(1, 2, "A", "a", "concept", "likely", 0.9)
    usage.clear()
    with pytest.raises(RuntimeError, match="overloaded"):
        judge.decide([pair, pair])
    assert usage.take() == {
        "claude-sonnet-5": {"input_tokens": 1000, "output_tokens": 50}
    }


# 7 -------------------------------------------------------------------------


def test_two_versions_of_one_title_keep_their_facts(con: sqlite3.Connection) -> None:
    """Two documents of one title are one node, kept on one of them; an ask
    bundle kept only the facts whose node is kept on their own document,
    and the other version came with none."""
    docs = []
    for n, author in ((1, "Ada"), (2, "Grace")):
        doc = _doc(con, f"Wavelets, version {n}. " * 20, "Wavelets")
        store.link(
            con,
            store.Edge("Wavelets", "paper", "authored_by", author, "person"),
            source_doc=doc,
            producer="test",
        )
        docs.append(doc)
    facts = store.document_facts(con, docs)
    assert [f["name"] for f in facts[docs[0]]] == ["Ada"]
    assert [f["name"] for f in facts[docs[1]]] == ["Grace"]


# 8 -------------------------------------------------------------------------


def test_a_run_that_ends_its_own_edges_is_restored_twice(
    con: sqlite3.Connection,
) -> None:
    """A sync's links run writes its edges and ends its older ones under one
    name; an undo states the older ones again under that name, and a
    second undo ended them for good."""
    doc = _doc(con, "Notes that link to the plan. " * 20, "Notes")
    old = store.link(
        con,
        store.Edge("Notes", "paper", "links_to", "Plan", "paper"),
        source_doc=doc,
        producer="sync",
        run="links:lab",
    )
    store.end_edges(con, [old], run="links:lab")
    store.link(
        con,
        store.Edge("Notes", "paper", "links_to", "Plan v2", "paper"),
        source_doc=doc,
        producer="sync",
        run="links:lab",
    )
    store.restore_run(con, "links:lab")
    live = lambda: sorted(
        r[0]
        for r in con.execute(
            "SELECT t.name FROM edges x JOIN entities t ON t.id = x.dst"
            " WHERE x.source_doc = ? AND x.valid_to IS NULL",
            (doc,),
        )
    )
    assert live() == ["Plan"]
    store.restore_run(con, "links:lab")
    assert live() == ["Plan"]


# 11 ------------------------------------------------------------------------


def test_a_title_filter_folds_case_beyond_ascii(con: sqlite3.Connection) -> None:
    """SQLite folds the case of ASCII alone: "über" never found "Über"."""
    doc = _doc(con, "A note on the weather. " * 20, "Über das Wetter")
    for words in ("über", "ÜBER", "wetter"):
        clause, args = store.title_contains(words)
        got = con.execute(f"SELECT id FROM documents d WHERE {clause}", args)
        assert [r[0] for r in got] == [doc], words


# 13 ------------------------------------------------------------------------


def test_a_repeated_equation_keeps_each_ones_reading() -> None:
    """A replacing read carries each occurrence's reading to the same
    occurrence of the new text: by the equation alone, both went under the
    first, or the second was lost."""
    from prax.parsers import annotate

    eq = "$$E = mc^2$$"
    first = "*Formula, as read by m:* energy, at the start."
    second = "*Formula, as read by m:* energy again, in the proof."
    old = f"# A\n\n{eq}\n{first}\n\nText.\n\n{eq}\n{second}\n"
    new = f"# A\n\n{eq}\n\nText, read again.\n\n{eq}\n"
    got = annotate.carry(old, new)
    assert got == f"# A\n\n{eq}\n{first}\n\nText, read again.\n\n{eq}\n{second}\n"


# 15 ------------------------------------------------------------------------


def test_a_walk_answers_at_most_its_cap() -> None:
    """``/traverse``'s limit went to the store as given, and 0 (the graph
    view's whole first hop) answered a hub's 13,440 edges; 0 is now the
    most, and a larger number is clamped to it."""
    from prax.api._base import AtMost
    from prax.api.graph import TRAVERSE_MOST

    cap = AtMost(TRAVERSE_MOST, zero_is_most=True)
    assert cap._clamp(0) == cap._clamp(10**6) == TRAVERSE_MOST
    assert cap._clamp(5) == 5
    assert AtMost(10)._clamp(0) == 1
