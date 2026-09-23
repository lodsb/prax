"""The language of a text, and the field it is recorded in."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from prax import language, store


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from prax.api import app

    with TestClient(app) as c:  # the lifespan opens the store
        yield c


GERMAN = (
    "Falls Sie für die kühleren Monate eine ungewöhnliche, aber leckere"
    " Füllung für Ihre Tacos suchen, die wenig kostet und auch vegan"
    " zubereitet werden kann, könnte der Knollensellerie Ihre Lösung sein."
    " Er ist fest und faserig genug, um auch bei großer Hitze seine Form zu"
    " wahren, und süß genug, um zu karamellisieren."
)
ENGLISH = (
    "The noise reduction method presented in this paper is based on a"
    " short-time spectral analysis of the signal, and we compare it with the"
    " classical approach of spectral subtraction over a set of recordings"
    " made in a reverberant room with two microphones."
)
FRENCH = (
    "Cette méthode de réduction du bruit est fondée sur une analyse spectrale"
    " à court terme du signal, et nous la comparons avec les approches"
    " classiques de la soustraction spectrale dans une salle réverbérante."
)


def test_detect_reads_the_languages_this_library_holds() -> None:
    assert language.detect(GERMAN) == "de"
    assert language.detect(ENGLISH) == "en"
    assert language.detect(FRENCH) == "fr"


def test_detect_says_nothing_rather_than_guess() -> None:
    """A title, a page of numbers, an empty artifact: no language."""
    assert language.detect("") is None
    assert language.detect(None) is None
    assert language.detect("Reverb") is None
    assert language.detect("12 34 56 78 90 " * 20) is None


def test_the_sample_is_the_head_of_the_text() -> None:
    """A book is read as far as the sample, not to the end."""
    long_one = ENGLISH + " " + ("x" * 500_000)
    assert language.detect(long_one) == "en"
    assert language.SAMPLE < 10_000


def test_a_name_for_a_reader() -> None:
    assert language.name("de") == "German"
    assert language.name("xx") == "xx"
    assert language.name(None) == ""


def test_indexing_a_text_records_its_language(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, GERMAN, title="Tacos")["doc_id"]
    assert store.get_meta(con, doc)["lang"] == "de"
    other = store.ingest_text(con, ENGLISH, title="Noise reduction")["doc_id"]
    assert store.get_meta(con, other)["lang"] == "en"
    # a text too short to tell carries no language at all, and that is fine
    short = store.ingest_text(con, "Reverb.", title="A note")["doc_id"]
    assert "lang" not in store.get_meta(con, short)


def test_the_maintain_pass_fills_what_was_indexed_before(
    con: sqlite3.Connection,
) -> None:
    """The backfill reads only documents that do not say yet, so the
    nightly costs nothing once the library is stamped."""
    doc = store.ingest_text(con, GERMAN, title="Tacos")["doc_id"]
    con.execute(
        "UPDATE documents SET meta = json_remove(meta, '$.lang') WHERE id = ?",
        (doc,),
    )
    con.commit()
    assert "lang" not in store.get_meta(con, doc)
    out = store.maintain(con, only=["languages"])["languages"]
    assert out["read"] == 1 and out["de"] == 1
    assert store.get_meta(con, doc)["lang"] == "de"
    again = store.maintain(con, only=["languages"])["languages"]
    assert again["read"] == 0


def test_the_door_answers_with_the_language(client: TestClient) -> None:
    doc = client.post("/ingest", json={"text": GERMAN, "title": "Tacos"}).json()
    got = client.get(f"/get/{doc['doc_id']}").json()
    assert got["meta"]["lang"] == "de"
