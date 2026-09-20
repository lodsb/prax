"""Structure-aware chunking: element kinds, heading paths, pages, locators."""

from __future__ import annotations

import sqlite3
from itertools import pairwise
from pathlib import Path

import pytest

from prax import chunking, parsers, store

FIXTURE = Path(__file__).parent / "fixtures" / "zotero"

DOC = """\
Title line before any heading

# 1 Introduction

First paragraph of the introduction, short.

Second paragraph, also short.

## 1.1 Data

**Table 1:** Measured values.

| name | value |
|---|---|
| a | 1 |
| b | **2** |

Paragraph after the table.

**Figure 2:** A schematic of the pipeline.

```python
def keep_whole(samples):
    # a real listing, long enough to stay a code chunk of its own
    total = 0.0
    for s in samples:
        total += s * s
    return (total / len(samples)) ** 0.5


print(keep_whole([0.1, 0.2, 0.3]))
```

--- end of page.page_number=1 ---

# 2 Method

Text on the second page.
"""


def _kinds(chunks: list[chunking.Chunk]) -> list[str]:
    return [c.kind for c in chunks]


def test_locator_invariant_and_kinds() -> None:
    cs = chunking.chunk(DOC)
    for c in cs:
        assert c.text == DOC[c.char_start : c.char_end]
    assert _kinds(cs) == ["text", "text", "table", "text", "figure", "code", "text"]
    starts = [c.char_start for c in cs]
    assert starts == sorted(starts)
    for a, b in pairwise(cs):
        assert a.char_end <= b.char_start  # never overlapping


def test_heading_paths_and_pages() -> None:
    cs = chunking.chunk(DOC)
    assert cs[0].heading == [] and cs[0].page == 1
    assert cs[1].heading == ["1 Introduction"]
    assert cs[1].text.startswith("First paragraph") and "Second paragraph" in cs[1].text
    assert cs[2].heading == ["1 Introduction", "1.1 Data"]
    assert cs[-1].heading == ["2 Method"] and cs[-1].page == 2
    assert all(c.page == 1 for c in cs[:-1])
    assert not any("end of page" in c.text for c in cs)


def test_table_chunk_has_caption_and_grid() -> None:
    table = next(c for c in chunking.chunk(DOC) if c.kind == "table")
    assert table.text.startswith("**Table 1:**") and table.text.endswith(
        "| b | **2** |"
    )
    assert table.data == {"header": ["name", "value"], "rows": [["a", "1"], ["b", "2"]]}
    assert table.locator() == {
        "char_start": table.char_start,
        "char_end": table.char_end,
        "page": 1,
    }


def test_caption_after_table_is_folded_in() -> None:
    doc = (
        "| x | y |\n|---|---|\n| 1 | 2 |\n\nTable 3: caption below.\n\n"
        + "Next paragraph.\n"
    )
    cs = chunking.chunk(doc)
    assert _kinds(cs) == ["table", "text"]
    assert cs[0].text.endswith("Table 3: caption below.")
    assert cs[1].text == "Next paragraph."


def test_text_spans_pages_and_running_headers_do_not_fragment() -> None:
    doc = (
        "Body of a section that continues over the page break.\n\n"
        "--- end of page.page_number=3 ---\n\n"
        "204 – MANUAL\n\n"
        "and this sentence finishes the thought on the next page.\n\n"
        "--- end of page.page_number=4 ---\n\n"
        "# Next section\n\nFresh start.\n"
    )
    cs = chunking.chunk(doc)
    assert [c.kind for c in cs] == ["text", "text"]
    assert cs[0].page == 3 and "204 – MANUAL" in cs[0].text
    assert cs[0].text.endswith("next page.")  # the marker line sits inside
    assert cs[1].page == 5 and cs[1].heading == ["Next section"]
    # a substantial chunk still ends at the page break
    long = (
        "Long paragraph. " * 30
        + "\n\n--- end of page.page_number=1 ---\n\n"
        + "Next page, also substantial. " * 20
    )
    cs = chunking.chunk(long)
    assert [c.page for c in cs] == [1, 2] and "end of page" not in cs[0].text


def test_small_code_folds_into_text_large_code_stands_alone() -> None:
    small = "Call it like this:\n\n```\nfoo()\n```\n\nand carry on.\n"
    cs = chunking.chunk(small)
    assert [c.kind for c in cs] == ["text"] and "foo()" in cs[0].text
    big = "Intro.\n\n```\n" + "\n".join(f"x{i} = {i}" for i in range(60)) + "\n```\n"
    kinds = [c.kind for c in chunking.chunk(big)]
    assert kinds == ["text", "code"]


def test_long_paragraph_is_windowed_with_overlap() -> None:
    para = "".join(f"w{i} " for i in range(800))  # ~4000 chars, one paragraph
    cs = chunking.chunk(para)
    assert len(cs) > 1 and all(c.kind == "text" for c in cs)
    joined = "".join(
        c.text[chunking.OVERLAP :] if i else c.text for i, c in enumerate(cs)
    )
    assert joined == para.strip() or joined == para
    assert cs[1].text[: chunking.OVERLAP] == cs[0].text[-chunking.OVERLAP :]
    assert chunking.windows("") == []


def test_paragraphs_group_up_to_target() -> None:
    paras = "\n\n".join(f"p{i} " + "x" * 400 for i in range(6))  # 6 x ~403 chars
    cs = chunking.chunk(paras)
    assert all(len(c.text) <= chunking.TARGET_CHARS for c in cs)
    assert 2 <= len(cs) <= 3
    assert "".join(c.text for c in cs).count("p") == 6


def test_plain_text_and_empty() -> None:
    assert chunking.chunk("") == []
    cs = chunking.chunk("just one line")
    assert len(cs) == 1 and cs[0].kind == "text" and cs[0].heading == []


def test_parse_table_strips_emphasis_and_alignment_row() -> None:
    grid = chunking.parse_table("| **A** | B |\n|:--|--:|\n| 1 | 2 |\n")
    assert grid == {"header": ["A", "B"], "rows": [["1", "2"]]}


# ----------------------------------------------------------------- store


def test_index_text_stores_structure(con: sqlite3.Connection) -> None:
    doc_id = store.ingest_text(con, DOC, title="structured")["doc_id"]
    hits = store.search(con, "measured values")
    assert hits and hits[0]["kind"] == "table"
    assert hits[0]["heading"] == ["1 Introduction", "1.1 Data"] and hits[0]["page"] == 1
    assert store.search(con, "measured values", kind="text") == [] or all(
        h["kind"] == "text" for h in store.search(con, "measured values", kind="text")
    )
    with pytest.raises(ValueError):
        store.search(con, "x", kind="audio")
    chunk = store.get_chunk(con, hits[0]["chunk_id"])
    assert chunk["doc_id"] == doc_id and chunk["data"]["rows"] == [
        ["a", "1"],
        ["b", "2"],
    ]
    doc = store.get_document(
        con,
        doc_id,
        offset=chunk["locator"]["char_start"],
        max_chars=chunk["locator"]["char_end"] - chunk["locator"]["char_start"],
    )
    assert doc["text"] == chunk["text"]  # locator addresses the artifact exactly
    assert store.get_chunk(con, 999_999) is None


def test_rechunk_rebuilds_legacy_rows(con: sqlite3.Connection) -> None:
    doc_id = store.ingest_text(con, DOC, title="legacy")["doc_id"]
    con.execute(
        "UPDATE chunks SET kind = NULL, locator = NULL WHERE doc_id = ?", (doc_id,)
    )
    con.commit()
    assert store.search(con, "measured values")[0]["kind"] is None
    n = store.rechunk(con, doc_id)
    assert n == 7 and store.search(con, "measured values")[0]["kind"] == "table"
    empty = store.register(con, b"%PDF", mime="application/pdf")["doc_id"]
    assert store.rechunk(con, empty) == 0
    with pytest.raises(KeyError):
        store.rechunk(con, 999_999)


@pytest.mark.skipif(
    not parsers.by_name("pymupdf4llm").available(), reason="pymupdf4llm not installed"
)
def test_real_pdf_yields_pages_tables_and_figures() -> None:
    pdf = next((FIXTURE / "storage" / "PTHEK9QS").glob("*.pdf")).read_bytes()
    cs = chunking.chunk(parsers.by_name("pymupdf4llm")(pdf))
    kinds = {c.kind for c in cs}
    assert {"text", "table", "figure"} <= kinds
    assert max(c.page for c in cs) == 8
    table = next(c for c in cs if c.kind == "table")
    assert table.data["rows"] and table.heading


def test_a_re_read_keeps_the_unchanged_chunks_and_their_vectors(
    con: sqlite3.Connection,
) -> None:
    """A chunk whose text did not change keeps its id (and so its embedding
    row); the changed and new ones are re-embedded, the gone ones deleted."""
    doc_id = store.ingest_text(con, DOC, title="kept")["doc_id"]
    before = {c["text"]: c["chunk_id"] for c in store.list_chunks(con, doc_id)}
    con.executemany(
        "INSERT INTO chunk_embeddings (chunk_id, model) VALUES (?, 'm')",
        [(i,) for i in before.values()],
    )
    con.commit()
    # a figure line added after the introduction: one chunk changes, one appears
    text = DOC.replace(
        "## 1.1 Data", "![A plot](figure:" + "ab" * 32 + ")\n\n## 1.1 Data", 1
    )
    assert text != DOC
    store.index_text(con, doc_id, text)
    after = {c["text"]: c["chunk_id"] for c in store.list_chunks(con, doc_id)}
    same = [t for t in after if t in before]
    assert same and all(after[t] == before[t] for t in same)
    embedded = {
        r[0] for r in con.execute("SELECT chunk_id FROM chunk_embeddings").fetchall()
    }
    assert {before[t] for t in same} <= embedded
    changed = [after[t] for t in after if t not in before]
    assert changed and not (set(changed) & embedded)  # the new chunks wait for a vector
    gone = [before[t] for t in before if t not in after]
    assert not (set(gone) & embedded)  # deleted chunks took their rows along
    seqs = [c["seq"] for c in store.list_chunks(con, doc_id)]
    assert seqs == list(range(len(seqs)))
    # the same text again changes nothing at all
    ids = [c["chunk_id"] for c in store.list_chunks(con, doc_id)]
    store.index_text(con, doc_id, text)
    assert [c["chunk_id"] for c in store.list_chunks(con, doc_id)] == ids


def test_a_display_equation_is_a_chunk_of_its_own() -> None:
    """A parser that reads mathematics writes a display equation as its own
    line; it becomes a formula chunk with the LaTeX and the number the prose
    refers to it by. Inline maths stays in the sentence it belongs to: a
    chunk is a region of the artifact and cannot tear one."""
    wave = (
        "$$\\frac{a-b}{2R} = I_s \\left( e^{\\frac{a+b}{2V_T}} - 1"
        " \\right). \\quad (4)$$"
    )
    parts = [
        "# A paper with equations",
        "## 2. The model",
        "The diode current follows Shockley's law,",
        "$$i = I_s \\left( e^{v/V_T} - 1 \\right), \\quad (1)$$",
        "where $i$ is the current and $v$ the voltage across it.",
        "Rearranging (1) in the wave domain gives",
        wave,
        "$$\\rightarrow$$",
        "which is solved with the Lambert W function.",
    ]
    text = "\n\n".join(parts)
    chunks = chunking.chunk(text)
    for c in chunks:  # the invariant every chunk keeps
        assert c.text == text[c.char_start : c.char_end]
    formulas = [c for c in chunks if c.kind == "formula"]
    assert len(formulas) == 2  # the arrow states no relation: not a formula
    first = formulas[0]
    assert first.data["latex"] == "i = I_s \\left( e^{v/V_T} - 1 \\right)"
    assert first.data["number"] == "1" and first.data["readings"] == []
    assert first.heading == ["A paper with equations", "2. The model"]
    assert formulas[1].data["number"] == "4"
    # the sentence with inline maths is text, and keeps it
    inline = next(c for c in chunks if c.kind == "text" and "$i$" in c.text)
    assert "where $i$ is the current" in inline.text
    # and the fragment stayed in the prose rather than becoming a chunk
    assert any("\\rightarrow" in c.text for c in chunks if c.kind == "text")


def test_a_formula_carries_what_a_model_read_in_it() -> None:
    """The reading is written under the equation the way a figure's is, and
    the chunk is the two together."""
    text = (
        "# Notes\n\n"
        "$$H(s) = \\frac{1}{1 + sRC}, \\quad (2)$$\n"
        "*Formula, as read by a-model:* the transfer function of a first-order"
        " lowpass, with the cutoff at one over RC.\n\n"
        "The pole is real and negative.\n"
    )
    chunks = chunking.chunk(text)
    formula = next(c for c in chunks if c.kind == "formula")
    assert formula.data["latex"] == "H(s) = \\frac{1}{1 + sRC}"
    assert formula.data["number"] == "2"
    assert formula.data["readings"] == [
        {
            "model": "a-model",
            "text": (
                "the transfer function of a first-order lowpass, with the"
                " cutoff at one over RC."
            ),
        }
    ]
    assert "*Formula, as read by" in formula.text
    assert "formula" in chunking.KINDS


def test_what_counts_as_a_formula() -> None:
    """A relation, or an expression long enough to stand on its own; a
    symbol a parser lifted out of a diagram is neither."""
    assert chunking._is_formula("$$E = mc^2$$")  # short, but it states one
    assert chunking._is_formula(r"$$\sum_{n=0}^{N-1} x[n] e^{-j 2 \pi k n / N}$$")
    assert not chunking._is_formula("$$\rightarrow K$$")
    assert not chunking._is_formula("$$x$$")
    assert not chunking._is_formula("text $$x = 1$$ more")  # not alone on its line


def test_a_reference_list_is_one_chunk_per_entry() -> None:
    """Under a References heading each entry is a reference chunk over the
    artifact's own characters, with what it names in data; a wrapped
    continuation joins its entry; prose before the first entry is text;
    Elsevier's one paragraph is cut at its inline numbers."""
    text = (
        "# A paper\n\nSome prose that cites [1] and [2].\n\n"
        "# 7. References\n\n"
        "The works cited, in order of appearance.\n\n"
        "- [1] D. Turnbull, L. Barrington, and G. Lanckriet, \u201cFive approaches to"
        " collecting tags for music,\u201d in _Proc. ISMIR_, pp. 225\u2013230,"
        " 2008.\n\n"
        "- [2] E. Pampalk, \u201cIslands of music: Analysis, organization, and"
        " visualization of music archives,\u201d Master\u2019s thesis,\n\n"
        "Vienna University of Technology, Vienna, Austria, December 2001.\n\n"
        "[3] [G. Yu, M. Yu, C. Xu, Synchroextracting transform, IEEE Trans. Ind."
        " Electron. 64 \\(10\\) \\(2017\\) 8042\u20138054.](http://refhub.elsevier.com/"
        "S0888-3270(18)30390-X/h0095) [4] [K. Dragomiretskiy, D. Zosso, Variational"
        " mode decomposition, IEEE Trans. Signal Process. 62 \\(3\\) \\(2014\\)"
        " 531\u2013544.](http://refhub.elsevier.com/S0888-3270(18)30390-X/h0100)\n\n"
        "# Appendix\n\nMore prose.\n"
    )
    chunks = chunking.chunk(text)
    assert [c.kind for c in chunks] == [
        "text",
        "text",
        "reference",
        "reference",
        "reference",
        "reference",
        "text",
    ]
    refs = [c for c in chunks if c.kind == "reference"]
    assert all(c.heading == ["7. References"] for c in refs)
    assert [c.data["number"] for c in refs] == [1, 2, 3, 4]
    assert refs[0].data["title"] == "Five approaches to collecting tags for music"
    assert refs[0].data["surnames"] == ["Turnbull", "Barrington", "Lanckriet"]
    assert refs[0].data["year"] == 2008 and "doi" not in refs[0].data
    assert refs[1].text.endswith("December 2001.") and refs[1].data["year"] == 2001
    assert refs[1].data["title"].startswith("Islands of music: Analysis")
    assert refs[2].text.startswith("[3] [G. Yu") and refs[2].data["year"] == 2017
    assert refs[3].data["title"] == "Variational mode decomposition"
    assert refs[3].data["surnames"] == ["Dragomiretskiy", "Zosso"]
    assert chunks[1].text == "The works cited, in order of appearance."
    for c in chunks:
        assert c.text == text[c.char_start : c.char_end]
    assert "reference" in chunking.KINDS
