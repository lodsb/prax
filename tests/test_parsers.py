"""Extractors and the parse queue, against the Zotero fixture files.

Docling is exercised only when ``PRAX_TEST_DOCLING=1`` (first run downloads
models and takes minutes); everything else runs on every checkout with the
``ingest`` extra installed.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest

from prax import parsers, store
from prax.importers import zotero
from prax.parsers import queue

FIXTURE = Path(__file__).parent / "fixtures" / "zotero"
AMBRITS_PDF = next((FIXTURE / "storage" / "FCEK3EI9").glob("*.pdf"))
SNAPSHOT_HTML = FIXTURE / "storage" / "AYY57KAK" / "iir-hilbert-transformer.html"

needs_pymupdf = pytest.mark.skipif(
    not parsers.by_name("pymupdf4llm").available(), reason="pymupdf4llm not installed"
)
needs_trafilatura = pytest.mark.skipif(
    not parsers.by_name("trafilatura").available(), reason="trafilatura not installed"
)


# ---------------------------------------------------------------- registry


FORUM_NOTE = """Line and Newton method
In my observation, the Newton method over the whole term converges roughly
10-20% faster than the line method. I think I wrote them so that they're
identical otherwise. Adjust ranges to taste.
Urs — Yesterday at 6:10 PM
Yo, this is as far as I can take it this year, example code pasted in.
inline double tickTanhs(const double inSample)
{
double vIn = inSample;
double y1;
do
{
// estimates based on linearised Equations:
double BPE = (-b2*g*g*m1 + vIn*g*m1 - g*m1*s2 + b1*g + s1)/(g*g*m1*m2 + g*k*m1 + 1);
y1 = (BPE - BP);
BP = BPE;
}
while( fabs(y1) > 0.000001f );
return BP;
}
That converges in three iterations for me, which is plenty fast.
Thanks for the pointers, I will try the DK method next week.
"""


def test_plain_fences_code_regions_inside_prose() -> None:
    out = parsers.by_name("plain")(FORUM_NOTE.encode())
    lines = out.split("\n")
    opens = [
        i
        for i, ln in enumerate(lines)
        if ln.startswith("```") and len(ln) > 3 or ln == "```"
    ]
    assert len(opens) == 2, out
    fenced = "\n".join(lines[opens[0] + 1 : opens[1]])
    assert fenced.startswith("inline double tickTanhs") and fenced.endswith("}")
    assert "return BP;" in fenced and "Thanks for the pointers" not in fenced
    assert lines[0] == "Line and Newton method" and lines[-2].startswith("Thanks")
    # the chunker keeps the fenced region as one code chunk
    from prax import chunking

    kinds = [(c.kind, c.text[:20]) for c in chunking.chunk(out)]
    assert [k for k, _ in kinds] == ["text", "code", "text"], kinds
    # prose stays prose; a fenced document is left alone
    prose = "Just a few sentences about reverb. Nothing to fence here.\n" * 3
    assert parsers.by_name("plain")(prose.encode()) == prose
    md = "# Notes\n\n```python\nx = 1\n```\n"
    assert parsers.by_name("plain")(md.encode()) == md
    assert parsers.code_regions(["a = 1;", "b = 2;"]) == []  # too short
    # a block comment is code whatever its lines say
    comment = [
        "/*",
        "EQ1 := g*(Vin - BPE) - LPE",
        "derivative at error:",
        "g^2 + 1",
        "*/",
    ]
    assert parsers.code_regions(comment) == [(0, 5)]
    assert parsers.code_regions(["prose here.", *comment, "x = 1;"]) == [(1, 7)]


def test_plain_fences_source_files_by_extension_or_content() -> None:
    plain = parsers.by_name("plain")
    assert plain.stamp == "plain/1-r3" and plain.hints
    code = b"function y = lim(x, t)\n  y = min(max(x, -t), t);\nend\n"
    out = plain(code, filename="getFilename.m")
    assert out.startswith("```matlab\n") and out.endswith("\nend\n```")
    prose = b"Comment: 11 pages. In Advances in Neural Information Processing Systems"
    assert plain(prose, filename=None) == prose.decode()
    assert (
        plain(code, filename="notes.txt") == code.decode()
    )  # a telling extension wins
    assert plain(b"```python\nx = 1\n```", filename="a.py").startswith("```python\nx")
    assert parsers.code_language("", "x.py") == "python"
    assert parsers.code_language("hello there", None) is None


@pytest.mark.skipif(
    not parsers.by_name("plain").available()
    or importlib.util.find_spec("magika") is None,
    reason="magika not installed",
)
def test_plain_detects_code_without_an_extension() -> None:
    py = (
        b"import numpy as np\n\ndef stft(x, n=1024):\n"
        b"    return np.fft.rfft(x[:n] * np.hanning(n))\n"
    )
    assert parsers.by_name("plain")(py).startswith("```python\n")
    text = b"Time-scale modification of audio is an essential tool in music production."
    assert parsers.by_name("plain")(text) == text.decode()
    note = (  # bibliographic notes look like YAML to a classifier: not code
        b'Comment: "Highlights of Spanish Astrophysics V", Proceedings of the VIII'
        b" Scientific Meeting of the Spanish Astronomical Society (SEA) held in"
        b" Santander, 7-11 July, 2008. Edited by J. Gorgas, L. J. Goicoechea."
    )
    assert parsers.by_name("plain")(note) == note.decode()
    assert parsers.code_language("a: 1", "conf.yaml") == "yaml"  # extension wins


def test_claude_vision_describes_an_image(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from prax.parsers import vision

    seen: dict[str, object] = {}

    class FakeMessages:
        def create(self, **kw):
            seen.update(kw)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="text",
                        text="## What it shows\nA compressor schematic. "
                        + "Detail. " * 60
                        + "\n\n"
                        "## Text in the image\nR12 100k\ngain (handwritten)",
                    )
                ]
            )

    monkeypatch.setattr(
        vision, "CLIENT_FACTORY", lambda: SimpleNamespace(messages=FakeMessages())
    )
    monkeypatch.setenv("PRAX_VISION_MODEL", "claude-test")
    gif = b"GIF89a" + bytes(40)
    ext = parsers.by_name("claude-vision")
    assert ext.explicit_only and ext.accepts("image/gif")
    assert "claude-vision" not in [e.name for e in parsers.candidates("image/gif")]
    out = ext(gif, filename="1176sch.gif")
    assert out.startswith("# 1176sch.gif\n\n*Image described by claude-test.*")
    assert "gain (handwritten)" in out
    assert seen["model"] == "claude-test"
    block = seen["messages"][0]["content"][0]
    assert block["type"] == "image" and block["source"]["media_type"] == "image/gif"
    with pytest.raises(parsers.ExtractionError, match="not a"):
        ext(b"plain text", filename="x.txt")
    # through the queue: the image gets a text artifact and chunks
    doc_id = store.register(con, gif, mime="image/gif", title="1176sch.gif")["doc_id"]
    assert queue.run(con, [doc_id], extractor="claude-vision").actions == {"created": 1}
    text = store.get_document(con, doc_id, max_chars=5000)["text"]
    assert "compressor schematic" in text
    assert store.get_meta(con, doc_id)["text_source"].startswith("claude-vision/")
    assert queue.run(con, [doc_id]).actions == {"skipped": 1}  # never by default


def test_a_server_with_a_projector_describes_images(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `vision` extractor takes the vision step's model whichever kind
    it is: an openai model gets the image as a data URL, and the stamp
    carries the model."""
    from prax import models
    from prax.parsers import vision

    seen: dict[str, object] = {}

    class FakeRuntime:
        def chat(self, system, user, **kw):
            seen.update(kw, system=system, user=user)
            shows = "## What it shows\nA front panel photo. " + "Detail. " * 60
            return shows + "\n\n## Text in the image\nINPUT (handwritten)", {}

    spec = models.ModelSpec(
        name="server-vl",
        kind="openai",
        base_url="http://127.0.0.1:8080/v1",
        model="qwen-vl",
    )
    monkeypatch.setattr(
        models, "resolve", lambda step: spec if step == "vision" else None
    )
    monkeypatch.setattr(models, "runtime", lambda s: FakeRuntime())
    png = b"\x89PNG\r\n\x1a\n" + bytes(40)
    ext = parsers.by_name("vision")
    assert ext.explicit_only and ext.stamp == "vision/1+qwen-vl@127.0.0.1:8080"
    out = ext(png, filename="panel.png")
    assert out.startswith("# panel.png\n\n*Image described by qwen-vl@127.0.0.1:8080.*")
    assert "INPUT (handwritten)" in out
    image = seen["images"][0]
    assert image[1] == "image/png" and image[0] == png
    assert seen["user"] == vision.PROMPT
    # the Claude-pinned name refuses a server model rather than quietly using it
    with pytest.raises(RuntimeError, match="claude-vision wants a Claude model"):
        parsers.by_name("claude-vision")(png, filename="panel.png")
    # through the queue the image is an image description for the document field
    doc_id = store.register(con, png, mime="image/png", title="panel.png")["doc_id"]
    assert queue.run(con, [doc_id], extractor="vision").actions == {"created": 1}
    stamp = store.get_meta(con, doc_id)["text_source"]
    assert stamp == "vision/1+qwen-vl@127.0.0.1:8080"
    assert "image description" in store.document_field(con, doc_id)


def test_a_promoted_image_is_described_again_by_the_promote_model(
    con: sqlite3.Connection, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The promote work step: before the extraction, a flagged image is
    read again with the promote step's model (``vision.describe`` with
    that model, the reading posted as a parse result), so the expensive
    pass reads the picture as well as the triples — and the second
    reading joins the first."""
    from types import SimpleNamespace

    from prax import models
    from prax.parsers import vision

    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / models.CONFIG_NAME).write_text(
        "models:\n  sonnet: {kind: claude, model: claude-sonnet-5}\n"
        "  server: {kind: openai, base_url: http://127.0.0.1:1/v1, model: vl}\n"
        "steps:\n  vision: {model: server}\n  promote: {model: sonnet}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PRAX_VISION", raising=False)
    monkeypatch.delenv("PRAX_VISION_MODEL", raising=False)
    models.reset()
    asked: list[str] = []

    class FakeMessages:
        def create(self, **kw):
            asked.append(kw["model"])
            text = "## What it shows\nA compressor. " + "Detail. " * 60
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])

    monkeypatch.setattr(
        vision, "CLIENT_FACTORY", lambda: SimpleNamespace(messages=FakeMessages())
    )
    png = b"\x89PNG\r\n\x1a\n" + bytes(40)
    doc_id = store.register(con, png, mime="image/png", title="panel.png")["doc_id"]
    text_id = store.ingest_text(con, "words " * 50, title="a paper")["doc_id"]
    # the local reading first: the vision step's server, faked at the HTTP level
    local = "## What it shows\nA panel. " + "Detail. " * 60 + "\n\n## Notes\nR12 100k"
    monkeypatch.setattr(
        models,
        "post_json",
        lambda url, body, key: {"choices": [{"message": {"content": local}}]},
    )
    assert queue.run(con, [doc_id], extractor="vision").actions == {"created": 1}
    assert store.get_meta(con, doc_id)["text_source"] == "vision/1+vl@127.0.0.1:1"

    # what the worker's promote step does: the promote model reads the
    # image with the current text as previous, the door takes the result
    spec = models.resolve("promote")
    assert spec is not None and spec.model == "claude-sonnet-5"
    again = vision.describe(
        png,
        filename="panel.png",
        model=spec.model,
        previous=store.get_document(con, doc_id)["text"],
    )
    queue.apply_parse(
        con, doc_id, stamp=f"vision/1+{spec.runtime_name}", text=again, force=True
    )
    assert asked == ["claude-sonnet-5"]  # the promote model, not the vision step's
    assert store.get_meta(con, doc_id)["text_source"] == "vision/1+claude-sonnet-5"
    assert models.resolve("vision").name == "server"  # the vision step untouched
    assert text_id  # a text document beside it is no image and is left alone
    # the second reading joined the first: both models' sections, the new one first
    text = store.get_document(con, doc_id)["text"]
    assert "*Image described by claude-sonnet-5; read again by vl@127.0.0.1:1.*" in text
    assert text.index("## What it shows (claude-sonnet-5)") < text.index(
        "## What it shows (vl@127.0.0.1:1)"
    )
    assert "## Notes (vl@127.0.0.1:1)\nR12 100k" in text
    who = [m for m, _ in vision.readings(text)]
    assert who == ["claude-sonnet-5", "vl@127.0.0.1:1"]
    # a chunk per section, each heading carrying its model
    heads = [" ".join(c["heading"] or []) for c in store.list_chunks(con, doc_id)]
    assert any("What it shows (claude-sonnet-5)" in h for h in heads)
    assert "image description" in store.document_field(con, doc_id)


@needs_pymupdf
def test_vision_pages_reads_the_scanned_pages_and_keeps_the_text_layer(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page with a text layer keeps it; a page without one is rendered and
    transcribed by the vision step's model; page markers throughout; the
    stamp names the model, and 'all' the mode."""
    import pymupdf

    from prax import models

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Printed page one, with a proper text layer.")
    doc.new_page()  # blank: a scan as far as the text layer knows
    pdf = doc.tobytes()
    doc.close()

    seen: list[bytes] = []

    class FakeRuntime:
        def chat(self, system, user, **kw):
            seen.append(kw["images"][0][0])
            assert user == parsers.vision.PAGE_PROMPT
            page = "(handwritten) Notes on the 1176: attack 800 us. " + "More. " * 400
            return page + "\n\n[Figure: a sketch]", {}

    spec = models.ModelSpec(
        name="server-vl", kind="openai", base_url="http://127.0.0.1:1/v1", model="vl"
    )
    monkeypatch.setattr(models, "resolve", lambda s: spec if s == "vision" else None)
    monkeypatch.setattr(models, "runtime", lambda s: FakeRuntime())
    monkeypatch.delenv("PRAX_VISION_PAGES", raising=False)
    ext = parsers.by_name("vision-pages")
    assert ext.explicit_only and ext.stamp.endswith("+vl@127.0.0.1:1")
    stamp = ext.stamp
    out = ext(pdf)
    assert "Printed page one, with a proper text layer." in out
    assert "(handwritten) Notes on the 1176: attack 800 us." in out
    assert out.count("--- end of page.page_number=") == 2
    assert out.index("page_number=1") < out.index("(handwritten)")
    assert len(seen) == 1 and seen[0][:2] == b"\xff\xd8"  # one page rendered, as JPEG
    # every page, when asked; and the page budget
    monkeypatch.setenv("PRAX_VISION_PAGES", "all")
    assert ext.stamp == stamp + "+all"
    ext(pdf)
    assert len(seen) == 3
    monkeypatch.setenv("PRAX_VISION_MAX_PAGES", "1")
    with pytest.raises(parsers.ExtractionError, match="exceeds the vision budget"):
        ext(pdf)
    monkeypatch.delenv("PRAX_VISION_MAX_PAGES")
    monkeypatch.setenv("PRAX_VISION_PAGES", "scans")
    # through the queue: chunks with pages
    doc_id = store.register(con, pdf, mime="application/pdf", title="n.pdf")["doc_id"]
    assert queue.run(con, [doc_id], extractor="vision-pages").actions == {"created": 1}
    pages = {c["page"] for c in store.list_chunks(con, doc_id)}
    assert pages == {1, 2}  # the scanned page is its own chunk, on page 2
    assert store.get_meta(con, doc_id)["text_source"] == stamp


def test_registry_dispatch() -> None:
    assert parsers.for_mime("text/plain").name == "plain"
    assert parsers.for_mime("text/markdown").name == "plain"
    assert parsers.for_mime("image/png") is None
    assert parsers.for_mime("text/plain", preferred="pymupdf") is None  # wrong type
    with pytest.raises(KeyError):
        parsers.by_name("nope")
    plain = parsers.by_name("plain")
    assert plain.stamp == "plain/1-r3"
    assert plain(b"h\xc3\xa9llo") == "héllo"
    # explicit-only extractors are never in the default chain, only when named
    chain = [e.name for e in parsers.candidates("application/pdf")]
    assert "docling" not in chain and "pymupdf4llm-ocr" not in chain
    if parsers.by_name("docling").available():
        assert [e.name for e in parsers.candidates("application/pdf", "docling")] == [
            "docling"
        ]


@needs_pymupdf
def test_pymupdf_extractors_read_the_fixture_pdf() -> None:
    data = AMBRITS_PDF.read_bytes()
    md = parsers.by_name("pymupdf4llm")(data)
    plain = parsers.by_name("pymupdf")(data)
    for text in (md, plain):
        assert "POLYNOMIAL TRANSITION REGIONS" in text
        assert "aliasing" in text.lower()
    assert parsers.by_name("pymupdf4llm").stamp.startswith("pymupdf4llm/")
    assert parsers.for_mime("application/pdf").name == "pymupdf4llm"


@needs_pymupdf
def test_scanned_pdf_is_refused_by_markdown_and_left_empty(
    con: sqlite3.Connection,
) -> None:
    import pymupdf

    with pymupdf.open() as doc:  # pages with no text layer, like a scan
        for _ in range(3):
            doc.new_page()
        scan = doc.tobytes()
    with pytest.raises(parsers.ExtractionError, match="needs OCR"):
        parsers.by_name("pymupdf4llm")(scan)
    doc_id = store.register(con, scan, mime="application/pdf", title="scan")["doc_id"]
    assert queue.run(con, [doc_id]).actions == {"empty": 1}
    history = store.get_meta(con, doc_id)["parse_history"]
    assert [h.get("outcome", "error") for h in history] == ["error", "empty"]
    assert store.select_documents(con, pending=True) == [doc_id]  # still pending


@needs_pymupdf
def test_a_long_pdf_is_laid_out_a_window_of_pages_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 532-page book was over the page cap and got the plain extractor:
    one line per line of print, no headings. Layout analysis now goes
    window by window, and the text is the same as in one pass — the
    page numbers the document's own, the headings kept (their level is
    ranked within the window, so it may differ by one)."""
    import re

    data = AMBRITS_PDF.read_bytes()
    whole = parsers.by_name("pymupdf4llm")(data)
    monkeypatch.setenv("PRAX_LAYOUT_WINDOW", "3")  # the 8-page fixture, in three passes
    windowed = parsers.by_name("pymupdf4llm")(data)
    pages = re.findall(r"--- end of page\.page_number=(\d+) ---", windowed)
    assert pages == [str(n) for n in range(1, 9)]
    level = re.compile(r"^#+ ", re.MULTILINE)
    assert len(level.findall(windowed)) == len(level.findall(whole)) > 0

    def flat(text: str) -> str:
        return " ".join(level.sub("# ", text).split())

    assert flat(windowed) == flat(whole)


@needs_pymupdf
def test_oversized_pdf_falls_back_to_plain_extraction(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = AMBRITS_PDF.read_bytes()
    monkeypatch.setenv("PRAX_MAX_LAYOUT_MB", "0.001")  # the 278 KB fixture is "too big"
    with pytest.raises(parsers.ExtractionError, match="PRAX_MAX_LAYOUT_MB"):
        parsers.by_name("pymupdf4llm")(data)
    doc_id = store.register(con, data, mime="application/pdf", title="big")["doc_id"]
    assert queue.run(con, [doc_id]).actions == {"created": 1}
    assert store.get_meta(con, doc_id)["text_source"].startswith("pymupdf/")


@needs_trafilatura
def test_trafilatura_fences_code_blocks() -> None:
    html = (
        b"<html><head><title>Reverb tricks</title></head><body><article>"
        b"<h1>Reverb tricks</h1><p>Some prose about feedback delay networks that"
        b" is long enough to be kept by the extractor as the main content.</p>"
        b"<pre><code>def fdn(x, delays):\n    return sum(x[-d] for d in delays)\n"
        b"</code></pre><p>More prose after the code block, again long enough to"
        b" count as content for the extraction step.</p></article></body></html>"
    )
    text = parsers.by_name("trafilatura")(html)
    assert "```\ndef fdn(x, delays):" in text and "# Reverb tricks" in text
    assert parsers.by_name("trafilatura").stamp.endswith("-r4")


@needs_trafilatura
def test_trafilatura_keeps_the_comments_under_their_own_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thread under an article is part of what the page says; it goes
    under "## Comments" so each comment is a chunk with that heading and
    the article's own text comes first; parse.comments turns it off."""
    para = (
        b"<p>" + b"Some prose about feedback delay networks that is long enough to be"
        b" kept by the extractor as the main content. " * 3 + b"</p>"
    )
    html = (
        b"<html><head><title>Reverb tricks</title></head><body><nav>Home</nav>"
        b"<main><article><h1>Reverb tricks</h1>" + para * 4 + b"</article></main>"
        b'<section id="comments" class="comments"><h2>Comments</h2>'
        b'<div class="comment"><p class="comment-body">Nice writeup, but the'
        b" matrix must be orthogonal for the energy claim to hold, otherwise the"
        b" tail explodes over time.</p></div>"
        b'<div class="comment"><p class="comment-body">Came for the reverb, stayed'
        b" for the jokes about allpass filters, which are all pass and no fail,"
        b" as they say.</p></div></section><footer>foot</footer></body></html>"
    )
    text = parsers.by_name("trafilatura")(html)
    assert "# Reverb tricks" in text and "## Comments" in text
    assert text.index("must be orthogonal") > text.index("## Comments")
    assert text.index("## Comments") > text.index("main content")
    assert "## Comments\n\nComments" not in text  # the section's own heading, once
    monkeypatch.setenv("PRAX_COMMENTS", "false")
    assert parsers.by_name("trafilatura").stamp.endswith("-r4+nocomments")
    assert "## Comments" not in parsers.by_name("trafilatura")(html)


@needs_trafilatura
def test_trafilatura_strips_page_chrome() -> None:
    text = parsers.by_name("trafilatura")(SNAPSHOT_HTML.read_bytes())
    assert text.startswith("# IIR Hilbert Transformer")
    assert "Differential Evolution" in text
    assert "Stack Exchange network" not in text
    assert "Sign up" not in text


@needs_trafilatura
def test_trafilatura_raises_on_empty_page() -> None:
    with pytest.raises(parsers.ExtractionError):
        parsers.by_name("trafilatura")(b"<html><body></body></html>")


@pytest.mark.skipif(
    not os.environ.get("PRAX_TEST_DOCLING"), reason="PRAX_TEST_DOCLING unset"
)
def test_docling_reads_the_fixture_pdf() -> None:
    text = parsers.by_name("docling")(AMBRITS_PDF.read_bytes())
    assert "POLYNOMIAL TRANSITION REGIONS" in text.upper()


# ------------------------------------------------------------------- queue


def _register_pdf(con: sqlite3.Connection) -> int:
    r = store.register(
        con, AMBRITS_PDF.read_bytes(), mime="application/pdf", title="Ambrits"
    )
    return r["doc_id"]


@needs_pymupdf
def test_queue_indexes_pending_pdf(con: sqlite3.Connection) -> None:
    doc_id = _register_pdf(con)
    assert store.select_documents(con, pending=True) == [doc_id]
    report = queue.run(con, [doc_id])
    assert report.actions == {"created": 1} and report.errors == []
    doc = store.get_document(con, doc_id, max_chars=100)
    assert doc["parsed_at"] and doc["meta"]["text_source"].startswith("pymupdf4llm/")
    assert doc["meta"]["parse_history"][0]["outcome"] == "created"
    assert store.search(con, "transition regions")[0]["doc_id"] == doc_id
    assert store.select_documents(con, pending=True) == []
    assert store.select_documents(con, text_source_prefix="pymupdf4llm/") == [doc_id]
    assert (
        store.select_documents(con, text_source_prefix="pymupdf/") == []
    )  # no prefix bleed


@needs_pymupdf
def test_queue_upgrades_zotero_cache_text(
    con: sqlite3.Connection, tmp_path: Path
) -> None:
    lib = zotero.open_library(FIXTURE, tmp_path / "w")
    zotero.run(lib, con)
    lib.close()
    doc_id = store.meta_index(con, "$.zotero.keys")["FCEK3EI9"]
    before = store.get_document(con, doc_id, max_chars=0)
    assert before["meta"]["text_source"] == "zotero-ft-cache"
    n_cache = len(store.select_documents(con, text_source_prefix="zotero-ft-cache"))
    assert n_cache == 9  # 12 attachments, the four Rutz twins are one document
    assert (
        len(
            store.select_documents(
                con, text_source_prefix="zotero-ft-cache", mime_prefix="text/html"
            )
        )
        == 1
    )

    report = queue.run(con, [doc_id])
    assert report.actions == {"upgraded": 1}
    after = store.get_document(con, doc_id, max_chars=0)
    assert after["meta"]["text_source"].startswith("pymupdf4llm/")
    assert after["text_hash"] != before["text_hash"]
    assert after["meta"]["zotero"]["keys"] == ["FCEK3EI9"]  # rest of meta untouched
    assert (
        len(store.select_documents(con, text_source_prefix="zotero-ft-cache"))
        == n_cache - 1
    )
    assert store.search(con, "polynomial transition regions")[0]["doc_id"] == doc_id


def test_queue_keeps_old_text_when_new_is_short(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id = store.ingest_text(con, "x " * 500, title="long")["doc_id"]
    stub = parsers.Extractor("stub", ("text/",), lambda data: "tiny")
    monkeypatch.setattr(parsers, "REGISTRY", [stub, *parsers.REGISTRY])
    assert queue.run(con, [doc_id]).actions == {"kept": 1}
    doc = store.get_document(con, doc_id, max_chars=0)
    assert doc["text_len"] == 1000 and doc["meta"].get("text_source") is None
    assert doc["meta"]["parse_history"][-1]["outcome"] == "kept"
    assert queue.run(con, [doc_id], force=True).actions == {"upgraded": 1}
    assert store.get_document(con, doc_id)["text"] == "tiny"


def test_queue_records_errors_and_continues(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(data: bytes) -> str:
        raise RuntimeError("no parser today")

    monkeypatch.setattr(
        parsers,
        "REGISTRY",
        [parsers.Extractor("boom", ("text/",), boom), *parsers.REGISTRY],
    )
    a = store.ingest_text(con, "first")["doc_id"]
    b = store.ingest_text(con, "second")["doc_id"]
    report = queue.run(con, [a, b], extractor="boom")
    assert report.actions == {"error": 2} and len(report.errors) == 2
    assert "no parser today" in store.get_meta(con, a)["parse_history"][-1]["error"]
    assert store.get_document(con, a)["text"] == "first"  # untouched


def test_queue_falls_back_to_next_extractor(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(data: bytes) -> str:
        raise RuntimeError("markdown path failed")

    monkeypatch.setattr(
        parsers,
        "REGISTRY",
        [parsers.Extractor("boom", ("text/",), boom), *parsers.REGISTRY],
    )
    doc_id = store.register(con, b"plain body text " * 20, mime="text/plain")["doc_id"]
    assert queue.run(con, [doc_id]).actions == {"created": 1}
    meta = store.get_meta(con, doc_id)
    assert meta["text_source"] == "plain/1-r3"
    assert [h.get("error", h.get("outcome")) for h in meta["parse_history"]] == [
        "RuntimeError: markdown path failed",
        "created",
    ]
    # an explicit extractor never falls back
    other = store.register(con, b"more body text " * 20, mime="text/plain")["doc_id"]
    report = queue.run(con, [other], extractor="boom")
    assert report.actions == {"error": 1}


def test_seen_documents_are_not_retried_and_limit_counts_work(
    con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = parsers.Extractor("stub", ("text/",), lambda data: "tiny")
    monkeypatch.setattr(parsers, "REGISTRY", [stub, *parsers.REGISTRY])
    ids = [store.ingest_text(con, f"doc {i} " + "x " * 300)["doc_id"] for i in range(3)]
    assert queue.run(con, ids).actions == {"kept": 3}
    # a second pass over the same (still cache-like) selection does no work
    assert queue.run(con, ids).actions == {"seen": 3}
    assert queue.run(con, ids, force=True).actions == {"upgraded": 3}
    # limit counts documents worked on, not rows looked at
    more = [
        store.ingest_text(con, f"fresh {i} " + "y " * 300)["doc_id"] for i in range(2)
    ]
    stub2 = parsers.Extractor("stub2", ("text/",), lambda data: "z " * 200)
    monkeypatch.setattr(parsers, "REGISTRY", [stub2, *parsers.REGISTRY])
    seen_first = [store.ingest_text(con, "seen one " + "w " * 300)["doc_id"]]
    queue.run(con, seen_first)  # stub2 upgrades it... make it 'seen' via kept:
    monkeypatch.setattr(
        parsers,
        "REGISTRY",
        [parsers.Extractor("stub3", ("text/",), lambda data: "q"), *parsers.REGISTRY],
    )
    queue.run(con, seen_first)  # kept by stub3 -> seen next time
    report = queue.run(con, seen_first + more, limit=1)
    assert (
        report.actions["seen"] == 1
        and sum(v for k, v in report.actions.items() if k != "seen") == 1
    )


def test_queue_skips_unsupported_mime(con: sqlite3.Connection) -> None:
    doc_id = store.register(con, b"\x89PNG", mime="image/png")["doc_id"]
    assert queue.run(con, [doc_id]).actions == {"skipped": 1}


def test_a_budget_refusal_is_not_an_attempt() -> None:
    """A document the OCR extractor refused for its page budget has not
    been read; a run with a bigger budget must get it again, while a real
    error or an empty result still counts as tried."""
    from prax.parsers import queue

    stamp = "pymupdf4llm-ocr/1.0"
    refused = {
        "parse_history": [
            {"extractor": stamp, "error": "98 pages exceeds the OCR budget of 60"}
        ]
    }
    failed = {
        "parse_history": [{"extractor": stamp, "error": "FileDataError: cannot open"}]
    }
    empty = {"parse_history": [{"extractor": stamp, "outcome": "empty"}]}
    assert not queue._seen(refused, stamp)
    assert queue._seen(failed, stamp)
    assert queue._seen(empty, stamp)


def test_the_ocr_language_is_a_setting_and_part_of_the_stamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scan read with the wrong recognizer is letter salad; the language
    is a setting, and a document read in one language has not been read in
    another, so the stamp carries it (the default language does not)."""
    ocr = parsers.by_name("pymupdf4llm-ocr")
    monkeypatch.delenv("PRAX_OCR_LANGUAGE", raising=False)
    assert ocr.stamp.startswith("pymupdf4llm-ocr/")
    assert "+" not in ocr.stamp
    monkeypatch.setenv("PRAX_OCR_LANGUAGE", "arabic")
    assert ocr.stamp.endswith("+arabic")
    assert ocr.stamp != parsers.by_name("pymupdf4llm").stamp + "+arabic"
    from prax.parsers import queue

    tried_in_chinese = {
        "parse_history": [{"extractor": ocr.stamp.replace("+arabic", ""), "chars": 5}]
    }
    assert not queue._seen(tried_in_chinese, ocr.stamp)


@pytest.mark.skipif(
    importlib.util.find_spec("rapidocr") is None, reason="rapidocr not installed"
)
def test_a_language_gets_the_newest_recognizer_rapidocr_ships() -> None:
    assert parsers._ocr_model_version("arabic") == "PP-OCRv5"
    assert parsers._ocr_model_version("latin") in ("PP-OCRv4", "PP-OCRv5")
    with pytest.raises(parsers.ExtractionError):
        parsers._ocr_model_version("klingon")


def test_select_documents_by_title(con: sqlite3.Connection) -> None:
    a = store.ingest_text(con, "one", title="In Arabic--Harmony")["doc_id"]
    b = store.ingest_text(con, "two", title="Piston - Harmony")["doc_id"]
    store.ingest_text(con, "three", title="Counterpoint")
    assert store.select_documents(con, title="harmony") == [a, b]
    assert store.select_documents(con, title="in arabic") == [a]
    assert store.select_documents(con, title="100%") == []  # a literal, not a wildcard


def test_ocr_rows_follow_the_page_and_the_script() -> None:
    """Recognized boxes become lines: grouped by height, left to right for
    Latin, right to left for Arabic; empty boxes are dropped."""
    box = lambda x, y, w=40, h=10: [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    boxes = [box(100, 10), box(10, 12), box(10, 40), box(60, 41), box(5, 80)]
    texts = ["second", "first", "third", "fourth", "  "]
    assert parsers._ocr_rows(boxes, texts, right_to_left=False) == [
        "first second",
        "third fourth",
    ]
    assert parsers._ocr_rows(boxes, texts, right_to_left=True) == [
        "second first",
        "fourth third",
    ]
    assert parsers._ocr_rows([], [], right_to_left=False) == []
