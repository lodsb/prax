"""Zotero importer against the checked-in fixture (tests/fixtures/zotero).

Fixture contents (tests/fixtures/zotero/README.md): Ambrits & Bank 2013
(one PDF, two creators, a collection); Průša & Rajmic 2017 (PDF, DOI);
Rutz et al. 2010 saved four times (identical PDF under four items); Carr &
Zukowski 2018 with a child note; FugueGenerator with three different PDFs
under one item; a Stack Exchange HTML snapshot; Das 2020 with a linked URL
and no file; Robust-NTF, a metadata-only computer-program item; one
standalone PDF with no parent item.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from prax import ontology, store
from prax.importers import zotero

FIXTURE = Path(__file__).parent / "fixtures" / "zotero"
RUTZ_ITEMS = {"Z9IJ6QGS", "5L254ZE8", "SEQ38R7E", "R2A8KM4Y"}
RUTZ_ATTACHMENTS = {"FBT2AP8S", "RWTJSFI7", "432URMMU", "RR3AAFYH"}
FUGUE_ATTACHMENTS = {"PTHEK9QS", "R89PCW52", "5QSJDXUQ"}
AMBRITS = "FCEK3EI9"  # the small PDF most tests reach for
PRUSA = "HJR45JMH"  # the one with a DOI
SNAPSHOT = "AYY57KAK"  # the HTML snapshot
URL_ONLY = "ZRWHFMBJ"
STANDALONE = "UW29C5GP"
N_DOCS = 12  # 12 attachments (4 twins -> 1) + url + metadata + note


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
    assert kinds == {"attachment", "url", "note", "metadata"}
    prusa = planned[PRUSA]
    assert prusa.title == (
        "Toward High-Quality Real-Time Signal Reconstruction From STFT Magnitude"
    )
    assert prusa.mime == "application/pdf"
    assert prusa.meta["doi"] == "10.1109/LSP.2017.2696970"
    assert prusa.meta["date"] == "2017-06"
    assert prusa.meta["creators"][0]["name"] == "Zdenek Prusa"
    assert prusa.meta["collections"] == ["time frequency"]
    assert prusa.meta["text_source"] == "zotero-ft-cache"
    assert prusa.meta["zotero"]["items"] == ["GE5DX6CB"]
    ambrits = planned[AMBRITS]
    assert ambrits.title.startswith("IMPROVED POLYNOMIAL TRANSITION REGIONS")
    assert ambrits.meta["collections"] == ["antialiasing"]
    url = planned[URL_ONLY]
    assert url.kind == "url" and url.mime == "text/uri-list"
    assert url.source_url.startswith("https://www.researchgate.net/")
    assert "pitch" in url.text.lower()
    note = next(p for p in planned.values() if p.kind == "note")
    assert note.meta["zotero"]["parent"] == "4MMHC9A4"
    assert note.text.startswith("Comment:")
    meta_only = planned["IIJ9PSTU"]
    assert meta_only.kind == "metadata" and meta_only.title == "Robust-NTF"
    assert meta_only.source_url.startswith("https://github.com/")
    standalone = planned[STANDALONE]
    assert standalone.item is None
    assert standalone.title  # from the attachment's own title or filename
    assert planned[SNAPSHOT].mime == "text/html"


def test_inventory_counts(lib: zotero.Library) -> None:
    inv = zotero.inventory(lib, hash_files=True)
    assert inv.items_by_type == {
        "journalArticle": 8,
        "computerProgram": 1,
        "preprint": 1,
        "webpage": 1,
    }
    assert inv.planned_by_kind == {
        "attachment": 12,
        "note": 1,
        "url": 1,
        "metadata": 1,
    }
    assert inv.missing_files == []
    assert inv.with_text_cache == 12 and inv.without_text_cache == 0
    assert [set(v) for v in inv.duplicate_hashes.values()] == [RUTZ_ATTACHMENTS]


def test_html_to_text() -> None:
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
    # 12 attachments, 4 of them one file -> 9; plus url, metadata and note.
    assert report.actions == {"created": 12, "merged": 3}
    assert _n_docs(con) == N_DOCS
    by_key = _docs_by_key(con)
    assert {by_key[k] for k in RUTZ_ATTACHMENTS} == {by_key["FBT2AP8S"]}
    meta = store.get_meta(con, by_key["FBT2AP8S"])
    assert set(meta["zotero"]["keys"]) == RUTZ_ATTACHMENTS
    assert set(meta["zotero"]["items"]) == RUTZ_ITEMS
    # FugueGenerator: three different PDFs under one item -> three documents.
    assert len({by_key[k] for k in FUGUE_ATTACHMENTS}) == 3


def test_import_indexes_from_zotero_cache(
    con: sqlite3.Connection, lib: zotero.Library
) -> None:
    zotero.run(lib, con)
    hits = store.search(con, "polynomial transition regions")
    assert hits and hits[0]["title"].startswith("IMPROVED POLYNOMIAL TRANSITION")
    doc = store.get_document(con, hits[0]["doc_id"], max_chars=200)
    assert doc["parsed_at"] and doc["meta"]["text_source"] == "zotero-ft-cache"
    assert doc["mime"] == "application/pdf"
    assert doc["original_path"].endswith(".pdf")
    # the HTML snapshot is searchable through its cache text too
    assert any(
        h["doc_id"] == _docs_by_key(con)[SNAPSHOT]
        for h in store.search(con, "IIR Hilbert transformer", limit=20)
    )


def test_url_only_metadata_and_note_documents(
    con: sqlite3.Connection, lib: zotero.Library
) -> None:
    zotero.run(lib, con)
    by_key = _docs_by_key(con)
    url_doc = store.get_document(con, by_key[URL_ONLY])
    assert url_doc["mime"] == "text/uri-list"
    assert url_doc["source_url"].startswith("https://www.researchgate.net/")
    assert "Extended Complex Kalman Filter" in url_doc["text"]
    meta_doc = store.get_document(con, by_key["IIJ9PSTU"])
    assert meta_doc["mime"] == "text/plain"
    assert meta_doc["meta"]["zotero"]["kind"] == "metadata"
    assert meta_doc["text"].startswith("Robust-NTF")
    note_id = next(
        i
        for k, i in by_key.items()
        if store.get_meta(con, i)["zotero"]["kind"] == "note"
    )
    note = store.get_document(con, note_id)
    assert note["meta"]["zotero"]["parent"] == "4MMHC9A4"
    assert note["text"].startswith("Comment:")


def test_import_seeds_authored_by_edges(
    con: sqlite3.Connection, lib: zotero.Library
) -> None:
    report = zotero.run(lib, con)
    # Ambrits 2, Průša 2, Rutz 3 (once for four twins), Carr 2, Das 3;
    # FugueGenerator, the snapshot, the standalone PDF and the note have
    # no creators; the metadata-only item's creator carries no paper title
    assert report.edges == 12
    assert _n_edges(con) == 12
    rows = store.traverse(con, "Zdenek Prusa")
    assert len(rows) == 1
    edge = rows[0]
    assert edge["rel"] == "authored_by" and edge["src_type"] == "paper"
    assert edge["confidence"] == "EXTRACTED"
    assert edge["source_doc"] == _docs_by_key(con)[PRUSA]
    row = con.execute("SELECT ontology_version FROM edges LIMIT 1").fetchone()
    assert row[0] == ontology.current().version


def test_rerun_is_idempotent(con: sqlite3.Connection, lib: zotero.Library) -> None:
    zotero.run(lib, con)
    n_docs, n_edges = _n_docs(con), _n_edges(con)
    n_chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    report = zotero.run(lib, con)
    assert report.actions == {"skipped": 15} and report.edges == 0
    assert (_n_docs(con), _n_edges(con)) == (n_docs, n_edges)
    assert con.execute("SELECT count(*) FROM chunks").fetchone()[0] == n_chunks


def test_changed_record_refreshes_meta(
    con: sqlite3.Connection, lib: zotero.Library
) -> None:
    zotero.run(lib, con)
    doc_id = _docs_by_key(con)[AMBRITS]
    old = store.get_meta(con, doc_id)
    old["zotero"]["modified"][AMBRITS] = "1999-01-01 00:00:00"
    old["tags"] = ["stale"]
    store.set_meta(con, doc_id, old)
    report = zotero.run(lib, con)
    assert report.actions["refreshed"] == 1 and report.edges == 0
    new = store.get_meta(con, doc_id)
    assert new["tags"] == []
    assert new["zotero"]["modified"][AMBRITS] != "1999-01-01 00:00:00"
    assert _n_docs(con) == N_DOCS


def test_refresh_keeps_a_repaired_title(
    con: sqlite3.Connection, lib: zotero.Library
) -> None:
    zotero.run(lib, con)
    doc_id = _docs_by_key(con)[STANDALONE]
    store.retitle(con, doc_id, "SID GUTS controls schematic", source="human")
    old = store.get_meta(con, doc_id)
    old["zotero"]["modified"][STANDALONE] = "1999-01-01 00:00:00"
    store.set_meta(con, doc_id, old)
    zotero.run(lib, con)
    assert store.get_document(con, doc_id, max_chars=0)["title"] == (
        "SID GUTS controls schematic"
    )


def test_limit_stops_early(con: sqlite3.Connection, lib: zotero.Library) -> None:
    report = zotero.run(lib, con, limit=3)
    assert sum(report.actions.values()) == 3
    report = zotero.run(lib, con)  # the rest follows on the next run
    assert report.actions["skipped"] == 3 and _n_docs(con) == N_DOCS


def test_missing_file_is_reported_not_fatal(
    con: sqlite3.Connection, tmp_path: Path
) -> None:
    import shutil

    broken = tmp_path / "zotero"
    shutil.copytree(FIXTURE, broken)
    shutil.rmtree(broken / "storage" / AMBRITS)
    lib = zotero.open_library(broken, tmp_path / "w")
    try:
        inv = zotero.inventory(lib)
        assert [k for k, _ in inv.missing_files] == [AMBRITS]
        # the parent has no other file, so it survives as a metadata-only
        # document, next to the one metadata-only item the fixture has
        assert inv.planned_by_kind["metadata"] == 2
        report = zotero.run(lib, con)
    finally:
        lib.close()
    assert report.actions["missing"] == 1 and report.errors == []
    doc = store.get_document(con, _docs_by_key(con)["GMY9D9QD"])
    assert doc["mime"] == "text/plain" and doc["meta"]["zotero"]["kind"] == "metadata"
    assert "POLYNOMIAL TRANSITION REGIONS" in doc["text"]
