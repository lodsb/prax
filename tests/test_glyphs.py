"""Glyphs the extractors leave behind: ligatures spelled out, Symbol-font
code points translated, on the way into the store and as a repair."""

from __future__ import annotations

import sqlite3

from prax import glyphs, store


def test_ligatures_and_symbol_font_code_points_become_letters() -> None:
    assert glyphs.clean("This study ﬁnds the ﬂow of eﬀort in oﬃce ﬁne") == (
        "This study finds the flow of effort in office fine"
    )
    # a Word-era formula set in Adobe Symbol: byte b at U+F000+b
    assert glyphs.clean("P  {p1  pN}  ,   ") == (
        "P = {p1 … pN} ∈ ℜ, α ≤ ∑"
    )
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
        (store.documents._archive_bytes(old.encode("utf-8")), doc_id),
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
