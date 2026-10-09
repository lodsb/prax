"""Glyphs the extractors leave behind: ligatures spelled out, Symbol-font
code points translated, on the way into the store and as a repair."""

from __future__ import annotations

import sqlite3

from prax import store
from prax.text import glyphs


def test_ligatures_and_symbol_font_code_points_become_letters() -> None:
    assert glyphs.clean("This study ﬁnds the ﬂow of eﬀort in oﬃce ﬁne") == (
        "This study finds the flow of effort in office fine"
    )
    # a Word-era formula set in Adobe Symbol: byte b at U+F000+b
    assert glyphs.clean("P  {p1  pN}  ,   ") == ("P = {p1 … pN} ∈ ℜ, α ≤ ∑")
    # a Wingdings bullet at the start of a line is a bullet; the same code
    # point inside a formula is the Greek letter the Symbol font puts there
    assert glyphs.clean("list:\n one\n   two\nrate  here") == (
        "list:\n• one\n  • two\nrate λ here"
    )
    # other private-use ranges and U+FFFD are left as they came
    assert glyphs.clean("x  y � z") == "x  y � z"
    assert not glyphs.damaged("plain text") and glyphs.damaged("ﬁ")


def test_texts_are_cleaned_on_the_way_in(con: sqlite3.Connection) -> None:
    raw = "A ﬁlter with cutoﬀ c and gain  2."
    doc_id = store.ingest_text(con, raw, title="t")["doc_id"]
    text = store.get_document(con, doc_id)["text"]
    assert text == "A filter with cutoff ωc and gain = 2."
    hits = store.search(con, "filter cutoff", mode="fts")
    assert [h["doc_id"] for h in hits] == [doc_id]


def test_the_heal_pass_reindexes_the_old_texts(con: sqlite3.Connection) -> None:
    from prax.store import repair

    doc_id = store.ingest_text(con, "words " * 60, title="old")["doc_id"]
    # an artifact from before the cleaning, planted as the store would have had it
    old = "The ﬁrst ﬁgure. " * 40
    con.execute(
        "UPDATE documents SET text_hash = ? WHERE id = ?",
        (store._archive_bytes(old.encode("utf-8")), doc_id),
    )
    store.documents._write_chunks(con, doc_id, old)  # through the FTS triggers
    con.commit()
    found = repair.health(con, only=["unmapped-glyphs"])["ailments"][0]
    assert found["count"] == 1 and found["examples"][0]["id"] == doc_id
    healed = repair.heal(con, only=["unmapped-glyphs"])
    assert healed["unmapped-glyphs"] == {"found": 1, "repaired": 1}
    assert "ﬁ" not in store.get_document(con, doc_id)["text"]
    assert "first figure" in store.get_document(con, doc_id)["text"]
    assert repair.health(con, only=["unmapped-glyphs"])["ailments"][0]["count"] == 0


def test_accents_set_beside_their_letters_go_back_on_them() -> None:
    a = glyphs.accents
    # before the letter, the common case; and after it, where the text does so
    assert a("f¨ur k¨onnen ¨uber Universit¨at") == "für können über Universität"
    assert a("Fakulta¨t fu¨r Informatik, Einfu¨ hrung") == (
        "Fakultät für Informatik, Einführung"
    )
    # both orders in one name: each accent goes where its letters are
    assert a("Greˇsa´kova, Erd˝os, B´ezier, Fran¸cois") == (
        "Grešákova, Erdős, Bézier, François"
    )
    # an accent over a dotless i is on that i
    assert a("reconnaˆıtre, technologi´ı") == "reconnaître, technologií"
    # a grave in a word is an accent; one that opens or closes code is not
    assert a("Universit`a di Pisa, tr`es") == "Università di Pisa, très"
    assert a("call `foo` and the`AND, g`contents`as") == (
        "call `foo` and the`AND, g`contents`as"
    )
    # an apostrophe written as ´, a hat in a formula, a mark alone: kept
    assert a("Wobbrock´s xˆ2 ´\n <sup>¨</sup> k˜At") == (
        "Wobbrock´s xˆ2 ´\n <sup>¨</sup> k˜At"
    )
    assert glyphs.clean("Einf¨uhrung") == "Einführung"
    assert glyphs.damaged("f¨ur") and not glyphs.damaged("Wobbrock´s `code`")


def test_the_heal_pass_puts_accents_back_in_old_texts(con: sqlite3.Connection) -> None:
    from prax.store import repair

    doc_id = store.ingest_text(con, "words " * 60, title="old")["doc_id"]
    code = store.ingest_text(con, "use `foo` here " * 40, title="code")["doc_id"]
    old = "Die Universit¨at M¨unchen. " * 40
    con.execute(
        "UPDATE documents SET text_hash = ? WHERE id = ?",
        (store._archive_bytes(old.encode("utf-8")), doc_id),
    )
    store.documents._write_chunks(con, doc_id, old)
    con.commit()
    found = repair.health(con, only=["unmapped-glyphs"])["ailments"][0]
    assert [e["id"] for e in found["examples"]] == [doc_id]  # code is no damage
    assert (
        repair.heal(con, only=["unmapped-glyphs"])["unmapped-glyphs"]["repaired"] == 1
    )
    assert "Universität München" in store.get_document(con, doc_id)["text"]
    hits = store.search(con, "Universität", mode="fts")
    assert doc_id in [h["doc_id"] for h in hits] and code not in [
        h["doc_id"] for h in hits
    ]
