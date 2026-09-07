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
    pdf = next((FIXTURE / "storage" / "7656ADFF").glob("*.pdf")).read_bytes()
    cs = chunking.chunk(parsers.by_name("pymupdf4llm")(pdf))
    kinds = {c.kind for c in cs}
    assert {"text", "table", "figure"} <= kinds
    assert max(c.page for c in cs) == 16
    table = next(c for c in cs if c.kind == "table")
    assert table.data["rows"] and table.heading
