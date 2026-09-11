"""Title repair: the classifier, the recase rule, the model prompt and parse,
and ``store.retitle`` with history, entity follow-up and the importer guard."""

from __future__ import annotations

import sqlite3

import pytest

from prax import store, titles
from prax.importers import zotero


@pytest.mark.parametrize(
    ("title", "why"),
    [
        ("", "empty"),
        ("1176thesis.pdf", "filename"),
        ("1176sch.gif", "filename"),
        ("00a7359a9bbd7c997ec7ee38c86aa41d-l01.pdf", "filename"),
        ("Korrektur_HA5.pdf.", "filename"),
        ("www.silicore.net/wishbone.htm", "filename"),
        ("Unknown - 1992 - No Title.pdf", "filename"),
        ("Unknown - 2002 - No Title", "zotero-auto"),
        ("TOWARDS MODELING AND DECOMPOSING LOOP-BASED ELECTRONIC MUSIC", "caps"),
        ("Feedback delay networks", None),
        ("LRTFS", None),
        ("An FFT-based approach", None),
    ],
)
def test_needs_title(title: str, why: str | None) -> None:
    assert titles.needs_title(title) == why


def test_repaired_titles_are_kept() -> None:
    assert titles.needs_title("x.pdf", {"title_source": "local:qwen"}) is None
    assert titles.needs_title("x.pdf", {"title_source": "zotero"}) == "filename"


def test_recase() -> None:
    assert (
        titles.recase("TOWARDS MODELING AND DECOMPOSING LOOP-BASED ELECTRONIC MUSIC")
        == "Towards Modeling and Decomposing Loop-Based Electronic Music"
    )
    assert (
        titles.recase("A DIFFUSION-INSPIRED STRATEGY FOR DSP: THE FFT IN 3D")
        == "A Diffusion-Inspired Strategy for DSP: The FFT in 3D"
    )
    assert (
        titles.recase("IMPROVED FIR FILTERS (PART II)")
        == "Improved FIR Filters (Part II)"
    )


def test_head_and_heading() -> None:
    text = (
        "# **Signals through systems**\n\n## Overview\n\ntext --- end of page."
        "page_number=1 --- <!-- Start of picture text -->noise<br>more<!-- End of"
        " picture text --> next   line\n"
    )
    h = titles.head(text)
    assert "picture" not in h and "page_number" not in h and "**" not in h
    assert h.startswith("# Signals through systems\n## Overview")
    assert titles.first_heading(text) == "Signals through systems"
    assert titles.first_heading("## 5. Iterative Methods\n# Real title") is None
    assert titles.first_heading("# **Table of Contents**") is None
    assert titles.first_heading("no headings") is None


def test_pdf_meta_title() -> None:
    assert titles.pdf_meta_title("Microsoft Word - signals.doc") is None
    assert titles.pdf_meta_title("folien.dvi") is None
    assert (
        titles.pdf_meta_title("HALion – Operation Manual")
        == "HALion – Operation Manual"
    )
    assert titles.pdf_meta_title("  ") is None


def test_parse_and_acceptable() -> None:
    assert (
        titles.parse('Title: "The BeatBearing: a Tangible Rhythm Sequencer"\n')
        == "The BeatBearing: a Tangible Rhythm Sequencer"
    )
    assert (
        titles.parse("document: What's New in LLVM<tool_call>") == "What's New in LLVM"
    )
    assert titles.parse("  \n") is None
    assert titles.acceptable("The BeatBearing", filename="beat.pdf")
    assert not titles.acceptable("beat.pdf", filename="x")
    assert not titles.acceptable("Introduction", filename=None)
    assert not titles.acceptable("unset", filename=None)


def test_tidy_recases_scan_damage() -> None:
    assert (
        titles.tidy("IntervIew MIt tontechnIk-Legende gerhard SteInke")
        == "Interview mit Tontechnik-Legende Gerhard Steinke"
    )
    assert titles.tidy("What's New in LLVM") == "What's New in LLVM"
    assert titles.tidy("Using MIDI and OSC in iOS") == "Using MIDI and OSC in iOS"


def test_printed_confidence() -> None:
    text = "# Embodied creativity\nAlex McLean, Goldsmiths"
    assert titles.printed("Embodied creativity", text) == "high"
    assert titles.printed("Lecture notes on synthesis", text) == "low"


class FakeRuntime:
    name = "local:fake"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, object]] = []

    def chat(
        self, system: str, user: str, *, grammar: str | None = None, **kw: object
    ) -> tuple[str, dict[str, int]]:
        self.calls.append({"system": system, "user": user, "grammar": grammar, **kw})
        return self.reply, {"input_tokens": 300, "output_tokens": 20}


def test_guess_title_prompt_and_result() -> None:
    rt = FakeRuntime("Embodied creativity\n")
    g = titles.guess_title(
        rt,
        "# Embodied creativity\nAlex McLean",
        filename="Mclean - 2009.pdf",
        heading="Embodied creativity",
        pdf_title=None,
    )
    assert g is not None and g.title == "Embodied creativity"
    assert g.confidence == "high" and g.usage["output_tokens"] == 20
    call = rt.calls[0]
    user = str(call["user"])
    assert "File name: Mclean - 2009.pdf" in user and "First heading" in user
    assert call["grammar"] is None and call["stop"] == ["\n"]
    assert titles.guess_title(FakeRuntime("x.pdf"), "t", filename="x.pdf") is None


def test_retitle_keeps_history_and_moves_entity(con: sqlite3.Connection) -> None:
    doc = store.ingest_text(con, "some paper text " * 30, title="abc123.pdf")["doc_id"]
    store.link(
        con,
        store.Edge("abc123.pdf", "paper", "proposes", "Thing", "method"),
        source_doc=doc,
        producer="test",
    )
    r = store.retitle(
        con, doc, "  A Real   Title ", source="local:fake", run="t1", confidence="high"
    )
    assert r["changed"] and r["entity"] == "renamed" and r["title"] == "A Real Title"
    d = store.get_document(con, doc, max_chars=0)
    assert d["title"] == "A Real Title"
    assert d["meta"]["title_source"] == "local:fake" and d["meta"]["title_run"] == "t1"
    assert d["meta"]["title_history"][0]["title"] == "abc123.pdf"
    assert d["meta"]["title_confidence"] == "high"
    # the paper entity was renamed, so its edges follow
    assert (
        con.execute(
            "SELECT count(*) FROM entities WHERE name = 'abc123.pdf'"
        ).fetchone()[0]
        == 0
    )
    facts = store.document_facts(con, [doc])[doc]
    assert facts == [{"rel": "proposes", "name": "Thing", "type": "method"}]
    # searchable by the new title through the field
    assert store.search(con, "Real Title")[0]["doc_id"] == doc
    # same title again: no change
    assert not store.retitle(con, doc, "A Real Title", source="x")["changed"]
    assert titles.needs_title(d["title"], d["meta"]) is None


def test_retitle_merges_into_existing_entity(con: sqlite3.Connection) -> None:
    a = store.ingest_text(con, "text " * 30, title="paper.pdf")["doc_id"]
    store.link(
        con, store.Edge("paper.pdf", "paper", "about", "X", "concept"), source_doc=a
    )
    store.link(con, store.Edge("Real", "paper", "about", "Y", "concept"))
    r = store.retitle(con, a, "Real", source="s")
    assert r["entity"] == "merged"
    old = con.execute(
        "SELECT canonical_id FROM entities WHERE name = 'paper.pdf'"
    ).fetchone()
    real = con.execute(
        "SELECT id FROM entities WHERE name = 'Real' AND type = 'paper'"
    ).fetchone()
    assert old["canonical_id"] == real["id"]


def test_retitle_leaves_shared_entity_alone(con: sqlite3.Connection) -> None:
    a = store.ingest_text(con, "one " * 30, title="Unknown - No Title.pdf")["doc_id"]
    store.ingest_text(con, "two " * 30, title="Unknown - No Title.pdf")
    store.link(
        con,
        store.Edge("Unknown - No Title.pdf", "paper", "about", "X", "concept"),
        source_doc=a,
    )
    r = store.retitle(con, a, "Named", source="s")
    assert r["changed"] and r["entity"] is None
    assert (
        con.execute(
            "SELECT count(*) FROM entities WHERE name = 'Unknown - No Title.pdf'"
        ).fetchone()[0]
        == 1
    )


def test_retitle_errors(con: sqlite3.Connection) -> None:
    with pytest.raises(KeyError):
        store.retitle(con, 999, "x", source="s")
    doc = store.ingest_text(con, "t " * 30, title="t")["doc_id"]
    with pytest.raises(ValueError):
        store.retitle(con, doc, "   ", source="s")


def test_importer_keeps_repaired_title() -> None:
    assert zotero.title_repaired({"title_source": "local:qwen"})
    assert not zotero.title_repaired({"title_source": "zotero"})
    assert not zotero.title_repaired({})


def test_init_db_refuses_a_newer_store(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA user_version = 999")
    with pytest.raises(RuntimeError, match="schema version 999"):
        store.init_db(con)
