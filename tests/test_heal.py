"""The heal pass: the damage that recurs is named, found and repaired
through the store's own rules — an edge is ended, never deleted."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prax import store


def _link(con: sqlite3.Connection, src: str, dst: str, rel: str = "extends") -> int:
    doc = store.ingest_text(con, f"{src} and {dst} " * 30, title=f"{src}-{dst}")
    return store.link(
        con,
        store.Edge(src, "concept", rel, dst, "concept"),
        source_doc=doc["doc_id"],
        producer="test",
    )


def test_every_ailment_says_what_it_is(con: sqlite3.Connection) -> None:
    seen = store.health(con)
    assert seen["found"] == 0
    names = [a["name"] for a in seen["ailments"]]
    assert "placeholder-entities" in names and "self-edges" in names
    for ailment in seen["ailments"]:
        assert ailment["what"] and ailment["fix"] and ailment["count"] == 0
        assert isinstance(ailment["repairable"], bool)


def test_placeholder_entities_are_found_and_their_edges_ended(
    con: sqlite3.Connection,
) -> None:
    junk = _link(con, "source name", "granular synthesis")
    good = _link(con, "wave digital filter", "granular synthesis")
    found = store.health(con, only=["placeholder-entities"])["ailments"][0]
    assert found["count"] == 1
    assert found["examples"][0]["name"] == "source name"
    assert found["examples"][0]["edges"] == 1

    done = store.heal(con, only=["placeholder-entities"])
    assert done["placeholder-entities"] == {"found": 1, "repaired": 1}
    assert store.health(con, only=["placeholder-entities"])["ailments"][0]["count"] == 0
    # ended, not deleted: the row is still there with a valid_to (invariant 8)
    row = con.execute("SELECT valid_to FROM edges WHERE id = ?", (junk,)).fetchone()
    assert row["valid_to"] is not None
    assert (
        con.execute("SELECT valid_to FROM edges WHERE id = ?", (good,)).fetchone()[
            "valid_to"
        ]
        is None
    )


def test_reference_numbers_and_unnamed_entities(con: sqlite3.Connection) -> None:
    _link(con, "[12]", "room acoustics")
    _link(con, "a whole citation as a name " * 20, "room acoustics")
    _link(con, "MIDI", "room acoustics")  # short, but a name
    assert (
        store.health(con, only=["reference-number-entities"])["ailments"][0]["count"]
        == 1
    )
    assert store.health(con, only=["unnamed-entities"])["ailments"][0]["count"] == 1
    done = store.heal(con, only=["reference-number-entities", "unnamed-entities"])
    assert done["reference-number-entities"]["repaired"] == 1
    assert done["unnamed-entities"]["repaired"] == 1
    assert [e["src"] for e in store.traverse(con, "room acoustics")] == ["MIDI"]


def test_self_edges_are_what_a_merge_leaves_behind(con: sqlite3.Connection) -> None:
    _link(con, "nonnegative matrix factorization", "NMF", rel="extends")
    duplicate = store._entity_id(con, "NMF", "concept")
    survivor = store._entity_id(con, "nonnegative matrix factorization", "concept")
    store.merge_entities(con, duplicate, survivor)
    found = store.health(con, only=["self-edges"])["ailments"][0]
    assert found["count"] == 1
    assert store.heal(con, only=["self-edges"])["self-edges"]["repaired"] == 1
    assert store.health(con, only=["self-edges"])["ailments"][0]["count"] == 0


def test_what_is_written_after_a_retirement_is_found(
    con: sqlite3.Connection,
) -> None:
    """Retiring a document ends its edges and drops its queue. A pass that
    was already reading it writes afterwards, and that is what these two
    ailments are for."""
    doc = store.ingest_text(con, "a captured page " * 40, title="Page")["doc_id"]
    store.retire_document(con, doc, reason="a duplicate capture")
    store.link(
        con,
        store.Edge("a", "concept", "extends", "b", "concept"),
        source_doc=doc,
        producer="a pass that was already running",
    )
    store.queue_review(
        con,
        src="a",
        src_type="concept",
        rel="invented_relation",
        dst="b",
        dst_type="concept",
        reason="unmapped: invented_relation",
        source_doc=doc,
    )
    assert (
        store.health(con, only=["edges-of-retired-documents"])["ailments"][0]["count"]
        == 1
    )
    assert (
        store.health(con, only=["review-of-retired-documents"])["ailments"][0]["count"]
        == 1
    )
    done = store.heal(
        con, only=["edges-of-retired-documents", "review-of-retired-documents"]
    )
    assert done["edges-of-retired-documents"]["repaired"] == 1
    assert done["review-of-retired-documents"]["repaired"] == 1
    assert store.count_review(con) == 0


def test_a_job_whose_heartbeat_stopped_a_day_ago_is_closed(
    con: sqlite3.Connection,
) -> None:
    job = store.job_start(con, "extract", host="another-machine", pid=0)
    assert store.health(con, only=["stale-jobs"])["ailments"][0]["count"] == 0
    con.execute(
        "UPDATE jobs SET updated_at = datetime('now', '-2 days') WHERE id = ?", (job,)
    )
    con.commit()
    assert store.health(con, only=["stale-jobs"])["ailments"][0]["count"] == 1
    assert store.heal(con, only=["stale-jobs"])["stale-jobs"]["repaired"] == 1
    assert store.running_jobs(con) == 0


def test_the_reports_say_what_to_do_and_change_nothing(
    con: sqlite3.Connection,
) -> None:
    store.register(con, b"PK\x03\x04 a zip", mime="application/zip", title="x.zip")
    found = store.health(con, only=["documents-without-an-extractor"])["ailments"][0]
    assert found["count"] == 1 and not found["repairable"]
    assert found["examples"][0]["mime"] == "application/zip"
    done = store.heal(con, only=["documents-without-an-extractor"])
    assert "look at" in done["documents-without-an-extractor"]


def _pdf(pages: int, text: str = "") -> bytes:
    """A PDF of that many pages, the text on the first one only (a scan
    whose text layer is the cover's), or blank."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if i == 0 and text:
            page.insert_text((72, 72), text)
    return doc.tobytes()


def test_a_scan_read_as_its_cover_is_thin(con: sqlite3.Connection) -> None:
    # a six-page scan whose only text is its cover, taken for the book
    scan = store.register(con, _pdf(6, "A Cover"), mime="application/pdf")["doc_id"]
    store.index_text(con, scan, "A Cover", text_source="pymupdf4llm/1")
    # a six-page paper with a page of text on every page: not thin
    paper = store.register(con, _pdf(6, "A Paper"), mime="application/pdf")["doc_id"]
    store.index_text(con, paper, "words " * 200, text_source="pymupdf4llm/1")
    # a leaflet of one page with a line of text: too short to be a scanned book
    leaf = store.register(con, _pdf(1, "A Leaflet"), mime="application/pdf")["doc_id"]
    store.index_text(con, leaf, "A Leaflet", text_source="pymupdf4llm/1")
    # nothing is weighed until the pages are counted: the check does not
    # open a file (ten thousand of them took 200 s), the repair counts once
    assert store.thin_documents(con) == []
    assert store.uncounted_pages(con) == [scan, paper, leaf]
    found = {a["name"]: a for a in store.health(con)["ailments"]}
    assert found["uncounted-pages"]["count"] == 3
    assert store.heal(con, only=["uncounted-pages"])["uncounted-pages"] == {
        "found": 3,
        "repaired": 3,
    }
    assert store.uncounted_pages(con) == []
    assert store.get_meta(con, leaf)["pages"] == 1
    thin = store.thin_documents(con)
    assert [o["id"] for o in thin] == [scan]
    assert thin[0]["pages"] == 6 and thin[0]["per_page"] < 100
    # the selector reaches it, with the bytes-a-page bar of its own
    assert store.select_for_reading(con, mime="application/pdf", thin=100) == [scan]
    assert store.select_for_reading(con, mime="application/pdf", thin=1) == []
    # the page count is counted from the original when asked to, and taken
    # from meta.pages when a parse recorded one — a paper of 1,200 bytes
    # said to have forty pages is thin after all
    assert store.page_counts(con, [scan, paper]) == {scan: 6, paper: 6}
    meta = store.get_meta(con, paper)
    meta["pages"] = 40
    store.set_meta(con, paper, meta)
    assert store.page_counts(con, [paper]) == {paper: 40}
    assert [o["id"] for o in store.thin_documents(con)] == [paper, scan]
    # and the heal names it, the way on being OCR over all of them
    found = {a["name"]: a for a in store.health(con)["ailments"]}
    ailment = found["thin-texts"]
    assert ailment["count"] == 2 and ailment["examples"][0]["id"] == paper
    assert (
        ailment["examples"][0]["title"] is None and ailment["examples"][1]["id"] == scan
    )
    assert ailment["offers"][0]["thin"] == 100


def test_what_cannot_be_a_document_is_named_and_retired(
    con: sqlite3.Connection,
) -> None:
    fork = store.register(
        con, b"\x00\x05\x16\x07\x00\x02\x00\x00" + b"\x00" * 40, mime="application/pdf"
    )["doc_id"]
    zeros = store.register(con, b"\x00" * 200, mime="application/pdf")["doc_id"]
    link = store.register(con, b"IntxLNK\x01" + b"x" * 30, mime="application/pdf")[
        "doc_id"
    ]
    page = store.register(con, b"<br />\n<html>...", mime="application/pdf")["doc_id"]
    # a link filed as the RTF it pointed at, and an ownCloud server's
    # encrypted copy of a document (2026-09-28, from the NAS)
    rtf_link = store.register(
        con, b"IntxLNK\x01S\x00N\x00B\x00o\x00x", mime="application/rtf"
    )["doc_id"]
    sealed = store.register(
        con,
        b"HBEGIN:oc_encryption_module:OC_DEFAULT_MODULE:cipher:AES-256-CTR:HEND",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )["doc_id"]
    # a real PDF, unread so far, is not judged by its emptiness
    real = store.register(
        con, b"%PDF-1.4\n" + b"1 0 obj << >> endobj\n" * 5, mime="application/pdf"
    )["doc_id"]
    found = {
        a["name"]: a for a in store.health(con, only=["not-documents"])["ailments"]
    }
    rows = found["not-documents"]["examples"]
    assert [r["id"] for r in rows] == [fork, zeros, link, page, rtf_link, sealed]
    assert rows[5]["why"] == "encrypted by an ownCloud server, not readable"
    assert rows[0]["why"].startswith("a macOS resource fork")
    assert rows[1]["why"] == "zeros where the file should be"
    assert rows[3]["why"] == "no PDF header in the first kilobyte"
    done = store.heal(con, only=["not-documents"])
    assert done["not-documents"] == {"found": 6, "repaired": 6}
    assert store.get_meta(con, fork)["retired"]["reason"].startswith("not a document")
    assert store.get_meta(con, real).get("retired") is None
    assert store.health(con, only=["not-documents"])["ailments"][0]["count"] == 0


def _with_figures(con: sqlite3.Connection) -> tuple[int, int]:
    """One document whose figure a model has read, one whose figure
    nobody has read."""
    ref, other = "a" * 64, "b" * 64
    read = store.ingest_text(
        con,
        "# A paper that was read\n\nProse about the plot below.\n\n"
        f"![Figure 1. The plot.](figure:{ref})\n"
        "*Figure, as read by a-vision-model:* a plot of two curves.\n",
        title="A paper that was read",
    )["doc_id"]
    unread = store.ingest_text(
        con,
        "# A paper nobody read\n\nProse about the plot below.\n\n"
        f"![Figure 1. The other plot.](figure:{other})\n",
        title="A paper nobody read",
    )["doc_id"]
    return read, unread


def test_a_figure_nobody_has_read_is_named_with_the_way_on(
    con: sqlite3.Connection,
) -> None:
    """A report: the picture is in the text and nothing says what it
    shows, so a search cannot find it — the offer asks the vision model."""
    read, unread = _with_figures(con)
    found = store.health(con, only=["unread-figures"])["ailments"][0]
    assert found["count"] == 1 and not found["repairable"]
    assert found["examples"][0]["id"] == unread
    assert found["examples"][0]["unread"] == 1
    assert [o["label"] for o in found["offers"]] == [
        "read the captioned ones",
        "read every image",
    ]
    assert found["offers"][0] == {
        "label": "read the captioned ones",
        "extractor": "figures",
        "unread_figures": True,
    }
    # and the two selections are the two halves of the library's figures
    assert store.select_for_reading(con, unread_figures=True) == [unread]
    assert store.select_for_reading(con, read_figures=True) == [read]
    done = store.heal(con, only=["unread-figures"])
    assert "ask" in done["unread-figures"]


def test_an_unknown_ailment_is_refused(con: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="no such ailment"):
        store.health(con, only=["hypochondria"])


def test_the_pass_is_a_job_and_the_door_offers_both_halves(
    client: TestClient, con: sqlite3.Connection
) -> None:
    _link(con, "source name", "granular synthesis")
    looked: dict[str, Any] = client.get("/heal").json()
    by_name = {a["name"]: a for a in looked["ailments"]}
    assert by_name["placeholder-entities"]["count"] == 1
    # the chunks of that document have no vectors yet, and the pass says so
    assert by_name["chunks-without-vectors"]["count"] == 1
    assert client.get("/heal", params={"check": "self-edges"}).json()["found"] == 0
    assert client.get("/heal", params={"check": "nonsense"}).status_code == 400
    done = client.post("/heal", json={"checks": ["placeholder-entities"]}).json()
    assert done["placeholder-entities"]["repaired"] == 1
    after = client.get("/heal", params={"check": "placeholder-entities"}).json()
    assert after["found"] == 0
    recent = client.get("/jobs").json()["recent"]
    assert recent[0]["name"] == "heal" and recent[0]["status"] == "done"


def test_a_name_with_markup_is_cleaned_rather_than_thrown_away(
    con: sqlite3.Connection,
) -> None:
    """What a citation importer leaves behind is repaired by mending the
    name, because the edge under it is real."""
    _link(con, "<i>The Origins of Music</i>", "music cognition")
    _link(con, "TOPOI" + chr(10) + "        and rhetorical competence", "rhetoric")
    found = store.health(con, only=["mangled-names"])["ailments"][0]
    assert found["count"] == 2
    assert {e["cleaned"] for e in found["examples"]} == {
        "The Origins of Music",
        "TOPOI and rhetorical competence",
    }
    assert store.heal(con, only=["mangled-names"])["mangled-names"]["repaired"] == 2
    assert {e["src"] for e in store.traverse(con, "music cognition")} == {
        "The Origins of Music"
    }
    assert store.health(con, only=["mangled-names"])["ailments"][0]["count"] == 0


def test_a_name_with_the_wire_syntax_glued_on_is_cut_back_to_the_name(
    con: sqlite3.Connection,
) -> None:
    """An early pass wrote its triples in the wire format and the parser
    took whole lines as names: 1,612 entities in the library, two of
    them at the top of the likely tier at 0.99. The name before the
    syntax is the name; nothing but syntax has its edges ended."""
    _link(
        con,
        "chord dst_type=concept(confidence=EXTRACTED evidence=The common",
        "harmony",
    )
    _link(con, "no_correspondence(dst=Sundberg_rule_system)", "harmony")
    _link(con, "chord", "voice leading")  # the clean one already there
    _link(con, "evidence=Internal symmetries would collapse these", "harmony")
    found = store.health(con, only=["wire-names"])["ailments"][0]
    assert found["count"] == 3
    assert {e["cleaned"] for e in found["examples"]} == {
        "chord",
        "no_correspondence",
        "",
    }
    assert store.heal(con, only=["wire-names"])["wire-names"]["repaired"] == 3
    srcs = {e["src"] for e in store.traverse(con, "harmony")}
    assert srcs == {"chord", "no_correspondence"}  # cut, merged; the junk ended
    assert {e["src"] for e in store.traverse(con, "voice leading")} == {"chord"}
    assert store.health(con, only=["wire-names"])["ailments"][0]["count"] == 0


def test_twin_documents_retire_into_the_keeper(con: sqlite3.Connection) -> None:
    """A book kept in two prints (4690 and 4366 on 2026-09-17): one title,
    the same text, different bytes. The twin retires into the one with
    more edges; what only the twin held moves over."""
    text = "# Music: A Mathematical Offering\n\n" + "\n\n".join(
        f"Chapter {i}. " + f"The harmonic series and the scale, part {i}. " * 12
        for i in range(80)
    )
    a = store.ingest_text(con, text, title="Music: A Mathematical Offering")["doc_id"]
    b = store.ingest_text(
        con, text + "\n\nSecond printing.", title="Music: A Mathematical Offering"
    )["doc_id"]
    other = store.ingest_text(
        con, "A different book. " * 80, title="Music: A Mathematical Offering"
    )["doc_id"]
    store.link(  # only one of the two has an edge: that one keeps
        con,
        store.Edge(
            "Music: A Mathematical Offering",
            "paper",
            "about",
            "harmonic series",
            "concept",
        ),
        source_doc=b,
        producer="test",
    )
    found = store.health(con, only=["twin-documents"])["ailments"][0]
    assert found["count"] == 1
    twin = found["examples"][0]
    assert twin["id"] == a and twin["duplicate_of"] == b  # b has the edge: keeper
    assert twin["similarity"] >= 0.9
    assert store.heal(con, only=["twin-documents"])["twin-documents"]["repaired"] == 1
    assert store.get_meta(con, a)["retired"]["of"] == b
    assert "retired" not in store.get_meta(con, other)  # a different text keeps
    assert store.health(con, only=["twin-documents"])["ailments"][0]["count"] == 0


def test_cleaning_a_name_merges_into_the_one_already_clean(
    con: sqlite3.Connection,
) -> None:
    _link(con, "The Origins of Music", "music cognition")
    _link(con, "<i>The Origins of Music</i>", "rhetoric")
    assert store.heal(con, only=["mangled-names"])["mangled-names"]["repaired"] == 1
    seen = {e["src"] for e in store.traverse(con, "music cognition")}
    seen |= {e["src"] for e in store.traverse(con, "rhetoric")}
    assert seen == {"The Origins of Music"}  # one entity, both edges on it


def test_a_long_claim_is_left_alone(con: sqlite3.Connection) -> None:
    """A claim is a sentence by definition; only other types are flagged for
    carrying a whole paragraph as a name."""
    long_name = ("a claim that goes on and on " * 20)[:320]
    for etype in ("claim", "paper"):
        store.link(
            con,
            store.Edge(long_name, etype, "contrasts", "reverberation", "concept"),
            producer="test",
        )
    found = store.health(con, only=["unnamed-entities"])["ailments"][0]
    assert found["count"] == 1 and found["examples"][0]["type"] == "paper"


def test_an_extraction_of_a_replaced_text_is_found_and_unstamped(
    con: sqlite3.Connection,
) -> None:
    """A text replaced after its extraction (a reader from before the rule
    that unstamps on the way in): the ailment names the document, the
    repair moves the stamp aside so the extract step selects it again; an
    annotating read after the extraction is not damage."""
    from prax.graph import extraction, ontology
    from prax.parsers import queue

    body = "# A paper on reverb\n\n" + "Feedback delay networks make reverb. " * 40
    replaced = store.ingest_text(con, body, title="Replaced")["doc_id"]
    annotated = store.ingest_text(con, body + " Twice.", title="Annotated")["doc_id"]
    for doc_id in (replaced, annotated):
        result = extraction.StubExtractor().extract(extraction.build_input(con, doc_id))
        extraction.apply(con, doc_id, result, extractor="stub", run="r1")
    # the history entries are what the ailment reads: written as the queue
    # writes them, but around the rule, as a reader from before it did
    queue._record(
        con,
        replaced,
        {
            "extractor": "marker/2.0.0",
            "outcome": "upgraded",
            "at": "2099-01-01T00:00:00Z",
        },
    )
    queue._record(
        con,
        annotated,
        {
            "extractor": "figures/2+m",
            "outcome": "upgraded",
            "at": "2099-01-01T00:00:00Z",
        },
    )
    ailment = next(a for a in store.AILMENTS if a.name == "stale-extractions")
    found = ailment.find(con)
    assert [f["id"] for f in found] == [replaced]
    assert found[0]["read_by"] == "marker/2.0.0" and found[0]["extractor"] == "stub"
    version = ontology.current().version
    assert replaced not in store.select_for_extraction(con, ontology_version=version)
    assert ailment.repair is not None
    assert ailment.repair(con, found) == 1
    assert replaced in store.select_for_extraction(con, ontology_version=version)
    meta = store.get_meta(con, replaced)
    assert meta["extraction_stale"]["run"] == "r1"
    assert meta["extraction_history"][-1]["superseded_by"] == "marker/2.0.0"
    assert ailment.find(con) == []  # repaired: nothing left to find
    assert store.get_meta(con, annotated).get("extraction")


def test_the_glyph_check_reads_a_text_once(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each document's chunks are read once per text: a second check
    over an unchanged library consults the memo, a re-indexed text is
    read again, a retired document leaves the memo."""
    from prax.store import repair
    from prax.text import glyphs

    repair._glyphs_seen.clear()
    # a text indexed before prax.text.glyphs cleaned every text: the ligature
    # is in the artifact and the chunks
    with monkeypatch.context() as m:
        m.setattr(glyphs, "clean", lambda t: t)
        bad = store.ingest_text(con, "a ﬁne ligature " * 10, title="lig")["doc_id"]
    ok = store.ingest_text(con, "plain words " * 10, title="ok")["doc_id"]
    assert [d["id"] for d in repair._glyph_documents(con)] == [bad]
    assert set(repair._glyphs_seen) == {bad, ok}
    # nothing changed: the memo answers (a planted lie is believed, which
    # is the proof the chunks are not read again)
    repair._glyphs_seen[ok] = (repair._glyphs_seen[ok][0], True)
    assert [d["id"] for d in repair._glyph_documents(con)] == [bad, ok]
    repair._glyphs_seen[ok] = (repair._glyphs_seen[ok][0], False)
    # the repair cleans the text: a new artifact, read again, clean now
    assert (
        repair.heal(con, only=["unmapped-glyphs"])["unmapped-glyphs"]["repaired"] == 1
    )
    assert repair._glyph_documents(con) == []
    store.retire_document(con, ok, reason="test")
    repair._glyph_documents(con)
    assert ok not in repair._glyphs_seen


def test_a_truncated_pdf_records_no_pages_rather_than_nagging(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PDF MuPDF opens with no pages at all (a truncated download whose
    text came from somewhere else) records zero. It used to be skipped,
    so uncounted-pages listed it for ever and repaired nothing."""
    # count_pages asks page_counts in its own part of the documents package
    from prax.store.documents import readings as docs

    doc = store.register(con, b"%PDF-1.4 truncated", mime="application/pdf", title="t")[
        "doc_id"
    ]
    store.index_text(con, doc, "text from the cache " * 20)
    whole = store.register(con, b"%PDF-1.4 whole", mime="application/pdf", title="w")[
        "doc_id"
    ]
    store.index_text(con, whole, "text " * 20)
    assert store.uncounted_pages(con) == [doc, whole]
    monkeypatch.setattr(
        docs, "page_counts", lambda _con, ids, **_kw: {doc: 0, whole: 12}
    )
    assert store.count_pages(con, [doc, whole]) == 2
    assert store.get_meta(con, doc)["pages"] == 0
    assert store.get_meta(con, whole)["pages"] == 12
    assert store.uncounted_pages(con) == []  # neither is asked about again


def test_a_citation_of_the_container_ends(con: sqlite3.Connection) -> None:
    """A paper cites a work, never the volume it appeared in. The prompt
    prevents it; this is what predates the prompt
    (docs/eval/traverse-neighbourhood-2026-09-25.md)."""
    volume = "Proceedings of the International Conference on New Interfaces"
    bad = store.link(
        con,
        store.Edge("A Paper", "paper", "cites", volume, "paper"),
        producer="extraction",
    )
    store.link(
        con,
        store.Edge("A Paper", "paper", "cites", "Wave digital filters", "paper"),
        producer="extraction",
    )

    found = store.health(con, only=["container-citations"])["ailments"][0]
    assert found["count"] == 1
    assert found["examples"][0]["container"] == volume

    store.heal(con, only=["container-citations"])
    live = {e["dst"] for e in store.traverse(con, "A Paper", hops=1)}
    assert live == {"Wave digital filters"}
    # ended, not deleted, and the container itself is untouched
    assert con.execute("SELECT valid_to FROM edges WHERE id = ?", (bad,)).fetchone()[
        "valid_to"
    ]
    kept = con.execute("SELECT type FROM entities WHERE name = ?", (volume,)).fetchone()
    assert kept["type"] == "paper"


def test_the_container_is_never_retyped(con: sqlite3.Connection) -> None:
    """38 of this library's 914 container-named entities are real documents
    someone imported, so their own edges are earned: a blanket retype would
    have ended 3,844 of them."""
    volume = "Proceedings of the 8th Sound and Music Computing Conference"
    store.link(
        con,
        store.Edge(volume, "paper", "about", "gesture", "concept"),
        producer="extraction",
    )
    store.heal(con, only=["container-citations"])
    assert {e["dst"] for e in store.traverse(con, volume, hops=1)} == {"gesture"}


def test_a_slow_graph_walk_is_the_graphs_threshold(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``slow-graph-walks`` times the walks from the most connected
    entities: the threshold of CLAUDE.md, measured rather than counted."""
    _link(con, "reverb", "feedback delay network")
    _link(con, "reverb", "allpass filter")
    assert store.health(con, only=["slow-graph-walks"])["found"] == 0
    # the part that reads it: a name set on the package would not reach it
    monkeypatch.setattr(store.repair.graph, "SLOW_WALK_MS", {1: -1.0})
    found = store.health(con, only=["slow-graph-walks"])["ailments"][0]
    assert found["count"] >= 1 and found["examples"][0]["entity"] == "reverb"


def test_names_that_differ_only_in_spacing_fold_into_the_clean_one(
    con: sqlite3.Connection,
) -> None:
    """``spacing-twins``: the sure tier keeps word boundaries, so "Valhalla
    DSP" and "ValhallaDSP" stayed two. The keeper is the clean spelling,
    not the most connected; a short name, or numbers split apart, is left."""
    _link(con, "Valhalla DSP", "plate reverb")
    _link(con, "Valhalla DSP", "shimmer")
    _link(con, "ValhallaDSP", "a plate")
    accent = "Fakulta" + chr(0xA8) + "t Informatik"  # a PDF's detached accent
    for other in ("a", "b", "c"):
        _link(con, accent, other)
    _link(con, "Fakultät Informatik", "d")
    _link(con, "ws fl", "x")
    _link(con, "wsfl", "y")  # four characters joined: too short to say
    _link(con, "SSL4000", "x")
    _link(con, "SSL 400 0", "y")  # the numbers differ
    found = store.health(con, only=["spacing-twins"])["ailments"][0]
    assert found["count"] == 2
    keep = {r["name"]: [a["name"] for a in r["also"]] for r in found["examples"]}
    assert keep == {
        "Valhalla DSP": ["ValhallaDSP"],
        "Fakultät Informatik": [accent],
    }
    assert store.heal(con, only=["spacing-twins"])["spacing-twins"]["repaired"] == 2
    gone = store._entity_id(con, "ValhallaDSP", "concept")
    row = con.execute(
        "SELECT canonical_id, merged_run FROM entities WHERE id = ?", (gone,)
    ).fetchone()
    assert row["canonical_id"] == store._entity_id(con, "Valhalla DSP", "concept")
    assert store.health(con, only=["spacing-twins"])["ailments"][0]["count"] == 0
    assert store.unmerge_run(con, row["merged_run"]) == 2  # the round, back whole


def test_figure_readings_a_replacing_read_dropped_come_back(
    con: sqlite3.Connection,
) -> None:
    """``lost-figure-readings``: a document read by marker before the fix
    lost the readings under its figures; the check finds them in an
    earlier text and the repair carries them back, no model asked."""
    from prax.parsers import queue

    a = "a" * 64
    prose = "Prose about the method of the paper. " * 10
    doc_id = store.ingest_text(con, f"# Paper\n\n{prose}\n", title="Paper")["doc_id"]
    read = (
        f"# Paper\n\n{prose}\n\n![Figure 1](figure:{a})\n"
        "*Figure, as read by big@host:* Six stacked curves.\n"
    )
    queue.apply_parse(con, doc_id, stamp="figures/1", text=read, keep_source=True)
    # the text as marker left it before the fix, its chunk a figure chunk
    bare = f"# Paper\n\n{prose} By marker.\n\n![Figure 1](figure:{a})\n"
    queue.apply_parse(con, doc_id, stamp="marker/x", text=bare, keep_source=True)
    found = store.health(con, only=["lost-figure-readings"])["ailments"][0]
    assert found["count"] == 1 and found["examples"][0]["readings"] == 1
    out = store.heal(con, only=["lost-figure-readings"])["lost-figure-readings"]
    assert out["repaired"] == 1
    text = store.get_document(con, doc_id)["text"]
    assert "Six stacked curves." in text and "By marker." in text
    assert store.health(con, only=["lost-figure-readings"])["ailments"][0]["count"] == 0


def test_damaged_names_are_mended_or_folded_and_can_be_taken_back(
    con: sqlite3.Connection,
) -> None:
    """``damaged-names``: an accent beside its letter, a type the model
    wrote into the name, a drop cap given twice. A name whose mended form
    another entity carries folds into it; the others are renamed, the old
    name kept as the one it was. ``unmerge_run`` takes the round back."""
    _link(con, "concept=social networks", "a")
    _link(con, "social networks", "b")
    _link(con, "rhythm complexity concept=EXTRACTED", "c")
    _link(con, "f face recognition", "d")
    _link(con, "relative h" + chr(0xA8) + "aufigkeit", "e")
    _link(con, "C compiler", "f")  # a single capital is a name's own letter
    _link(con, "k mismatches", "g")  # and a lowercase one before another word
    found = store.health(con, only=["damaged-names"])["ailments"][0]
    mended = {r["name"]: r["cleaned"] for r in found["examples"]}
    assert mended == {
        "concept=social networks": "social networks",
        "rhythm complexity concept=EXTRACTED": "rhythm complexity",
        "f face recognition": "face recognition",
        "relative h" + chr(0xA8) + "aufigkeit": "relative häufigkeit",
    }
    assert store.heal(con, only=["damaged-names"])["damaged-names"]["repaired"] == 4
    leaked = con.execute(
        "SELECT canonical_id, merged_run FROM entities WHERE name = ?",
        ("concept=social networks",),
    ).fetchone()
    assert leaked["canonical_id"] == store._entity_id(con, "social networks", "concept")
    names = {
        r[0]
        for r in con.execute("SELECT name FROM entities WHERE canonical_id IS NULL")
    }
    assert {"rhythm complexity", "face recognition", "relative häufigkeit"} <= names
    assert store.health(con, only=["damaged-names"])["ailments"][0]["count"] == 0
    # one run, taken back whole: the fold undone, the old names back
    assert store.unmerge_run(con, leaked["merged_run"]) >= 4
    names = {
        r[0]
        for r in con.execute("SELECT name FROM entities WHERE canonical_id IS NULL")
    }
    assert {"concept=social networks", "f face recognition"} <= names


def test_a_models_names_are_mended_as_its_answer_is_read() -> None:
    from prax.graph import extraction

    got = extraction.parse_output(
        {
            "triples": [
                {
                    "src": {"name": "Universit`a di Pisa", "type": "organization"},
                    "rel": "uses",
                    "dst": {"name": "concept=genetic algorithms", "type": "concept"},
                }
            ]
        }
    )
    assert (got.triples[0].src, got.triples[0].dst) == (
        "Università di Pisa",
        "genetic algorithms",
    )
