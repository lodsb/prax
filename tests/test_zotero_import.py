"""Zotero importer against the checked-in fixture (tests/fixtures/zotero).

Fixture contents (docs/sources.md): Yoshii 2018 (one 8 KB PDF, DOI, one
creator); Birbaumer 1996 saved four times (identical PDF under four items);
Brown & Duda 1997; Tobar 2019 with a child note; Févotte & Kowalski 2018 with
an HTML snapshot; Das 2020 with a linked URL and no file; de Berardinis 2020
with two different PDFs; one standalone PDF with no parent item.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from prax import store
from prax.importers import zotero

FIXTURE = Path(__file__).parent / "fixtures" / "zotero"
BIRBAUMER_ITEMS = {"4L6ILMZN", "HW3N7956", "U263HF74", "MCNISPSX"}
BIRBAUMER_ATTACHMENTS = {"J2E8FPQW", "HJSXK2AJ", "ST5TET8Z", "WZ7VIHP9"}


@pytest.fixture()
def lib(tmp_path: Path):
    lib = zotero.open_library(FIXTURE, tmp_path / "work")
    yield lib
    lib.close()


def _docs_by_key(con: sqlite3.Connection) -> dict[str, int]:
    return store.meta_index(con, "$.zotero.keys")


def _n_docs(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM documents").fetchone()[0]


def _n_edges(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM edges").fetchone()[0]


# --------------------------------------------------------------- read side


def test_open_library_works_on_a_copy(tmp_path: Path) -> None:
    before = hashlib.sha256((FIXTURE / "zotero.sqlite").read_bytes()).hexdigest()
    lib = zotero.open_library(FIXTURE, tmp_path / "w")
    assert lib.copy_path != FIXTURE / "zotero.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        lib.con.execute("DELETE FROM items")
    lib.close()
    after = hashlib.sha256((FIXTURE / "zotero.sqlite").read_bytes()).hexdigest()
    assert before == after


def test_plan_shapes(lib: zotero.Library) -> None:
    planned = {p.key: p for p in zotero.plan(lib)}
    kinds = {p.kind for p in planned.values()}
    assert kinds == {"attachment", "url", "note"}
    yoshii = planned["T7VVNPCK"]
    assert yoshii.title == "Correlated Tensor Factorization for Audio Source Separation"
    assert yoshii.mime == "application/pdf"
    assert yoshii.meta["doi"] == "10.1109/ICASSP.2018.8461434"
    assert yoshii.meta["date"] == "2018-04"
    assert yoshii.meta["creators"][0]["name"] == "Kazuyoshi Yoshii"
    assert yoshii.meta["collections"] == ["analysis"]
    assert yoshii.meta["text_source"] == "zotero-ft-cache"
    assert yoshii.meta["zotero"]["items"] == ["9QRPZL68"]
    url = planned["ZRWHFMBJ"]
    assert url.kind == "url" and url.mime == "text/uri-list"
    assert url.source_url.startswith("https://www.researchgate.net/")
    assert "pitch" in url.text.lower()
    note = next(p for p in planned.values() if p.kind == "note")
    assert note.meta["zotero"]["parent"] == "EZLSQSMG"
    assert note.text.startswith("Comment:")
    standalone = planned["VVJITI78"]
    assert standalone.item is None
    assert standalone.title  # from the attachment's own title or filename
    assert planned["97KAI26I"].mime == "text/html"


def test_inventory_counts(lib: zotero.Library) -> None:
    inv = zotero.inventory(lib, hash_files=True)
    assert inv.items_by_type == {
        "journalArticle": 7,
        "conferencePaper": 2,
        "preprint": 1,
    }
    assert inv.planned_by_kind == {"attachment": 11, "note": 1, "url": 1}
    assert inv.missing_files == []
    assert inv.with_text_cache == 11 and inv.without_text_cache == 0
    assert (
        list(inv.duplicate_hashes.values()) == [sorted(BIRBAUMER_ATTACHMENTS)]
        or set(next(iter(inv.duplicate_hashes.values()))) == BIRBAUMER_ATTACHMENTS
    )


def test_html_to_text_strips_markup() -> None:
    assert zotero.html_to_text("<div><p>a &amp; b</p><br>c</div>") == "a & b\nc"


def test_normalize_date() -> None:
    assert zotero._normalize_date("2018-04-00 4/2018") == "2018-04"
    assert zotero._normalize_date("1997-00-00 1997") == "1997"
    assert zotero._normalize_date("2020-06-24 2020-06-24") == "2020-06-24"
    assert zotero._normalize_date(None) is None


# -------------------------------------------------------------- write side


def test_import_creates_documents_and_dedupes(
    con: sqlite3.Connection, lib: zotero.Library
) -> None:
    report = zotero.run(lib, con)
    assert report.errors == []
    # 11 attachments, 4 of them one file -> 8; plus url and note.
    assert report.actions == {"created": 10, "merged": 3}
    assert _n_docs(con) == 10
    by_key = _docs_by_key(con)
    assert {by_key[k] for k in BIRBAUMER_ATTACHMENTS} == {by_key["J2E8FPQW"]}
    meta = store.get_meta(con, by_key["J2E8FPQW"])
    assert set(meta["zotero"]["keys"]) == BIRBAUMER_ATTACHMENTS
    assert set(meta["zotero"]["items"]) == BIRBAUMER_ITEMS
    # de Berardinis: two different PDFs under one item -> two documents.
    assert by_key["7656ADFF"] != by_key["M7Q9GCW6"]


def test_import_indexes_from_zotero_cache(
    con: sqlite3.Connection, lib: zotero.Library
) -> None:
    zotero.run(lib, con)
    hits = store.search(con, "correlated tensor factorization")
    assert hits and hits[0]["title"].startswith("Correlated Tensor Factorization")
    doc = store.get_document(con, hits[0]["doc_id"], max_chars=200)
    assert doc["parsed_at"] and doc["meta"]["text_source"] == "zotero-ft-cache"
    assert doc["mime"] == "application/pdf"
    assert doc["original_path"].endswith(".pdf")
    # the HTML snapshot is searchable through its cache text too
    assert any(
        h["doc_id"] == _docs_by_key(con)["97KAI26I"]
        for h in store.search(con, "low-rank time-frequency synthesis", limit=20)
    )


def test_url_only_and_note_documents(
    con: sqlite3.Connection, lib: zotero.Library
) -> None:
    zotero.run(lib, con)
    by_key = _docs_by_key(con)
    url_doc = store.get_document(con, by_key["ZRWHFMBJ"])
    assert url_doc["mime"] == "text/uri-list"
    assert url_doc["source_url"].startswith("https://www.researchgate.net/")
    assert "Extended Complex Kalman Filter" in url_doc["text"]
    note_id = next(
        i
        for k, i in by_key.items()
        if store.get_meta(con, i)["zotero"]["kind"] == "note"
    )
    note = store.get_document(con, note_id)
    assert note["meta"]["zotero"]["parent"] == "EZLSQSMG"
    assert note["text"].startswith("Comment:")


def test_import_seeds_authored_by_edges(
    con: sqlite3.Connection, lib: zotero.Library
) -> None:
    report = zotero.run(lib, con)
    # Yoshii 1, Birbaumer 5 (once for four twins), Brown & Duda 2, Tobar 1,
    # Févotte & Kowalski 2, Das 3, de Berardinis 4 (once for two PDFs); no note
    assert report.edges == 18
    assert _n_edges(con) == 18
    rows = store.traverse(con, "Kazuyoshi Yoshii")
    assert len(rows) == 1
    edge = rows[0]
    assert edge["rel"] == "authored_by" and edge["src_type"] == "paper"
    assert edge["confidence"] == "EXTRACTED"
    assert edge["source_doc"] == _docs_by_key(con)["T7VVNPCK"]
    row = con.execute("SELECT ontology_version FROM edges LIMIT 1").fetchone()
    assert row[0] == "0"


def test_rerun_is_idempotent(con: sqlite3.Connection, lib: zotero.Library) -> None:
    zotero.run(lib, con)
    n_docs, n_edges = _n_docs(con), _n_edges(con)
    n_chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    report = zotero.run(lib, con)
    assert report.actions == {"skipped": 13} and report.edges == 0
    assert (_n_docs(con), _n_edges(con)) == (n_docs, n_edges)
    assert con.execute("SELECT count(*) FROM chunks").fetchone()[0] == n_chunks


def test_changed_record_refreshes_meta(
    con: sqlite3.Connection, lib: zotero.Library
) -> None:
    zotero.run(lib, con)
    doc_id = _docs_by_key(con)["T7VVNPCK"]
    old = store.get_meta(con, doc_id)
    old["zotero"]["modified"]["T7VVNPCK"] = "1999-01-01 00:00:00"
    old["tags"] = ["stale"]
    store.set_meta(con, doc_id, old)
    report = zotero.run(lib, con)
    assert report.actions["refreshed"] == 1 and report.edges == 0
    new = store.get_meta(con, doc_id)
    assert new["tags"] == []
    assert new["zotero"]["modified"]["T7VVNPCK"] != "1999-01-01 00:00:00"
    assert _n_docs(con) == 10


def test_limit_stops_early(con: sqlite3.Connection, lib: zotero.Library) -> None:
    report = zotero.run(lib, con, limit=3)
    assert sum(report.actions.values()) == 3
    report = zotero.run(lib, con)  # the rest follows on the next run
    assert report.actions["skipped"] == 3 and _n_docs(con) == 10


def test_missing_file_is_reported_not_fatal(
    con: sqlite3.Connection, tmp_path: Path
) -> None:
    import shutil

    broken = tmp_path / "zotero"
    shutil.copytree(FIXTURE, broken)
    shutil.rmtree(broken / "storage" / "T7VVNPCK")
    lib = zotero.open_library(broken, tmp_path / "w")
    try:
        inv = zotero.inventory(lib)
        assert [k for k, _ in inv.missing_files] == ["T7VVNPCK"]
        # the parent has no other file, so it survives as a metadata-only document
        assert inv.planned_by_kind["metadata"] == 1
        report = zotero.run(lib, con)
    finally:
        lib.close()
    assert report.actions["missing"] == 1 and report.errors == []
    doc = store.get_document(con, _docs_by_key(con)["9QRPZL68"])
    assert doc["mime"] == "text/plain" and doc["meta"]["zotero"]["kind"] == "metadata"
    assert "Correlated Tensor Factorization" in doc["text"]
