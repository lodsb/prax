"""The summary's language: the ontology's naming split, the translation
and what the store keeps of it."""

from __future__ import annotations

from typing import Any

import pytest

from prax import ontology, summaries

# two summaries of the length a real one has: the detector says nothing
# about six words, which is a property this file also tests
DE = (
    "Dieses Dokument ist ein Verzeichnis von Herstellern und Zulieferern für"
    " Elektrofahrräder, mit den Anschriften der Firmen und den Ansprechpartnern"
    " im Vertrieb. Es nennt auch die Komponenten, die jeder Hersteller selbst"
    " baut, und was er von anderen bezieht."
)
EN = (
    "This document is a directory of electric bicycle manufacturers and"
    " component suppliers, with company addresses and the people to ask in"
    " sales. It also says which components each maker builds itself and which"
    " it buys in from others."
)


class Runtime:
    """A model that answers with whatever it was given."""

    name = "stub"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, **kw: Any) -> tuple[str, dict[str, Any]]:
        self.asked.append((system, user))
        return self.answer, {"input_tokens": 10, "output_tokens": 20}


# --------------------------------------------------------------- naming


def test_common_types_are_the_kinds_of_thing() -> None:
    onto = ontology.current()
    for name in ("concept", "method", "ingredient", "technique", "material"):
        assert onto.naming(name) == "common", name
    for name in ("person", "author", "organization", "paper", "place", "tool"):
        assert onto.naming(name) == "proper", name
    # a claim is a sentence one document asserts, not the name of a kind
    # of thing: never folded across languages
    assert onto.naming("claim") == "proper"


def test_a_subtype_may_differ_from_its_parent() -> None:
    onto = ontology.current()
    # a dish is a work whose name translates; a standard is a concept
    # whose name does not
    assert onto.naming("work") == "proper"
    assert onto.naming("dish") == "common"
    assert onto.naming("concept") == "common"
    assert onto.naming("standard") == "proper"


def test_a_subtype_inherits_when_it_says_nothing() -> None:
    onto = ontology.current()
    assert onto.naming("ingredient") == "common"  # material
    assert onto.naming("cuisine") == "common"  # concept
    assert onto.naming("author") == "proper"  # person


def test_a_type_that_says_nothing_anywhere_is_proper() -> None:
    module = ontology.parse_module(
        "module: m\nversion: 1\nentity_types:\n  gadget:\n    description: a thing\n"
    )
    onto = ontology.compose([module])
    assert onto.naming("gadget") == "proper"
    assert onto.common_types == frozenset()


def test_an_unknown_naming_is_refused() -> None:
    with pytest.raises(ValueError, match="naming"):
        ontology.parse_module(
            "module: m\nversion: 1\nentity_types:\n  g:\n    naming: sometimes\n"
        )


def test_the_naming_key_bumps_no_version() -> None:
    # it says how a type's names behave, not what types exist: no triple
    # that validated before stops validating, so nothing re-extracts
    assert ontology.current().version.startswith("core3+")


def test_the_prompt_names_the_common_types() -> None:
    from prax import extraction

    prompt = extraction.system_prompt(ontology.current())
    assert "ingredient" in prompt
    assert "never translate it" in prompt


# ----------------------------------------------------------- translation


def test_translate_returns_the_english_text() -> None:
    runtime = Runtime(EN)
    got = summaries.translate(runtime, DE, lang="de", title="Ebike Hersteller")
    assert got is not None
    assert got.text.startswith("This document is a directory")
    assert got.usage["output_tokens"] == 20
    assert "German" in runtime.asked[0][1]  # the language is named, not the code
    assert "Ebike Hersteller" in runtime.asked[0][1]


def test_a_preamble_and_quotes_come_off() -> None:
    assert (
        summaries.parse('Here is the translation: "A short note."') == "A short note."
    )
    assert summaries.parse("```\nA short note.\n```") == "A short note."


def test_a_translation_still_in_german_is_refused() -> None:
    assert summaries.translate(Runtime(DE), DE, lang="de") is None


def test_a_model_that_writes_an_essay_is_refused() -> None:
    runtime = Runtime("The document is interesting. " * 60)
    assert summaries.translate(runtime, DE, lang="de") is None


def test_an_empty_answer_is_refused() -> None:
    assert summaries.translate(Runtime("   "), DE, lang="de") is None


def test_an_answer_about_the_text_instead_of_a_translation_is_refused() -> None:
    assert summaries.acceptable("Sure!", DE) == "too short"


# ------------------------------------------------------------ what is kept


def test_keep_files_the_summary_under_its_language() -> None:
    meta: dict[str, Any] = {}
    assert summaries.keep(meta, DE) == "de"
    assert meta["summary_lang"] == "de"
    assert meta["summaries"]["de"] == DE
    # the only summary there is, so it is the canonical one
    assert meta["summary"] == DE


def test_english_takes_the_canonical_place_and_german_is_kept() -> None:
    meta: dict[str, Any] = {}
    summaries.keep(meta, DE)
    summaries.keep(meta, EN, lang="en")
    assert meta["summary"] == EN
    assert meta["summary_lang"] == "en"
    assert set(meta["summaries"]) == {"de", "en"}
    assert meta["summaries"]["de"] == DE


def test_a_later_german_summary_does_not_displace_the_english_one() -> None:
    meta: dict[str, Any] = {"summary": EN, "summary_lang": "en"}
    summaries.keep(meta, DE, lang="de")
    assert meta["summary"] == EN
    assert meta["summary_lang"] == "en"
    assert meta["summaries"]["de"] == DE


def test_a_summary_too_short_to_place_claims_no_language() -> None:
    meta: dict[str, Any] = {}
    assert summaries.keep(meta, "Ein Verzeichnis.") is None
    assert meta["summary"] == "Ein Verzeichnis."
    assert "summary_lang" not in meta  # so no pass hands it to a model


# ------------------------------------------------------- the step, end to end


def test_the_step_translates_and_the_door_keeps_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hand-out, worker, take-in: the door stays the only writer."""
    from fastapi.testclient import TestClient

    from prax import store, work, worker

    monkeypatch.setenv("PRAX_SUMMARIES", "stub")
    work._leases.clear()
    from prax.api import app

    with TestClient(app) as client:
        con = client.app.state.con
        doc_id = client.post(
            "/ingest", json={"text": "ein Text " * 80, "title": "Ebike Hersteller"}
        ).json()["doc_id"]
        meta = store.get_meta(con, doc_id)
        summaries.keep(meta, DE)
        store.set_meta(con, doc_id, meta)
        assert store.get_meta(con, doc_id)["summary_lang"] == "de"

        batch = client.get("/work/summaries", params={"scope": "all"}).json()
        assert [i["doc_id"] for i in batch["items"]] == [doc_id]
        assert batch["items"][0]["lang"] == "de"

        results = worker.do_summaries(batch["items"], Runtime(EN))
        rep = client.post("/work/summaries", json={"results": results}).json()
        assert rep["applied"] == 1

        got = store.get_meta(con, doc_id)
        assert got["summary"] == EN
        assert got["summary_lang"] == "en"
        assert got["summaries"]["de"] == DE  # the German one is not lost
        # and the document is not handed out again
        assert (
            client.get("/work/summaries", params={"scope": "all"}).json()["items"] == []
        )


def test_a_summary_the_model_cannot_translate_is_not_handed_out_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from prax import store, work, worker

    monkeypatch.setenv("PRAX_SUMMARIES", "stub")
    work._leases.clear()
    from prax.api import app

    with TestClient(app) as client:
        con = client.app.state.con
        doc_id = client.post(
            "/ingest", json={"text": "ein Text " * 80, "title": "Ebike"}
        ).json()["doc_id"]
        meta = store.get_meta(con, doc_id)
        summaries.keep(meta, DE)
        store.set_meta(con, doc_id, meta)

        batch = client.get("/work/summaries", params={"scope": "all"}).json()
        results = worker.do_summaries(batch["items"], Runtime(DE))  # unchanged
        assert results[0]["tried"]
        rep = client.post("/work/summaries", json={"results": results}).json()
        assert rep["skipped"] == 1
        assert store.get_meta(con, doc_id)["summary"] == DE  # kept as written
        work._leases.clear()
        assert (
            client.get("/work/summaries", params={"scope": "all"}).json()["items"] == []
        )


# --------------------------------- the prompt handed back with the answer


ECHOED = (
    "Document title: Use Case Diagrams\n"
    "Description, written in German:\n"
    "This document introduces use case diagrams according to UML 2 to model"
    " functional requirements, with a cafeteria system as the example, and"
    " says where each notation belongs in a specification."
)


def test_the_labels_come_off_a_reflected_answer() -> None:
    got = summaries.parse(ECHOED)
    assert got is not None
    assert got.startswith("This document introduces")
    assert "Document title" not in got


def test_a_one_line_label_comes_off() -> None:
    got = summaries.parse("Description: The text analyzes the aesthetics of design.")
    assert got == "The text analyzes the aesthetics of design."


def test_a_label_that_survived_the_parse_is_refused() -> None:
    assert (
        summaries.acceptable("Title: something\nAnd more.", DE) == "the prompt's labels"
    )


def test_the_message_is_a_sentence_not_a_form() -> None:
    message = summaries.user_message(DE, lang="de", title="Ebike Hersteller")
    assert "Ebike Hersteller" in message
    assert "German" in message
    for label in ("Document title:", "Description:", "Summary:"):
        assert label not in message


def test_a_translation_not_good_enough_is_asked_for_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix for a bad batch is a better check and another pass, not a
    repair of rows: the summary as first written is still there."""
    from fastapi.testclient import TestClient

    from prax import pipeline, store, work, worker

    monkeypatch.setenv("PRAX_SUMMARIES", "stub")
    work._leases.clear()
    from prax.api import app

    with TestClient(app) as client:
        con = client.app.state.con
        doc_id = client.post(
            "/ingest", json={"text": "ein Text " * 80, "title": "Ebike"}
        ).json()["doc_id"]
        meta = store.get_meta(con, doc_id)
        summaries.keep(meta, DE)
        store.set_meta(con, doc_id, meta)

        # a batch that went through before the check knew about labels
        batch = client.get("/work/summaries", params={"scope": "all"}).json()
        results = worker.do_summaries(batch["items"], Runtime(ECHOED))
        # the worker refuses it now, so put it in the way the door did then
        store.set_summary(con, doc_id, ECHOED, lang="en", source="test")
        assert store.get_meta(con, doc_id)["summary_lang"] == "en"
        assert results  # the worker's own refusal is the other half

        # the pass asks for it again, and from the German, not the English
        work._leases.clear()
        assert pipeline.summaries_needed(con) == [(doc_id, "de")]
        again = client.get("/work/summaries", params={"scope": "all"}).json()
        assert again["items"][0]["summary"] == DE

        results = worker.do_summaries(again["items"], Runtime(EN))
        client.post("/work/summaries", json={"results": results})
        assert store.get_meta(con, doc_id)["summary"] == EN
        assert pipeline.summaries_needed(con) == []


def test_the_summary_already_there_is_filed_before_it_is_replaced() -> None:
    """The first batch overwrote eight German summaries: `keep` filed what
    it was handed and nothing filed what was already there."""
    meta: dict[str, Any] = {"summary": DE, "summary_lang": "de"}  # written before
    summaries.keep(meta, EN, lang="en")
    assert meta["summary"] == EN
    assert meta["summaries"]["de"] == DE
    assert meta["summaries"]["en"] == EN


def test_heal_takes_the_label_off_a_stored_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from prax import store
    from prax.api import app
    from prax.store import repair

    with TestClient(app) as client:
        con = client.app.state.con
        doc_id = client.post(
            "/ingest", json={"text": "ein Text " * 80, "title": "Use Case Diagrams"}
        ).json()["doc_id"]
        meta = store.get_meta(con, doc_id)
        meta["summary"], meta["summary_lang"] = ECHOED, "en"
        store.set_meta(con, doc_id, meta)

        found = repair._labelled_summaries(con)
        assert [r["id"] for r in found] == [doc_id]
        assert repair._repair_labelled_summaries(con, found) == 1
        got = store.get_meta(con, doc_id)["summary"]
        assert got.startswith("This document introduces")
        assert repair._labelled_summaries(con) == []
