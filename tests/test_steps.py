"""A step is one object: what the door hands out, what the worker does
with it, what the door takes in (``prax.steps``).

The three used to be three ``if step == …`` chains in two modules, and a
new pass was three edits in the same order with the same shape
(docs/audit/engineering-2026-09-25.md, finding 1). The worker passes of
the three model steps that no test drove through ``run_once`` are pinned
here too, so the move has something to keep.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import models, steps, store, work, worker
from prax.capture import pipeline
from prax.writing import summaries

DE = (
    "Dieses Dokument ist ein Verzeichnis von Herstellern und Zulieferern für"
    " Elektrofahrräder, mit den Anschriften der Firmen und den Ansprechpartnern"
    " im Vertrieb. Es nennt auch die Komponenten, die jeder Hersteller selbst"
    " baut, und die, die er von anderen bezieht."
)
EN = (
    "This document is a directory of electric bicycle manufacturers and"
    " component suppliers, with company addresses and the people to ask in"
    " sales. It also says which components each maker builds itself and which"
    " it buys in from others."
)


class Runtime:
    """A model that answers with one line, whatever it is asked."""

    name = "stub"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, Any]]:
        self.asked.append(user)
        return self.answer, {"input_tokens": 10, "output_tokens": 3}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_EXTRACT", "stub")
    monkeypatch.setenv("PRAX_TITLES", "none")
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _door(client: TestClient) -> worker.Door:
    return worker.Door("http://testserver", client=client, name="test-worker")


def _answer(monkeypatch: pytest.MonkeyPatch, text: str) -> Runtime:
    rt = Runtime(text)
    monkeypatch.setattr(models, "runtime", lambda spec: rt)
    return rt


# ------------------------------------------------------------ the object


def test_every_step_is_one_registered_object() -> None:
    for name in steps.STEPS:
        step = steps.get(name)
        assert step.name == name
        for part in ("hand_out", "take_in", "run"):
            assert callable(getattr(step, part)), (name, part)


def test_an_unknown_step_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="unknown step"):
        steps.get("nonsense")


def test_the_dispatchers_hold_no_chain_of_steps() -> None:
    """What keeps the chains from growing back: the door's hand-out and
    take-in and the worker's pass name no step."""
    chained = re.compile(r"""step\s*(==|in)\s*\(?["']""")
    for fn in (work.hand_out, work.take_in, worker.run_once):
        src = inspect.getsource(fn)
        assert not chained.search(src), fn.__qualname__


# --------------------------- the worker passes no test drove end to end


def test_the_summaries_pass_translates_through_the_door(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    monkeypatch.setenv("PRAX_SUMMARIES", "stub")
    doc = client.post(
        "/ingest", json={"text": "ein Text " * 80, "title": "Hersteller"}
    ).json()["doc_id"]
    meta = store.get_meta(con, doc)
    summaries.keep(meta, DE)
    store.set_meta(con, doc, meta)
    _answer(monkeypatch, EN)
    out = worker.run_once(
        _door(client), steps=("summaries",), scope="all", log_=lambda t: None
    )
    assert out["summaries"] == "1 translated, 0 left"
    assert store.get_meta(con, doc)["summary"] == EN


def test_the_sections_pass_reads_a_book_through_the_door(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    monkeypatch.setenv("PRAX_SECTIONS", "stub")
    body = "\n\n".join(
        f"# Chapter {i + 1}\n\n" + f"Some prose about topic {i}. " * 1000
        for i in range(3)
    )
    doc = client.post("/ingest", json={"text": body, "title": "Book"}).json()["doc_id"]
    _answer(monkeypatch, "Delay lines and the algorithms built on them.")
    out = worker.run_once(
        _door(client), steps=("sections",), scope="all", log_=lambda t: None
    )
    assert out["sections"] == "1 documents, 3 sections"
    assert len(store.get_meta(con, doc)["sections"]["items"]) == 3


def test_the_vocabulary_pass_names_through_the_door(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    monkeypatch.setenv("PRAX_VOCABULARY", "stub")
    doc = client.post(
        "/ingest",
        json={"text": "Dieses Rezept braucht Knoblauchzehen. " * 8, "title": "R"},
    ).json()["doc_id"]
    meta = store.get_meta(con, doc)
    meta["lang"] = "de"
    store.set_meta(con, doc, meta)
    store.link(
        con,
        store.Edge("R", "recipe", "calls_for", "Knoblauchzehen", "ingredient"),
        confidence="EXTRACTED",
        source_doc=doc,
        producer="test",
        run="test",
    )
    _answer(monkeypatch, "garlic cloves")
    out = worker.run_once(_door(client), steps=("vocabulary",), log_=lambda t: None)
    assert out["vocabulary"] == "1 named {'renamed': 1}"
    assert store.entities_by_label(con, "Knoblauchzehen")


def test_a_paid_model_step_fetches_nothing_it_will_not_do(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four local-model passes checked the price after the batch was
    leased, and the batch then sat leased for fifteen minutes."""
    con = client.app.state.con
    doc = client.post(
        "/ingest", json={"text": "ein Text " * 80, "title": "Hersteller"}
    ).json()["doc_id"]
    meta = store.get_meta(con, doc)
    summaries.keep(meta, DE)
    store.set_meta(con, doc, meta)
    paid = models.ModelSpec(name="sonnet", kind="claude", model="claude-sonnet-5")
    monkeypatch.setattr(models, "resolve", lambda s: paid if s == "summaries" else None)
    out = worker.run_once(
        _door(client), steps=("summaries",), scope="all", log_=lambda t: None
    )
    assert out["summaries"].startswith("skipped") and "paid" in out["summaries"]
    assert not any(s == "summaries" for s, _ in work._leases)


def test_a_deferred_entity_is_held_like_a_deferred_document(
    client: TestClient,
) -> None:
    """The vocabulary's items are entities, keyed ``id``: a model server
    that was not ready made the door read a ``doc_id`` that was not there."""
    con = client.app.state.con
    rep = work.take_in(con, "vocabulary", {"results": [{"id": 7, "defer": True}]})
    assert rep["deferred"] == 1
    assert ("vocabulary", 7) in work._leases


# ----------------------------------------- a title many documents share, stage H


class Headings:
    """A titles model that answers with the document's first heading."""

    name = "stub-titles"

    def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, Any]]:
        heading = next(
            (
                ln.split(":", 1)[1].strip()
                for ln in user.splitlines()
                if ln.startswith("First heading in the text:")
            ),
            "",
        )
        return heading, {}


def test_a_title_many_documents_share_is_replaced_and_read_again(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """118 NIME papers carried their volume's name as their title, and the
    one entity it named held all their facts."""
    con = client.app.state.con
    volume = "Proceedings of a Conference on New Interfaces 2011"
    docs = []
    for name in ("Nuvolet", "Tangible Loops", "Death and the Powers"):
        text = (
            f"{volume}\n\n# {name}: an interface\n\n"
            + f"The {name} system is described here in some detail. " * 30
        )
        doc = client.post("/ingest", json={"text": text, "title": volume}).json()
        docs.append(int(doc["doc_id"]))
    alone = client.post(
        "/ingest",
        json={
            "text": "# A title of its own\n\n" + "Other words. " * 60,
            "title": "Own",
        },
    ).json()["doc_id"]
    needed = dict(pipeline.titles_needed(con, untried_only=True, reasons=("shared",)))
    assert set(needed) == set(docs) and alone not in needed
    spec = models.ModelSpec(
        name="server", kind="openai", base_url="http://127.0.0.1:1/v1", model="t"
    )
    monkeypatch.setattr(models, "resolve", lambda s: spec if s == "titles" else None)
    monkeypatch.setattr(models, "runtime", lambda s: Headings())
    out = worker.run_once(
        _door(client), steps=("titles",), scope="all", log_=lambda t: None
    )
    assert out["titles"].startswith("3 retitled")
    for d in docs:
        meta = store.get_meta(con, d)
        assert meta["title_history"][-1]["title"] == volume
        assert meta["extraction_stale"]["requested"]
    assert store.shared_titles(con) == set()
