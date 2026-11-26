"""What the reading-in-full pilot asked of the modules and the review
queue (2026-11-26): core 9, craft 2, music 2, sound 2, society 2; a triple
written the other way round is linked turned when only that fits; and an
item is judged under the modules its document is read with. The names and
quotes are made up here."""

from __future__ import annotations

import sqlite3
from importlib import resources
from typing import Any

from prax import store
from prax.graph import ontology, review


def _item(
    src: str, st: str, rel: str, dst: str, dt: str, *, refused: str = "src"
) -> dict[str, Any]:
    typed = st if refused == "src" else dt
    return {
        "id": 1,
        "src": src,
        "src_type": st,
        "rel": rel,
        "dst": dst,
        "dst_type": dt,
        "evidence": "made up",
        "source_doc": None,
        "reason": f"{rel!r} does not accept {refused} type {typed!r}",
    }


def test_the_modules_compose_and_people_in_music_have_relations() -> None:
    o = ontology.current()
    music = o.for_domains(["music"])
    assert music.version == "core9+craft2+music2"
    music.check_edge("person", "plays", "musical_instrument")
    music.check_edge("music_recording", "performed_by", "person")
    music.check_edge("person", "active_in", "music_genre")
    music.check_edge("person", "exemplifies", "compositional_technique")
    music.check_edge("person", "influenced_by", "person")  # core's now
    music.check_edge("document", "applies", "voice_leading")  # craft 2
    music.check_edge("compositional_technique", "valid_in", "musical_tradition")
    music.check_edge("musical_interval", "specializes", "musical_interval")
    # a plain concept is not a kind of music's: half such links were wrong
    for st, dt in (("concept", "concept"), ("concept", "chord")):
        try:
            music.check_edge(st, "specializes", dt)
        except ValueError:
            continue
        raise AssertionError(f"specializes took {st} -> {dt}")
    sound = o.for_domains(["sound"])
    sound.check_edge("audio_band", "affects", "sound_quality")
    sound.check_edge("sound_problem", "arises_from", "audio_band")
    sound.check_edge("production_technique", "applied_to", "audio_band")
    # society's thinkers keep their relation, under core
    o.for_domains(["society"]).check_edge("theory", "influenced_by", "person")


def test_a_triple_written_the_other_way_is_linked_turned() -> None:
    sound = ontology.current().for_domains(["sound"])
    a, edges, rule = review.decide(
        _item("de-esser", "audio_effect", "remedied_by", "sibilance", "sound_problem"),
        None,
        sound,
    )
    assert (a, rule) == ("link", "flip-fits")
    assert (edges[0].src, edges[0].rel, edges[0].dst) == (
        "sibilance",
        "remedied_by",
        "de-esser",
    )
    music = ontology.current().for_domains(["music"])
    a, edges, _ = review.decide(
        _item("Bach", "person", "composed_by", "a concerto", "musical_work"),
        None,
        music,
    )
    assert a == "link" and edges[0].src == "a concerto"


def test_a_flip_is_not_a_guess() -> None:
    o = ontology.current()
    sound = o.for_domains(["sound"])
    # the target's type was refused, not the source's: the order was right
    item = _item(
        "decay time", "sound_parameter", "affects", "rhythm", "rhythm", refused="dst"
    )
    assert review.decide(item, None, sound)[0] == "open"
    # a relation no text states, and one with its own rule
    for rel, st, dt in (
        ("depicts", "person", "document"),
        ("example_of", "work", "document"),
        ("part_of", "organization", "document"),
        ("uses", "concept", "method"),
    ):
        assert review.decide(_item("a", st, rel, "b", dt), None, o)[0] == "open", rel


def test_a_music_book_does_not_take_its_players_for_authors() -> None:
    """ "50. Blue Mitchell:" heads a musical example. Read with music alone
    the book has no paper and no author list; read with research, a person
    who authored_by a paper is the paper's author, as before."""
    music = ontology.current().for_domains(["music"])
    item = _item("Blue Mitchell", "person", "authored_by", "A Jazz Book", "document")
    assert review.decide(item, ("A Jazz Book", "document", None), music)[0] == "open"
    research = ontology.current().for_domains(["research"])
    a, _, rule = review.decide(item, ("A Jazz Book", "paper", None), research)
    assert (a, rule) == ("link", "flip-authored_by")


def test_replay_judges_an_item_under_its_documents_modules(
    con: sqlite3.Connection,
) -> None:
    """A music book is read with core, craft and music: research's
    ``about`` is not its relation, though the whole ontology has it."""
    book = int(
        store.ingest_text(con, "a book on harmony " * 50, title="Harmony")["doc_id"]
    )
    store.set_domains(con, book, ["music"])
    paper = int(
        store.ingest_text(con, "a paper on harmony " * 50, title="Paper")["doc_id"]
    )
    for doc in (book, paper):
        store.queue_review(
            con,
            src="Harmony" if doc == book else "Paper",
            src_type="document",
            rel="about",
            dst="voice leading",
            dst_type="concept",
            reason="made up",
            source_doc=doc,
            evidence="about voice leading",
        )
    rep = review.replay(con)
    assert rep.linked == 1
    (left,) = store.list_review(con)
    assert left["source_doc"] == book
    (edge,) = store.find_edges(
        con, store.Edge("Paper", "document", "about", "voice leading", "concept")
    )
    assert edge


def test_the_stamps_move_to_the_new_modules(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "a book read under music one " * 20)["doc_id"]
    meta = store.get_meta(con, doc)
    meta["extraction"] = {"ontology_version": "core8+craft1+music1"}
    meta["parts"] = {"plan": {"version": "core8+craft1+music1+research9+sound1"}}
    store.set_meta(con, doc, meta)
    con.executescript(
        resources.files("prax.migrations")
        .joinpath("0075_reading_in_full_modules.sql")
        .read_text(encoding="utf-8")
    )
    meta = store.get_meta(con, doc)
    assert meta["extraction"]["ontology_version"] == "core9+craft2+music2"
    assert meta["parts"]["plan"]["version"] == "core9+craft2+music2+research9+sound2"
