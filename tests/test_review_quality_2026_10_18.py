"""The quality review of 2026-10-18 (``docs/log.md``): one test per agreed
finding, numbered as the review's report numbers them, each failing on the
code the review read (72710d3 with the safety fixes, 46aa2a7)."""

from __future__ import annotations

from typing import Any

from prax import store


def test_1_a_repair_ends_its_edges_in_a_few_commits(con: Any) -> None:
    """Ending 1,711 duplicate facts committed twice an edge and held the
    write lock 11.4 s; a batch is one commit per ``END_BATCH`` edges, and
    every ending is still recorded for ``restore_run``."""
    from prax.store.repair.common import _invalidate

    E = store.Edge
    ids = [
        store.link(con, E(f"Paper {n}", "paper", "cites", "Paper X", "paper"))
        for n in range(30)
    ]
    for e in ids[:2]:  # ended by another pass first
        store.invalidate_edge(con, e)
    commits: list[str] = []
    con.set_trace_callback(
        lambda sql: commits.append(sql) if sql.strip().upper() == "COMMIT" else None
    )
    try:
        assert _invalidate(con, ids, run="heal:test") == 28
    finally:
        con.set_trace_callback(None)
    assert len(commits) == 1
    assert (
        con.execute(
            "SELECT count(*) FROM edge_endings WHERE run = 'heal:test'"
        ).fetchone()[0]
        == 28
    )
    assert (
        con.execute(
            "SELECT count(*) FROM edges WHERE valid_to IS NULL AND rel = 'cites'"
        ).fetchone()[0]
        == 0
    )


def test_2_one_arxiv_id_wherever_it_is_named() -> None:
    """Five grammars read an arXiv id: a citation kept its version
    (``2501.13484v2`` never met the copy captured from its abstract URL),
    an old-style id was not read at all, and the citation index cut
    ``solv-int/9901001`` down to ``sol``."""
    from prax.capture import inbox
    from prax.store.documents.reads import _work_key
    from prax.text import arxiv, dates, references

    cited = references.parse("[3] A. Smith. Deep things. arXiv:2501.13484v2, 2025.")
    assert cited.arxiv == "2501.13484"
    assert references.parse("[6] arXiv preprint _arXiv:1410.5401_ , 2014.").arxiv == (
        "1410.5401"
    )
    assert inbox.url_ids("https://arxiv.org/abs/2501.13484v2") == {
        "arxiv": "2501.13484"
    }
    old = references.parse("[4] B. Jones. Strings. arXiv:hep-th/9901001, 1999.")
    assert old.arxiv == "hep-th/9901001"
    assert references.parse("[5] arXiv:MATH.gt/0309136v3").arxiv == "math.GT/0309136"
    assert _work_key({"arxiv": "solv-int/9901001v2"}) == "arxiv:solv-int/9901001"
    assert dates.from_arxiv("solv-int/9901001v2") == dates.from_arxiv("hep-th/9901001")
    assert arxiv.canonical("arXiv:2310.08560v4") == "2310.08560"
    assert arxiv.canonical("2310.085601") is None
    assert inbox.paper_meta({"arxiv": "arXiv:1308.3432v2"})["arxiv"] == "1308.3432"
    store.check_mode("latex-source", "math.GT/0309136")


def test_3_a_fix_is_a_reading_and_the_host_gives_marker_its_mode(
    data_dir: Any, monkeypatch: Any
) -> None:
    """A finding's fix was a word ("marker-ocr") decoded in four places:
    the door asked for balanced-ocr on a host set to balanced while the
    Admin button asked for fast-ocr, the LaTeX source had no button, and
    a scan with no text layer was the reader choice's own rule, never a
    finding. Now a fix is a reading, the host's layout is chosen in one
    place, and the bulk request asks each document for its own fix."""
    from fastapi.testclient import TestClient

    from prax.api import app
    from prax.parsers import quality

    monkeypatch.setenv("PRAX_MARKER_MODE", "balanced")
    layer = "pymupdf4llm/1.28.2-r2"
    pdf = "application/pdf"
    bare = {"pages": 4, "scanned": 1.0, "ocr_layer": 0.0, "layer": False}
    (found,) = quality.check(
        "![](figure:ab)\n" * 4, stamp=layer, mime=pdf, profile=bare
    )
    assert found.check == "no-layer" and found.fix == quality.Fix("marker", ocr=True)
    assert quality.reading(found.fix) == ("marker", "balanced-ocr")
    assert found.as_dict()["fix"] == {"extractor": "marker", "ocr": True}
    assert quality.Fix.of({"extractor": "latex-source", "mode": "2501.13484"}) == (
        quality.Fix("latex-source", "2501.13484")
    )
    assert quality.Fix.of("marker-ocr") is None  # a version 4 word asks for nothing
    # one list of the readers that write LaTeX, latex2html among them
    assert quality.writes_latex("latex2html/2") and not quality.writes_latex(layer)

    data_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        con = client.app.state.con

        def flagged(fix: dict[str, Any]) -> int:
            doc = int(
                store.register(con, b"%PDF-1.4 " + str(fix).encode(), mime=pdf)[
                    "doc_id"
                ]
            )
            store.index_text(con, doc, "Words of a paper. " * 30, text_source=layer)
            text_hash = store.get_document(con, doc, max_chars=0)["text_hash"]
            store.set_quality(
                con,
                doc,
                [{"check": "maths-as-text", "count": 4, "fix": fix}],
                version=quality.VERSION,
                text_hash=text_hash,
            )
            return doc

        on_arxiv = flagged({"extractor": "latex-source", "mode": "2501.13484"})
        not_on = flagged({"extractor": "marker"})
        asked = client.post("/readings/bulk", json={"finding": "maths-as-text"})
        assert asked.status_code == 200, asked.text
        assert asked.json()["requested"] == 2
        waiting = {
            d: [(r["extractor"], r.get("mode")) for r in store.pending_readings(con, d)]
            for d in (on_arxiv, not_on)
        }
        assert waiting == {
            on_arxiv: [("latex-source", "2501.13484")],
            not_on: [("marker", "balanced")],
        }
        assert client.post("/readings/bulk", json={}).status_code == 400


def test_7_a_latex2html_text_gets_its_formula_readings(
    con: Any, monkeypatch: Any
) -> None:
    """The formula readings followed marker and the LaTeX source only, a
    list of their own: a latex2html book's display equations were never
    asked for. They follow every reader that writes LaTeX."""
    from prax.capture import pipeline

    monkeypatch.setattr(pipeline, "formulas_are_free", lambda: True)
    doc = int(
        store.ingest_text(
            con,
            "# Maths\n\nSee\n\n$$E = mc^2 \\quad (1)$$\n\nwhich holds. " * 3,
            title="A book",
        )["doc_id"]
    )
    store.set_text_source(con, doc, "latex2html/2")
    got = pipeline.follow_ups(con, doc, stamp="latex2html/2", action="created")
    assert got == "formulas"
    assert [r["extractor"] for r in store.pending_readings(con, doc)] == ["formulas"]


def test_9_the_page_poll_reads_indexes_only(con: Any) -> None:
    """``document_fingerprint`` is asked every 10 s while a document is
    open, and its facts' lookup scanned the whole edges table (22 ms at
    400,000 edges). Every statement it runs is served by an index, and it
    still moves when a fact is ended."""
    doc = int(store.ingest_text(con, "A paper. " * 40, title="A paper")["doc_id"])
    fact = store.Edge("A paper", "paper", "cites", "Paper B", "paper")
    edge = store.link(con, fact, source_doc=doc)
    before = store.document_fingerprint(con, doc)
    statements: list[str] = []
    con.set_trace_callback(statements.append)
    try:
        store.document_fingerprint(con, doc)
    finally:
        con.set_trace_callback(None)
    for sql in statements:
        if " FROM " not in sql.upper():
            continue
        plan = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + sql))
        assert "SCAN" not in plan.replace("COVERING INDEX", ""), (sql, plan)
    store.invalidate_edge(con, edge)
    assert store.document_fingerprint(con, doc) != before


def test_6_world_dates_guards_read_german_and_french() -> None:
    """The world-dates reading hands out German and French sentences (its
    cues are in three languages) and guarded their ends in English only:
    "unbekannt", "die Firma" and "Mitte der 1980er" became dated facts.
    The guards are the lexicon's words, every language's beside the cues."""
    from prax.graph import worlddates

    g = worlddates.guards()
    for name in (
        "unbekannt",
        "inconnu",
        "die Firma",
        "l'entreprise",
        "er",
        "the company",
    ):
        assert g.stand_in(name), name
    for name in ("Mitte der 1980er", "Anfang 1990", "début 1990", "les années 1980"):
        assert g.date_name(name), name
    for name in ("the mid-1980s", "June 1977", "3. Juni 1977", "1984–86"):
        assert g.date_name(name), name
    for name in ("Paris 1968", "Windows 95", "La Société Générale", "Moog Music"):
        assert not g.date_name(name) and not g.stand_in(name), name


def test_5_a_type_written_into_a_name_is_mended_for_every_pack() -> None:
    """``mended_name`` took "concept=" and "method:" out of a name, research's
    types only: "device=Minimoog" and "ingredient=apple" stayed, and the
    heal check that lists what it would mend never saw them."""
    assert store.mended_name("device=Minimoog", "device") == "Minimoog"
    assert store.mended_name("ingredient=apple", "ingredient") == "apple"
    assert store.mended_name("Minimoog device=synth", "device") == "Minimoog"
    assert store.mended_name("concept=social networks", "concept") == "social networks"
    assert store.mended_name("Moog Music", "organization") == "Moog Music"


def test_20_a_german_or_french_biography_is_no_reference() -> None:
    """A biography printed after the list was told by English verbs only;
    a German or French one with two years in it became a reference entry."""
    from prax.text import references

    de = (
        "Anna Beispiel studierte Elektrotechnik in Karlsruhe von 1998 bis 2004 "
        "und promovierte 2009 über Filterbänke. " * 3
    )
    fr = (
        "Jean Exemple a obtenu son doctorat en 2001 à Paris et travaille depuis "
        "2005 sur le traitement du signal. " * 3
    )
    assert references.is_prose(de) and references.is_prose(fr)
    assert not references.is_prose("[3] A. Beispiel. Filterbänke. 2009.")
