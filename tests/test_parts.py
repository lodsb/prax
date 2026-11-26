"""A document read in full (``prax.graph.parts``): its parts planned from
its chunks, handed out by the extract step after its first reading, and
taken in without touching that reading. The texts are made up here."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi.testclient import TestClient

from prax import store, work
from prax.graph import extraction, parts
from prax.steps import leases


def _chunk(text: str, start: int, heading: list[str], kind: str = "text") -> Any:
    loc = {"char_start": start, "char_end": start + len(text)}
    return {"text": text, "kind": kind, "locator": loc, "heading": heading}


def test_a_book_is_planned_in_parts_by_its_chapters() -> None:
    chunks, at = [], 0

    def add(n: int, heading: list[str], kind: str = "text") -> None:
        nonlocal at
        for _ in range(n):
            chunks.append(_chunk("w" * 998, at, heading, kind))
            at += 1000

    add(2, ["Contents"])  # front matter: never read
    add(5, ["Chapter 1", "Tape"])
    add(1, ["Chapter 2"])
    add(1, ["Chapter 3"])  # short chapters share a part
    add(30, ["Chapter 4", "Delay"])  # a long one is cut into windows
    add(3, ["Chapter 4"], kind="reference")  # set aside
    add(2, ["Index"])
    got = parts.plan(chunks)
    assert got[0] == parts.Part(2000, 6998, "Chapter 1 > Tape")
    assert got[1].label == "Chapter 2" and got[1].start == 7000
    # chapter 3 joined chapter 2, and chapter 4 went on filling that part
    assert all(p.end - p.start <= parts.PART_CHARS for p in got)
    assert got[-1].end == 7000 + 32 * 1000 - 2  # the last text chunk of chapter 4
    assert len(got) == 1 + 3  # 34,000 characters after chapter 1: three windows
    assert parts.next_part({"plan": {"parts": got}, "done": [0, 2]}) == 1


BOOK = "\n\n".join(
    [
        "# Echoes",
        "A book about tape echo and how it is built. " * 20,
        *(
            f"# Chapter {i}\n\n" + f"Chapter {i} explains the tape loop {i}. " * 120
            for i in range(1, 4)
        ),
    ]
)


def _result(doc_id: int, item: dict[str, Any], dst: str) -> dict[str, Any]:
    triple = extraction.Triple(
        "Echoes", "paper", "uses", dst, "method", "EXTRACTED", "the tape loop"
    )
    ex = extraction.Extraction(triples=[triple], summary=f"read as {dst}")
    out = {
        "doc_id": doc_id,
        "extraction": {
            "summary": ex.summary,
            "triples": [triple.__dict__],
            "unmapped": [],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    }
    if item.get("part"):
        out["part"] = item["part"]
    return out


def _take(con: sqlite3.Connection, results: list[dict[str, Any]]) -> dict[str, Any]:
    return work.take_in(
        con, "extract", {"results": results, "extractor": "m", "prompt": 1}
    )


def test_a_document_asked_is_read_in_parts_after_its_first_reading(
    con: sqlite3.Connection,
) -> None:
    doc = int(store.ingest_text(con, BOOK, title="Echoes")["doc_id"])
    store.ask_parts(con, doc, by="human")
    # the first reading goes first
    first = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    assert [i.get("part") for i in first] == [None]
    _take(con, [_result(doc, first[0], "tape echo")])
    stamp = store.get_meta(con, doc)["extraction"]
    # then its parts, one a batch, with the header saying which
    seen = []
    while True:
        items = work.hand_out(con, "extract", limit=5, scope="captures")["items"]
        if not items:
            break
        (item,) = items
        assert item["part"]["index"] == len(seen)
        assert f"Part: {len(seen) + 1} of {item['part']['of']}" in item["header"]
        assert "Title: Echoes" in item["header"] and "tape loop" in item["text"]
        seen.append(item)
        _take(con, [_result(doc, item, f"loop {len(seen)}")])
    assert len(seen) == seen[0]["part"]["of"] >= 2
    meta = store.get_meta(con, doc)
    # the first reading's stamp and summary are as it left them
    assert meta["extraction"] == stamp and meta["summary"] == "read as tape echo"
    record = meta["parts"]
    assert record["done"] == list(range(len(seen))) and record["linked"] == len(seen)
    loop = store.Edge("Echoes", "paper", "uses", "loop 1", "method")
    assert store.find_edges(con, loop, source_doc=doc)
    assert record["run"].startswith("parts-")
    (row,) = store.parts_asked(con)
    assert (row["done"], row["parts"]) == (len(seen), len(seen))


def test_a_part_comes_home_as_a_part_from_a_worker_that_does_not_say(
    con: sqlite3.Connection,
) -> None:
    """A worker that predates parts echoes no ``part``: the door's own
    note of what it handed out keeps the result off the first reading."""
    doc = int(store.ingest_text(con, BOOK, title="Echoes")["doc_id"])
    first = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    _take(con, [_result(doc, first[0], "tape echo")])
    store.ask_parts(con, doc)
    (_item,) = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    old = _result(doc, {}, "loop 1")  # no part in it
    _take(con, [old])
    meta = store.get_meta(con, doc)
    assert meta["summary"] == "read as tape echo" and meta["parts"]["done"] == [0]


def test_a_new_reading_of_the_whole_reads_the_parts_again(
    con: sqlite3.Connection,
) -> None:
    doc = int(store.ingest_text(con, BOOK, title="Echoes")["doc_id"])
    first = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    _take(con, [_result(doc, first[0], "tape echo")])
    store.ask_parts(con, doc)
    (item,) = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    _take(con, [_result(doc, item, "loop 1")])
    plan = dict(store.get_meta(con, doc)["parts"]["plan"])
    # a person asks for the whole to be read again: that retires every
    # edge of the reader, the parts' too, so they are due again
    store.request_extraction(con, doc, by="human")
    again = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    assert [i.get("part") for i in again] == [None]
    _take(con, [_result(doc, again[0], "tape echo")])
    record = store.get_meta(con, doc)["parts"]
    assert "plan" not in record and record["asked"]
    # a part handed out under a plan that is gone is not written
    stale = extraction.apply_part(
        con,
        doc,
        1,
        extraction.Extraction(),
        plan={**plan, "text_hash": "other"},
        extractor="m",
    )
    assert stale is None
    (nxt,) = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    assert nxt["part"]["index"] == 0


def test_a_part_the_model_cannot_read_is_passed(con: sqlite3.Connection) -> None:
    doc = int(store.ingest_text(con, BOOK, title="Echoes")["doc_id"])
    first = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    _take(con, [_result(doc, first[0], "tape echo")])
    store.ask_parts(con, doc)
    (item,) = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    _take(con, [{"doc_id": doc, "error": "timed out", "part": item["part"]}])
    meta = store.get_meta(con, doc)
    assert meta["parts"]["failed"] == {"0": "timed out"}
    assert "extraction_error" not in meta  # the first reading is not marked
    (nxt,) = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    assert nxt["part"]["index"] == 1
    leases.release("extract", [doc])


def test_the_door_asks_stops_and_lists(client: TestClient) -> None:
    got = client.post("/ingest", json={"text": BOOK, "title": "Echoes"}).json()
    doc = int(got["doc_id"])
    assert client.post(f"/doc/{doc}/parts", json={}).json()["asked"]["by"] == "human"
    (row,) = client.get("/parts").json()["documents"]
    assert row["doc_id"] == doc and row["parts"] is None
    assert client.delete(f"/doc/{doc}/parts").json() == {"stopped": True}
    assert client.get("/parts").json()["documents"] == []
    assert client.post("/doc/999999/parts", json={}).status_code == 404
