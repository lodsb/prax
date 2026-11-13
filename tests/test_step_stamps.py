"""Which documents a step reads again (structure review finding 1): every
step's record is named once in ``store.STAMPS``, and every due query and
count of the waiting is built from it (``_due``), so a new step does not
pick one of four shapes by copying."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from prax import store
from prax.store.base import DocumentMeta
from prax.store.documents.meta import _due, _of_this_text


def _doc(con: sqlite3.Connection, text: str, meta: dict) -> int:
    doc = int(
        store.register(con, text.encode(), mime="text/plain", title=text[:20])["doc_id"]
    )
    store.index_text(con, doc, text)
    row = con.execute("SELECT meta FROM documents WHERE id = ?", (doc,)).fetchone()
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?",
        (json.dumps({**json.loads(row[0]), **meta}), doc),
    )
    con.commit()
    return doc


def _waiting(con: sqlite3.Connection, step: str, *args: object) -> set[int]:
    return {
        int(r[0])
        for r in con.execute(f"SELECT id FROM documents WHERE {_due(step)}", args)
    }


def test_a_record_is_read_again_on_a_new_text_or_an_older_version(
    con: sqlite3.Connection,
) -> None:
    fresh = _doc(con, "A text the quality pass has not read. " * 5, {})
    text = con.execute("SELECT text_hash FROM documents WHERE id = ?", (fresh,))
    current = _doc(con, "A text it read at this version. " * 5, {})
    th = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (current,)
    ).fetchone()[0]
    store.update_meta(con, current, keys={"quality": {"text_hash": th, "version": 3}})
    older = _doc(con, "A text it read at an older version. " * 5, {})
    th_older = con.execute(
        "SELECT text_hash FROM documents WHERE id = ?", (older,)
    ).fetchone()[0]
    store.update_meta(
        con, older, keys={"quality": {"text_hash": th_older, "version": 2}}
    )
    other = _doc(
        con,
        "A text read when it was another. " * 5,
        {"quality": {"text_hash": "0" * 64, "version": 3}},
    )
    assert text.fetchone()[0]
    assert _waiting(con, "quality", 3) == {fresh, older, other}
    assert {
        int(r[0])
        for r in con.execute(
            f"SELECT id FROM documents WHERE {_of_this_text('quality')}"
        )
    } == {current, older}


def test_a_versioned_record_and_a_tried_mark(con: sqlite3.Connection) -> None:
    read = _doc(con, "A picture read at version two. " * 5, {"photo": {"v": 2}})
    old = _doc(con, "A picture read at version one. " * 5, {"photo": {"v": 1}})
    never = _doc(con, "A picture never read. " * 5, {})
    assert _waiting(con, "photos", 2) == {old, never}
    tried = _doc(con, "Genres tried here. " * 5, {})
    store.step_tried(con, tried, "genres", "run-1", "no label")
    assert tried not in _waiting(con, "genres")
    assert never in _waiting(con, "genres") and read in _waiting(con, "genres")


def test_every_record_is_declared_and_its_test_written_once() -> None:
    declared = set(DocumentMeta.__annotations__)
    for step, stamp in store.STAMPS.items():
        assert stamp.key in declared, f"{step}: meta.{stamp.key} is not declared"
    # the tests on a record's version or text hash, written outside the
    # builder, are what drifted apart (the counts and the due queries)
    keys = "|".join(re.escape(s.key) for s in store.STAMPS.values())
    # every field a record is read by, the shapes of finding 29 among them
    fields = {"text_hash"} | {
        f
        for s in store.STAMPS.values()
        for f in (s.version, s.exact, s.on_original, s.read_mark)
        if f
    }
    named = "|".join(sorted(re.escape(f) for f in fields))
    written = re.compile(rf"\$\.({keys})\.({named})'|\$\.({keys})'\)\s*IS NULL")
    root = Path(__file__).resolve().parents[1] / "src" / "prax"
    found = [
        f"{p.relative_to(root)}:{n}"
        for p in root.rglob("*.py")
        if p.name != "meta.py" or p.parent.name != "documents"
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "json_extract" in line and written.search(line)
    ]
    assert found == []


def test_a_title_clears_the_titles_steps_tried_mark(con: sqlite3.Connection) -> None:
    """Finding 46 of the 2026-11-11 quality review: each step's result
    clears its tried mark, the titles step's too."""
    doc = _doc(con, "A document without a title yet. " * 5, {})
    store.step_tried(con, doc, "titles", "run-1", "no title kept")
    assert "titles_tried" in store.get_meta(con, doc)
    store.retitle(con, doc, "A title at last", source="titles")
    assert "titles_tried" not in store.get_meta(con, doc)


def test_a_record_of_the_original_and_an_exact_version(
    con: sqlite3.Connection,
) -> None:
    """Finding 29 of the 2026-11-11 quality review: the markup pass's
    record of the original's hash and the private pass's rules stamp and
    read mark are stamps too, built by ``_due``."""
    fresh = _doc(con, "A page never read for its markup. " * 5, {})
    row = con.execute("SELECT hash FROM documents WHERE id = ?", (fresh,)).fetchone()
    read = _doc(con, "A page read for its markup. " * 5, {})
    mine = con.execute("SELECT hash FROM documents WHERE id = ?", (read,)).fetchone()
    con.execute(
        "UPDATE documents SET meta = json_set(meta, '$.markup', json(?)) WHERE id = ?",
        (json.dumps({"hash": mine[0]}), read),
    )
    other = _doc(con, "A page read when its bytes were others. " * 5, {})
    con.execute(
        "UPDATE documents SET meta = json_set(meta, '$.markup', json(?)) WHERE id = ?",
        (json.dumps({"hash": row[0]}), other),
    )
    con.commit()
    waiting = _waiting(con, "markup")
    assert fresh in waiting and other in waiting and read not in waiting
    looked = _doc(con, "Looked at by the rules of r2. " * 5, {})
    con.execute(
        "UPDATE documents SET meta = json_set(meta, '$.private', json(?)) WHERE id = ?",
        (json.dumps({"rules": "r2", "read": True}), looked),
    )
    con.commit()
    assert looked not in _waiting(con, "private", "r2")
    assert looked in _waiting(con, "private", "r3")
    assert fresh in _waiting(con, "private", "r2")
