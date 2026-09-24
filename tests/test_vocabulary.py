"""One name per thing: the candidate net, the model's answer, and what
the door does with it."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import store, vocabulary, work, worker

GERMAN = "Dieses Rezept braucht Olivenöl und Knoblauch für die Pfanne. " * 6
ENGLISH = "This recipe needs olive oil and garlic for the pan. " * 6


class Runtime:
    """A model that answers from a table, and repeats what it does not
    know — which is what the real prompt asks for."""

    name = "stub"

    def __init__(self, table: dict[str, str] | None = None) -> None:
        self.table = table or {}
        self.asked: list[str] = []

    def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, Any]]:
        self.asked.append(user)
        for given, answer in self.table.items():
            if given in user:
                return answer, {"input_tokens": 8, "output_tokens": 4}
        return "", {"input_tokens": 8, "output_tokens": 1}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    work._leases.clear()
    from prax.api import app

    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------ the answer


def test_a_name_comes_back_as_a_name() -> None:
    runtime = Runtime({"Olivenöl": "olive oil"})
    got = vocabulary.rename(runtime, "Olivenöl", "ingredient", context="Pastarezept")
    assert got is not None and got.changed
    assert got.name == "olive oil"
    assert "ingredient" in runtime.asked[0]
    assert "Pastarezept" in runtime.asked[0]


def test_a_preamble_and_a_full_stop_come_off() -> None:
    assert vocabulary.parse('The English name is "olive oil".') == "olive oil"
    assert vocabulary.parse("olive oil\nbecause …") == "olive oil"


def test_a_name_already_english_is_kept() -> None:
    runtime = Runtime({"extendible hashing": "extendible hashing"})
    got = vocabulary.rename(runtime, "extendible hashing", "concept")
    assert got is not None and not got.changed
    assert got.name == "extendible hashing"


def test_a_model_that_says_nothing_changes_nothing() -> None:
    got = vocabulary.rename(Runtime(), "Olivenöl", "ingredient")
    assert got is not None and not got.changed


def test_a_definition_instead_of_a_name_is_refused() -> None:
    runtime = Runtime({"Olivenöl": "an oil pressed from olives used in cooking"})
    assert vocabulary.rename(runtime, "Olivenöl", "ingredient") is None


def test_the_prompt_keeps_a_persons_name() -> None:
    assert "Gauß-Elimination" in vocabulary.SYSTEM
    assert "Büchi" in vocabulary.SYSTEM


# ------------------------------------------------- the library as dictionary


def test_a_name_an_english_document_uses_is_not_a_candidate(
    client: TestClient,
) -> None:
    con = client.app.state.con
    client.post("/ingest", json={"text": ENGLISH, "title": "Pasta"})
    assert vocabulary.in_english_text(con, "olive oil")
    assert not vocabulary.in_english_text(con, "Olivenöl")


def test_a_name_only_a_german_document_uses_is_a_candidate(
    client: TestClient,
) -> None:
    con = client.app.state.con
    client.post("/ingest", json={"text": GERMAN, "title": "Rezept"})
    assert not vocabulary.in_english_text(con, "Olivenöl")


def test_a_sentence_is_not_a_name(client: TestClient) -> None:
    con = client.app.state.con
    assert vocabulary.in_english_text(con, " ".join(["wort"] * 12))


# --------------------------------------------------------- the step at work


def _german_entity(con: Any, client: TestClient, name: str, etype: str) -> int:
    """An entity of ``etype`` named in a German document, with an edge."""
    doc_id = client.post("/ingest", json={"text": GERMAN, "title": "Rezept"}).json()[
        "doc_id"
    ]
    meta = store.get_meta(con, doc_id)
    meta["lang"] = "de"
    store.set_meta(con, doc_id, meta)
    edge = (
        store.Edge("Rezept", "recipe", "calls_for", name, etype)
        if etype == "ingredient"
        else store.Edge("Rezept", "document", "about", name, etype)
        if etype in ("concept", "method")
        else store.Edge("Rezept", "document", "authored_by", name, etype)
    )
    store.link(
        con,
        edge,
        confidence="EXTRACTED",
        source_doc=doc_id,
        producer="test",
        run="test",
    )
    row = con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ?", (name, etype)
    ).fetchone()
    return int(row["id"])


def test_only_a_common_type_is_offered(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    ingredient = _german_entity(con, client, "Olivenöl", "ingredient")
    _german_entity(con, client, "Süddeutsche Zeitung", "organization")
    offered = {r["id"] for r in store.foreign_names(con)}
    assert ingredient in offered
    # an organization names one particular thing: never translated
    names = {r["name"] for r in store.foreign_names(con)}
    assert "Süddeutsche Zeitung" not in names


def test_renamed_when_nobody_else_has_the_name(client: TestClient) -> None:
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    got = store.name_in_english(con, eid, "garlic cloves", run="r1")
    assert got["action"] == "renamed"
    row = con.execute("SELECT name FROM entities WHERE id = ?", (eid,)).fetchone()
    assert row["name"] == "garlic cloves"
    # the document's own word still reaches it
    assert store.entities_by_label(con, "Knoblauchzehen") == [eid]


def test_merged_when_the_english_entity_is_already_there(client: TestClient) -> None:
    con = client.app.state.con
    eid = _german_entity(con, client, "Olivenöl", "ingredient")
    twin = _german_entity(con, client, "olive oil", "ingredient")
    got = store.name_in_english(con, eid, "olive oil", run="r2")
    assert got["action"] == "merged" and got["into"] == twin
    row = con.execute(
        "SELECT canonical_id FROM entities WHERE id = ?", (eid,)
    ).fetchone()
    assert row["canonical_id"] == twin
    assert eid in store.entities_by_label(con, "Olivenöl") or store.entities_by_label(
        con, "Olivenöl"
    ) == [twin]


def test_a_type_clash_is_a_question_not_a_fold(client: TestClient) -> None:
    """Olivenöl is an ingredient where olive oil is a concept: which of
    the two is right is the review queue's business."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Olivenöl", "ingredient")
    _german_entity(con, client, "olive oil", "concept")
    before = store.count_review(con)
    got = store.name_in_english(con, eid, "olive oil", run="r3")
    assert got["action"] == "type clash"
    row = con.execute(
        "SELECT name, canonical_id FROM entities WHERE id = ?", (eid,)
    ).fetchone()
    assert row["name"] == "Olivenöl" and row["canonical_id"] is None
    assert store.count_review(con) == before + 1


def test_a_round_can_be_taken_back(client: TestClient) -> None:
    con = client.app.state.con
    eid = _german_entity(con, client, "Olivenöl", "ingredient")
    twin = _german_entity(con, client, "olive oil", "ingredient")
    store.name_in_english(con, eid, "olive oil", run="r4")
    assert store.unmerge_run(con, "r4") == 1
    row = con.execute(
        "SELECT canonical_id FROM entities WHERE id = ?", (eid,)
    ).fetchone()
    assert row["canonical_id"] is None
    assert twin  # still there, on its own


def test_an_entity_the_pass_has_seen_is_not_offered_twice(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prax import models

    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    monkeypatch.setenv("PRAX_VOCABULARY", "stub")
    batch = client.get("/work/vocabulary").json()
    assert [i["id"] for i in batch["items"]] == [eid]
    assert batch["items"][0]["type"] == "ingredient"
    assert models  # the step is on

    results = worker.do_vocabulary(
        batch["items"], Runtime({"Knoblauchzehen": "garlic"})
    )
    rep = client.post("/work/vocabulary", json={"results": results}).json()
    assert rep["applied"] == 1 and rep["actions"] == {"renamed": 1}

    work._leases.clear()
    assert client.get("/work/vocabulary").json()["items"] == []


def test_a_name_kept_as_english_is_not_asked_again(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    _german_entity(con, client, "extendible hashing", "concept")
    monkeypatch.setenv("PRAX_VOCABULARY", "stub")
    batch = client.get("/work/vocabulary").json()
    results = worker.do_vocabulary(batch["items"], Runtime())  # says nothing
    rep = client.post("/work/vocabulary", json={"results": results}).json()
    assert rep["actions"] == {"kept": 1}
    work._leases.clear()
    assert client.get("/work/vocabulary").json()["items"] == []
