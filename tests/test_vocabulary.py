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
    assert "Gauß-Elimination" in vocabulary.system()
    assert "Büchi" in vocabulary.system()


def test_the_prompt_names_the_library_s_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """English by default, and a setting rather than a constant: a French
    library normalizes to French."""
    assert "English" in vocabulary.system()
    monkeypatch.setenv("PRAX_GRAPH_LANGUAGE", "fr")
    assert "French" in vocabulary.system()
    assert "English" not in vocabulary.system()


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


def test_a_name_with_words_only_taken_out_is_refused() -> None:
    """The candidate net catches rare English names too, and the model
    was told to repeat them. One came back shortened instead."""
    runtime = Runtime({"outdoor travel health insurance": "travel health insurance"})
    assert (
        vocabulary.rename(runtime, "outdoor travel health insurance", "concept") is None
    )
    assert (
        vocabulary.acceptable(
            "travel health insurance", "outdoor travel health insurance"
        )
        == "only words taken out"
    )
    # a real translation shares no words with what it replaces, or adds some
    assert vocabulary.acceptable("white beans", "Weiße Bohnen") is None
    assert vocabulary.acceptable("trapezoidal rule", "Trapez rule") is None


def test_a_rename_is_taken_back_with_the_run(client: TestClient) -> None:
    """A rename is a claim like a merge; taking back only the merges
    would leave the wrong names behind."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Seitentabelle", "concept")
    store.name_in_english(con, eid, "side table", run="r5")
    assert (
        con.execute("SELECT name FROM entities WHERE id = ?", (eid,)).fetchone()["name"]
        == "side table"
    )
    assert store.unmerge_run(con, "r5") == 1
    assert (
        con.execute("SELECT name FROM entities WHERE id = ?", (eid,)).fetchone()["name"]
        == "Seitentabelle"
    )
    # what the run wrote is gone, the name the entity always had is the
    # preferred one again, and the name it briefly carried is kept as one
    # it answered to — a name this design drops is one nothing can recover
    left = {ln["label"]: ln["kind"] for ln in store.entity_labels(con, eid)}
    assert left == {"Seitentabelle": "pref", "side table": "alt"}


def test_the_twin_is_one_of_its_own_type_that_is_still_standing(
    client: TestClient,
) -> None:
    """The same name can be several entities of several types. Taking
    whichever came first made `page table` the concept answer for `page
    table` the method, and turned a fold into a clash."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Seitentabelle", "method")
    concept = _german_entity(con, client, "page table", "concept")
    method = _german_entity(con, client, "page table", "method")
    assert concept < method  # the wrong one comes first by id
    got = store.name_in_english(con, eid, "page table", run="r6")
    assert got["action"] == "merged" and got["into"] == method


def test_a_name_the_entity_already_answers_to_is_not_asked_again(
    client: TestClient,
) -> None:
    """An earlier pass had folded `reachability graph` into
    `Erreichbarkeitsgraph`, so the English name was already this entity's
    own. The outcome wrote nothing, and 62 entities were asked about on
    every pass for ever."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Erreichbarkeitsgraph", "concept")
    other = _german_entity(con, client, "reachability graph", "concept")
    store.merge_entities(con, other, eid, producer="earlier", run="r0")

    got = store.name_in_english(con, eid, "reachability graph", run="r7")
    assert got["action"] == "already"
    assert store.foreign_names(con) == []


def test_the_fold_lands_on_the_survivor_not_the_twin(client: TestClient) -> None:
    """A twin of this entity's own type may itself have been folded into
    one of another type. Comparing the twin let that through to
    merge_entities, which refused it — and the worker's summary line does
    not print errors, so 62 entities failed silently."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Erreichbarkeitsgraph", "concept")
    twin = _german_entity(con, client, "reachability graph", "concept")
    survivor = _german_entity(con, client, "reachability graph too", "method")
    store.merge_entities(con, twin, survivor, across_types=True, run="r0")

    got = store.name_in_english(con, eid, "reachability graph", run="r8")
    assert got["action"] == "type clash"  # not a refusal, and not a bad fold
    assert got["twin"] == survivor
    # and it is not asked again (the third entity of the fixture is its
    # own candidate, which is right)
    assert eid not in {r["id"] for r in store.foreign_names(con)}


# ------------------------------------------- one name per thing, afterwards


def test_a_name_the_graph_knows_lands_on_the_entity_that_owns_it(
    client: TestClient,
) -> None:
    """The payoff of the pass. A merge leaves the old name on an entity
    of its own, which `traverse` resolves; a *rename* does not, so
    without this the next German recipe would make `Knoblauchzehen`
    again and the split would start over."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    store.name_in_english(con, eid, "garlic cloves", run="r9")
    assert not con.execute(
        "SELECT 1 FROM entities WHERE name = ?", ("Knoblauchzehen",)
    ).fetchone()  # nothing carries the old name now

    again = store.link(
        con,
        store.Edge("Rezept", "recipe", "calls_for", "Knoblauchzehen", "ingredient"),
        source_doc=None,
        producer="test",
    )
    assert again
    row = con.execute("SELECT dst FROM edges WHERE id = ?", (again,)).fetchone()
    assert row["dst"] == eid  # the entity that answers to that name


def test_a_label_shared_by_two_entities_of_one_type_is_a_question(
    client: TestClient,
) -> None:
    """Two entities of one type answering to a name is not resolved by
    taking the lower id: a new entity is made and the pass can decide."""
    con = client.app.state.con
    a = _german_entity(con, client, "first", "concept")
    b = _german_entity(con, client, "second", "concept")
    for eid in (a, b):
        store.add_label(con, eid, "shared name", lang="de", producer="test", run="r0")
    store.link(
        con,
        store.Edge("Rezept", "document", "about", "shared name", "concept"),
        source_doc=None,
        producer="test",
    )
    made = con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = 'concept'", ("shared name",)
    ).fetchone()
    assert made is not None and made["id"] not in (a, b)


def test_one_preferred_name_per_language(client: TestClient) -> None:
    con = client.app.state.con
    eid = _german_entity(con, client, "Olivenöl", "ingredient")
    store.add_label(con, eid, "olive oil", lang="en", kind="pref", run="r0")
    store.add_label(con, eid, "olive-oil", lang="en", kind="pref", run="r1")
    prefs = con.execute(
        "SELECT label FROM entity_labels WHERE entity_id = ? AND lang = 'en'"
        " AND kind = 'pref'",
        (eid,),
    ).fetchall()
    assert [r["label"] for r in prefs] == ["olive-oil"]  # the later one wins
    # and the one it displaced is still a name the entity answers to
    assert eid in store.entities_by_label(con, "olive oil")


def test_a_rename_leaves_a_preferred_name_in_each_language(
    client: TestClient,
) -> None:
    """One preferred label *per language*, which is the shape SKOS gives
    a concept: the document's own word stays the preferred German name
    and the translation becomes the preferred English one. Only the
    display follows the host's language."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    store.name_in_english(con, eid, "garlic cloves", run="r10")
    rows = con.execute(
        "SELECT label, lang, kind, was FROM entity_labels WHERE entity_id = ?",
        (eid,),
    ).fetchall()
    by_label = {r["label"]: r for r in rows}
    assert by_label["Knoblauchzehen"]["lang"] == "de"
    assert by_label["Knoblauchzehen"]["kind"] == "pref"  # preferred, in German
    assert by_label["Knoblauchzehen"]["was"] == 1  # and what the entity was called
    assert by_label["garlic cloves"]["lang"] == "en"
    assert by_label["garlic cloves"]["kind"] == "pref"
    assert (
        con.execute("SELECT name FROM entities WHERE id = ?", (eid,)).fetchone()["name"]
        == "garlic cloves"
    )


# ------------------------------------------ the names a search has to reach


def test_a_renamed_entity_is_found_by_the_word_the_document_used(
    client: TestClient,
) -> None:
    """522 names reached nothing on 2026-09-24: the pass renamed them and
    the entity search read only `entities.name`."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    store.name_in_english(con, eid, "garlic cloves", run="r11")

    hits = store.find_entities(con, "Knoblauchzehen")
    assert [h["id"] for h in hits] == [eid]
    assert hits[0]["name"] == "garlic cloves"  # what it is called now
    assert hits[0]["as"] == "Knoblauchzehen"  # and what matched
    # the English name matches without an explanation
    assert "as" not in store.find_entities(con, "garlic")[0]


def test_a_walk_starts_from_a_name_the_entity_is_known_by(
    client: TestClient,
) -> None:
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    store.name_in_english(con, eid, "garlic cloves", run="r12")
    walked = store.traverse(con, "Knoblauchzehen", hops=1)
    assert walked
    assert any(
        e["dst"] == "garlic cloves" or e["src"] == "garlic cloves" for e in walked
    )


def test_a_merged_name_is_found_under_the_survivor(client: TestClient) -> None:
    con = client.app.state.con
    german = _german_entity(con, client, "Olivenöl", "ingredient")
    english = _german_entity(con, client, "olive oil", "ingredient")
    store.name_in_english(con, german, "olive oil", run="r13")
    hits = store.find_entities(con, "Olivenöl")
    assert [h["id"] for h in hits] == [english]


def test_the_rename_label_carries_the_document_s_language(
    client: TestClient,
) -> None:
    """A two-word name is under the detector's floor, so the document that
    named it says which language it was."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    store.name_in_english(con, eid, "garlic cloves", run="r14")
    row = con.execute(
        "SELECT lang FROM entity_labels WHERE entity_id = ? AND label = ?",
        (eid, "Knoblauchzehen"),
    ).fetchone()
    assert row["lang"] == "de"


def test_a_name_with_an_umlaut_is_found_whatever_the_case(
    client: TestClient,
) -> None:
    """SQLite's own lower() is ASCII-only, so `lower(name) LIKE
    '%ästhetik%'` never matched "Ästhetik der Lüge" — in a library a
    fifth of which is German, silently."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Ästhetik der Lüge", "concept")
    for q in ("Ästhetik der Lüge", "ästhetik", "ÄSTHETIK", "der lüge"):
        assert [h["id"] for h in store.find_entities(con, q)] == [eid], q


def test_a_label_with_an_umlaut_is_found_too(client: TestClient) -> None:
    con = client.app.state.con
    eid = _german_entity(con, client, "Übertragung", "concept")
    store.name_in_english(con, eid, "transmission", run="r15")
    assert [h["id"] for h in store.find_entities(con, "übertragung")] == [eid]


# ------------------------------------------- the dictionary it leaves behind


def test_a_query_reaches_the_english_name_through_the_graph(
    client: TestClient,
) -> None:
    """The vocabulary pass leaves a dictionary: Olivenöl is a label of
    the entity called olive oil, with a document behind the pair. That is
    what the German keyword half needs, and nothing had to be built."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    store.name_in_english(con, eid, "garlic cloves", run="r16")

    assert store.known_as(con, "Knoblauchzehen") == ["garlic cloves"]
    terms = store.expand_query(con, "Knoblauchzehen")
    assert terms == [["knoblauchzehen", "garlic cloves"]]


def test_a_name_of_several_words_is_looked_up_whole(client: TestClient) -> None:
    con = client.app.state.con
    eid = _german_entity(con, client, "dünn besetzte Matrizen", "concept")
    store.name_in_english(con, eid, "sparse matrix", run="r17")
    terms = store.expand_query(con, "dünn besetzte Matrizen")
    # no single token is the name, so the whole query is a term of its own
    assert ["sparse matrix"] in terms


def test_a_query_that_names_nothing_is_unchanged(client: TestClient) -> None:
    con = client.app.state.con
    assert store.expand_query(con, "reverberation time") == [
        ["reverberation"],
        ["time"],
    ]


def test_the_library_is_not_its_own_evidence(client: TestClient) -> None:
    """A briefing page of prax's own that says "Olivenöl sits beside
    olive oil" is an English document containing the German word, and it
    took Olivenöl out of the net that would have folded it."""
    con = client.app.state.con
    client.post("/ingest", json={"text": GERMAN, "title": "Rezept"})
    assert not vocabulary.in_english_text(con, "Olivenöl")
    store.write_page(
        con,
        slug="what-arrived",
        title="What arrived",
        text="Olivenöl sits beside olive oil in the graph. " * 8,
        kind="topic",
    )
    meta_doc = con.execute(
        "SELECT doc_id FROM pages WHERE slug = 'what-arrived'"
    ).fetchone()
    meta = store.get_meta(con, meta_doc["doc_id"])
    meta["lang"] = "en"
    store.set_meta(con, meta_doc["doc_id"], meta)
    assert not vocabulary.in_english_text(con, "Olivenöl")


# ------------------------------------ the name as a cache of the labels


def test_every_entity_answers_to_its_own_name(client: TestClient) -> None:
    """Migration 23 and `_entity_id`: without this the labels are not the
    whole truth and the name cannot be rebuilt from them."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    labels = store.entity_labels(con, eid)
    assert [(ln["label"], ln["kind"]) for ln in labels] == [("Knoblauchzehen", "pref")]


def test_a_name_appears_once_per_entity(client: TestClient) -> None:
    """Its language is a property of the label, not part of which label
    it is — two rows differing only in `lang` collided later."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    store.add_label(con, eid, "Knoblauchzehen", lang="de", kind="alt", run="r")
    rows = con.execute(
        "SELECT label, lang FROM entity_labels WHERE entity_id = ?", (eid,)
    ).fetchall()
    assert [(r["label"], r["lang"]) for r in rows] == [("Knoblauchzehen", "de")]


def test_the_shown_name_follows_the_preferred_label(client: TestClient) -> None:
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    store.add_label(con, eid, "garlic cloves", lang="en", kind="pref", run="r")
    assert (
        con.execute("SELECT name FROM entities WHERE id = ?", (eid,)).fetchone()["name"]
        == "garlic cloves"
    )


def test_the_host_chooses_which_name_it_shows(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The graph in German for a German reader, one node either way."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    store.add_label(con, eid, "Knoblauchzehen", lang="de", kind="pref", run="r")
    store.add_label(con, eid, "garlic cloves", lang="en", kind="pref", run="r")
    assert con.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone()[
        0
    ] == ("garlic cloves")

    monkeypatch.setenv("PRAX_GRAPH_LANGUAGE", "de")
    got = store.rename_display_language(con)
    assert got["language"] == "de" and got["renamed"] >= 1
    assert con.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone()[
        0
    ] == ("Knoblauchzehen")
    # and it is the same entity either way
    assert store.entities_by_label(con, "garlic cloves") == [eid]


def test_a_name_another_entity_shows_is_not_taken(client: TestClient) -> None:
    """Two things may not display the same, which is the type clash the
    review queue is for — not something to resolve by overwriting."""
    con = client.app.state.con
    a = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    b = _german_entity(con, client, "garlic cloves", "ingredient")
    store.add_label(con, a, "garlic cloves", lang="en", kind="pref", run="r")
    assert (
        con.execute("SELECT name FROM entities WHERE id = ?", (a,)).fetchone()["name"]
        == "Knoblauchzehen"
    )
    assert b  # the one that already shows it keeps it


def test_the_maintain_pass_rebuilds_the_names(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    store.add_label(con, eid, "Knoblauchzehen", lang="de", kind="pref", run="r")
    store.add_label(con, eid, "garlic cloves", lang="en", kind="pref", run="r")
    monkeypatch.setenv("PRAX_GRAPH_LANGUAGE", "de")
    got = store.maintain(con, only=["names"])["names"]
    assert got["language"] == "de"
    assert (
        con.execute("SELECT name FROM entities WHERE id = ?", (eid,)).fetchone()["name"]
        == "Knoblauchzehen"
    )


def test_a_name_that_is_replaced_is_recorded_before_it_goes(
    client: TestClient,
) -> None:
    """Migration 23 skipped the entities that already had a preferred
    label, so their own name was in no row and the first rebuild renamed
    four of them with nothing left to put back — `Chomsky-Normalform`
    among them. A guard would have caught that one path; recording the
    outgoing name makes the loss impossible on all of them."""
    con = client.app.state.con
    eid = _german_entity(con, client, "Chomsky-Normalform", "concept")
    con.execute("DELETE FROM entity_labels WHERE entity_id = ?", (eid,))  # pre-24
    store.add_label(con, eid, "Chomsky normal form", lang="en", kind="pref", run="r")
    assert (
        con.execute("SELECT name FROM entities WHERE id = ?", (eid,)).fetchone()["name"]
        == "Chomsky normal form"
    )
    # and the name it used to have is still a name it answers to
    assert store.entities_by_label(con, "Chomsky-Normalform") == [eid]


def test_the_corpus_ruling_is_remembered(client: TestClient) -> None:
    """The third condition costs an FTS lookup a name, and on an
    exhausted queue it was paid for every candidate on every ask: 97
    seconds to answer "nothing". The ruling is monotone — a name in an
    English document will always be in one — so it is kept, and the
    second ask took 0.7."""
    con = client.app.state.con
    client.post("/ingest", json={"text": ENGLISH, "title": "Pasta"})
    eid = _german_entity(con, client, "olive oil", "ingredient")

    assert store.foreign_names(con) == []  # ruled out by the corpus
    row = con.execute(
        "SELECT producer FROM entity_labels WHERE entity_id = ? AND label = ?",
        (eid, "olive oil"),
    ).fetchone()
    assert row["producer"] == "vocabulary:corpus"
    # and it is not asked about again, so the pass converges
    assert store.foreign_names(con) == []


def test_a_ruling_does_not_overwrite_a_pass_s_own_word(client: TestClient) -> None:
    con = client.app.state.con
    eid = _german_entity(con, client, "Knoblauchzehen", "ingredient")
    store.name_in_english(con, eid, "garlic cloves", run="r20")
    store.foreign_names(con)
    row = con.execute(
        "SELECT producer FROM entity_labels WHERE entity_id = ? AND label = ?",
        (eid, "garlic cloves"),
    ).fetchone()
    assert row["producer"] == "vocabulary"  # not the corpus's marker
