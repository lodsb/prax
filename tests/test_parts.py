"""A document read in full (``prax.graph.parts``): its parts planned from
its chunks, handed out by the extract step after its first reading, and
taken in without touching that reading. The texts are made up here."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import config, store, work
from prax.graph import extraction, parts
from prax.steps import leases
from prax.text import evidence


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


# The engineering review of 2026-11-26 (768ca91..7d8b8a6)


def test_a_part_waits_for_a_first_reading_its_scope_does_not_hand_out(
    con: sqlite3.Connection,
) -> None:
    """A document of a source the daytime scope leaves out (an import) is
    not on that scope's list of first readings due, and its parts wait for
    that reading all the same: their header says the title is known."""
    doc = int(store.ingest_text(con, BOOK, title="Echoes")["doc_id"])
    store.update_meta(con, doc, keys={"source": "zotero"})
    store.ask_parts(con, doc)
    assert work.hand_out(con, "extract", limit=5, scope="captures")["items"] == []
    (row,) = store.parts_asked(con)
    assert row["first_read"] is False
    first = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    assert [i.get("part") for i in first] == [None]
    _take(con, [_result(doc, first[0], "tape echo")])
    (item,) = work.hand_out(con, "extract", limit=5, scope="captures")["items"]
    assert item["part"]["index"] == 0
    leases.release("extract", [doc])


def test_a_part_the_plan_does_not_have_is_refused(con: sqlite3.Connection) -> None:
    doc = int(store.ingest_text(con, BOOK, title="Echoes")["doc_id"])
    first = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    _take(con, [_result(doc, first[0], "tape echo")])
    store.ask_parts(con, doc)
    (item,) = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    leases.release("extract", [doc])
    plan = store.get_meta(con, doc)["parts"]["plan"]
    with pytest.raises(ValueError, match="its plan has"):
        extraction.apply_part(
            con, doc, 99, extraction.Extraction(), plan=plan, extractor="m"
        )
    with pytest.raises(ValueError, match="its plan has"):
        store.part_failed(con, doc, -1, "x")
    assert store.get_meta(con, doc)["parts"]["done"] == []
    # the door's note of what it handed out wins over what a worker echoes
    echoed = _result(doc, item, "loop 1")
    (_again,) = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    echoed["part"] = {**echoed["part"], "index": len(plan["parts"]) - 1}
    _take(con, [echoed])
    assert store.get_meta(con, doc)["parts"]["done"] == [0]


def test_a_rechunk_loses_no_chunk_between_two_parts() -> None:
    """After a rechunk the plan holds (the text's hash is the same) and the
    chunks' boundaries have moved: a chunk that straddles two parts goes
    to the part it begins in, not to neither."""
    plan = {"parts": [[0, 4998, "One"], [5000, 9998, "Two"]]}
    rechunked = [
        _chunk("a" * 3998, 0, ["One"]),
        _chunk("b" * 2998, 4000, ["One"]),  # over the first part's end
        _chunk("c" * 2998, 7000, ["Two"]),
    ]
    one = parts.part_text(rechunked, *parts.span(plan, 0), window=12_000)
    two = parts.part_text(rechunked, *parts.span(plan, 1), window=12_000)
    assert "b" * 100 in one and "b" not in two
    assert "c" * 100 in two and "a" not in two


def test_front_and_back_matter_are_known_by_the_lexicon_in_other_languages() -> None:
    words = parts.matter_headings()
    assert parts._matter(["Indice"], words)  # Italian contents
    assert parts._matter(["7. References"], words)  # the chunker's words
    assert parts._matter(["Agradecimientos"], words)
    # a chapter that opens like a reference list is read
    assert not parts._matter(["Literature review"], words)
    assert not parts._matter(["Register"], words)  # an orchestration chapter


def test_the_window_is_the_hosts_setting_and_the_plan_keeps_its_own(
    con: sqlite3.Connection,
) -> None:
    chunks = [_chunk("w" * 998, i * 1000, ["Chapter 1"]) for i in range(30)]
    with config.overriding("PRAX_PART_CHARS", "6000"):
        assert all(p.end - p.start <= 6000 for p in parts.plan(chunks))
        assert len(parts.plan(chunks)) > len(parts.plan(chunks, window=12_000))
    with config.overriding("PRAX_PART_CHARS", "10"), pytest.raises(config.ConfigError):
        parts.part_chars()
    doc = int(store.ingest_text(con, BOOK, title="Echoes")["doc_id"])
    first = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    _take(con, [_result(doc, first[0], "tape echo")])
    store.ask_parts(con, doc)
    with config.overriding("PRAX_PART_CHARS", "4000"):
        (item,) = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    assert store.get_meta(con, doc)["parts"]["plan"]["window"] == 4000
    assert len(item["text"]) <= 4000
    leases.release("extract", [doc])


def test_the_asked_documents_are_read_through_their_index(
    con: sqlite3.Connection,
) -> None:
    """``parts_asked`` runs on every extract hand-out, an empty one too: a
    scan of every document's meta took 200 ms on the live store of
    2026-11-26 (33,811 documents); migration 74 serves it."""
    seen: list[str] = []
    con.set_trace_callback(seen.append)
    store.parts_asked(con)
    con.set_trace_callback(None)
    sql = next(s for s in seen if "$.parts.asked" in s)
    plan = " ".join(str(r[-1]) for r in con.execute("EXPLAIN QUERY PLAN " + sql))
    assert "idx_documents_parts" in plan, plan


# The pilot's edges judged (2026-11-26)


def test_a_part_names_the_document_as_its_one_node(con: sqlite3.Connection) -> None:
    """A part that writes the title another way ("Echoes.pdf") or calls the
    book an article lands on the node the first reading made: the pilot's
    jazz book was three nodes, its reverb guide two."""
    doc = int(store.ingest_text(con, BOOK, title="Echoes")["doc_id"])
    first = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    _take(con, [_result(doc, first[0], "tape echo")])
    store.ask_parts(con, doc)
    (item,) = work.hand_out(con, "extract", limit=5, scope="all")["items"]
    triple = extraction.Triple(
        "echoes .PDF",
        "article",
        "uses",
        "loop 1",
        "method",
        "EXTRACTED",
        "the tape loop",
    )
    result = _result(doc, item, "loop 1")
    result["extraction"]["triples"] = [triple.__dict__]
    _take(con, [result])
    assert store.find_edges(
        con, store.Edge("Echoes", "paper", "uses", "loop 1", "method"), source_doc=doc
    )
    assert store.document_node(con, doc) == ("Echoes", "paper")
    assert not any(
        "pdf" in str(r["name"]).lower() for r in store.find_entities(con, "echoes")
    )


def test_an_index_at_the_back_is_left_out_and_the_body_kept() -> None:
    """A PDF's index sits under the last chapter's heading path, its own
    heading third ("… / INDEX"), or under none, and is known by its page
    numbers; in the body neither a section called "Index" nor numbers
    leave a chunk out (a database book, a paper's pitch-class sets)."""
    pages = " ".join(f"term {i}, {i * 3}–{i * 3 + 2}," for i in range(20))
    sets = " ".join("{0, 2, 4, 7, 9}" for _ in range(12)) + " are pentatonic sets"
    chunks, at = [], 0
    for text, heading in (
        *(("w " * 499, ["Chapter 1"]) for _ in range(8)),
        (sets, ["Chapter 2"]),
        ("w " * 499, ["Chapter 2", "3.2 Index"]),
        *(("w " * 499, ["Chapter 3"]) for _ in range(8)),
        (pages, ["Chapter 3", "Strategy", "INDEX"]),
        (pages, ["Chapter 3", "Strategy", "C"]),
    ):
        chunks.append(_chunk(text, at, heading))
        at += len(text) + 2
    got = parts.plan(chunks, window=12_000, min_chars=3_000)
    text = "".join(
        parts.part_text(chunks, *parts.span({"parts": got_parts}, i), window=12_000)
        for got_parts in [[[p.start, p.end, p.label] for p in got]]
        for i in range(len(got))
    )
    assert "pentatonic" in text and "term 3" not in text
    assert got[-1].end <= chunks[-3]["locator"]["char_end"]
    assert sum(1 for p in got if p.label.startswith("Chapter 2")) == 1


def test_an_index_runs_on_over_its_thinner_and_shorter_chunks() -> None:
    """An index chunk with longer entries holds fewer page numbers than
    ``INDEX_SHARE``, and its last letter's chunk is too short to judge
    alone: beside one that is clearly index, both go with it, and the
    prose after it (the author's note) is read."""
    dense = " ".join(f"term {i}, {i * 3}–{i * 3 + 2}," for i in range(20))
    thin = " ".join(f"entry on topic {chr(97 + i)}, {i * 3 + 10}," for i in range(8))
    short = "zither, 141 zone, 77–78"
    chunks, at = [], 0
    for text, heading in (
        *(("w " * 499, ["Chapter 1"]) for _ in range(12)),
        (thin, ["Chapter 2", "I"]),
        (dense, ["Chapter 2", "I"]),
        (short, ["Chapter 2", "Z"]),
        ("The author teaches arranging and plays the trombone. " * 4, ["About"]),
    ):
        chunks.append(_chunk(text, at, heading))
        at += len(text) + 2
    assert evidence.contents_share(thin) < parts.INDEX_SHARE
    got = parts.plan(chunks, window=12_000, min_chars=3_000)
    text = "".join(
        parts.part_text(chunks, *parts.span({"parts": ranges}, i), window=12_000)
        for ranges in [[[p.start, p.end, p.label] for p in got]]
        for i in range(len(got))
    )
    assert "topic" not in text and "term" not in text and "zither" not in text
    assert "trombone" in text
