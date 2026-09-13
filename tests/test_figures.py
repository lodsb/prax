"""Figures: content images referenced in the text by hash, served out of
the original, chunked with their caption, and read by the vision model."""

from __future__ import annotations

import base64
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient

from prax import chunking, parsers, store
from prax.parsers import figures, queue

needs_pymupdf = pytest.mark.skipif(
    not parsers.by_name("pymupdf4llm").available(), reason="pymupdf4llm not installed"
)
needs_trafilatura = pytest.mark.skipif(
    not parsers.by_name("trafilatura").available(), reason="trafilatura not installed"
)


def _png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    im = Image.new("RGB", (width, height), color)
    # noise so the bytes are not tiny (an icon-sized flat image compresses away)
    px = im.load()
    for x in range(0, width, 3):
        for y in range(0, height, 3):
            px[x, y] = ((x * 7) % 256, (y * 11) % 256, (x + y) % 256)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _data_url(data: bytes, media: str = "image/png") -> str:
    return f"data:{media};base64,{base64.b64encode(data).decode()}"


PHOTO = _png(320, 240, (200, 30, 30))
PLOT = _png(300, 200, (30, 30, 200))
ICON = _png(16, 16, (0, 0, 0))
P1 = (
    "Extreme wealth has a deranging effect, the author says, and the argument"
    " runs through a long history of the very rich. "
)
P2 = (
    "A second paragraph carries the argument on to the present day and its"
    " billionaires, with examples. "
)
CAP = "Naomi Klein. Photograph: T. Goehring"
ALT = "Wealth share of the top one percent since 1980"

PAGE = f"""<html><head><title>Klein on wealth</title></head><body>
<header><img src="{_data_url(ICON)}" alt=""><a href="/">Home</a></header>
<main>
<h1>Klein on wealth</h1>
<p>{P1 * 4}</p>
<figure><img src="{_data_url(PHOTO)}" alt="Naomi Klein">
<figcaption>{CAP}</figcaption></figure>
<p>{P2 * 4}</p>
<p><img src="{_data_url(PLOT)}" alt="{ALT}"></p>
<p>{"A closing paragraph. " * 8}</p>
</main>
<aside><img src="{_data_url(_png(200, 200, (9, 9, 9)))}"> teaser</aside>
</body></html>"""


@pytest.fixture()
def client(data_dir):
    from prax.api import app

    with TestClient(app) as c:
        yield c


def test_html_figures_are_the_content_images() -> None:
    figs = figures.html_figures(PAGE.encode())
    assert [f.caption for f in figs] == [
        "Naomi Klein. Photograph: T. Goehring",
        "Wealth share of the top one percent since 1980",
    ]
    assert figs[0].data == PHOTO and figs[0].ref == figures.sha(PHOTO)
    assert figs[0].anchor and figs[0].anchor.startswith("Extreme wealth")
    assert figs[1].anchor and figs[1].anchor.startswith("A second paragraph")


def test_place_puts_a_figure_after_its_anchor_and_the_rest_at_the_end() -> None:
    figs = figures.html_figures(PAGE.encode())
    md = f"# Klein on wealth\n\n{P1}Twice.\n\nA closing paragraph.\n"
    out = figures.place(md, figs)
    lines = out.split("\n")
    i = lines.index(figures.line_for(figs[0]))
    assert lines[i - 2].startswith("Extreme wealth") and lines[i - 1] == ""
    assert figures.FIGURES_HEADING in out  # the plot's anchor is not in this text
    assert out.rstrip().endswith(figures.line_for(figs[1]))
    assert figures.place(out, figs) == out  # idempotent
    assert [r["ref"] for r in figures.refs(out)] == [f.ref for f in figs]


@needs_trafilatura
def test_trafilatura_writes_the_figures_into_the_markdown() -> None:
    text = parsers.by_name("trafilatura")(PAGE.encode())
    refs = figures.refs(text)
    assert [r["caption"] for r in refs] == [
        "Naomi Klein. Photograph: T. Goehring",
        "Wealth share of the top one percent since 1980",
    ]
    assert text.index("Extreme wealth") < text.index(refs[0]["ref"])
    assert figures.find(PAGE.encode(), refs[0]["ref"]) == (PHOTO, "image/png")
    assert figures.find(PAGE.encode(), "0" * 64) is None


def test_the_chunker_makes_a_figure_chunk_with_its_data() -> None:
    ref = figures.sha(PHOTO)
    text = (
        "# A page\n\nSome prose here that runs on for a while.\n\n"
        f"![Naomi Klein. Photograph](figure:{ref})\n"
        "*Figure, as read by qwen@host:* A portrait of a woman.\n\n"
        "More prose after the figure.\n"
    )
    chunks = chunking.chunk(text)
    fig = [c for c in chunks if c.kind == "figure"]
    assert len(fig) == 1
    assert fig[0].data == {
        "ref": ref,
        "caption": "Naomi Klein. Photograph",
        "readings": [{"model": "qwen@host", "text": "A portrait of a woman."}],
    }
    assert fig[0].text.startswith("![Naomi") and "More prose" not in fig[0].text


@needs_pymupdf
def test_pdf_figures_come_with_their_caption_and_page() -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Some text above the figure.")
    page.insert_image(pymupdf.Rect(72, 100, 372, 300), stream=PLOT)
    page.insert_text((72, 320), "Figure 1: Wealth share since 1980.")
    logo_page_rects = []
    for _ in range(5):  # a logo on every page: not a figure
        p = doc.new_page()
        p.insert_image(pymupdf.Rect(400, 20, 520, 140), stream=PHOTO)
        logo_page_rects.append(p)
    figs = figures.pdf_figures(doc)
    assert [(f.caption, f.page) for f in figs] == [
        ("Figure 1: Wealth share since 1980.", 1)
    ]
    pdf = doc.tobytes()
    text = parsers.by_name("pymupdf4llm")(pdf)
    refs = figures.refs(text)
    assert len(refs) == 1 and refs[0]["caption"] == "Figure 1: Wealth share since 1980."
    # the image line sits right above its caption, inside page 1
    lines = text.split("\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith("![Figure 1"))
    assert "Figure 1" in "".join(lines[i + 1 : i + 3])
    assert i < next(k for k, ln in enumerate(lines) if "page_number=1" in ln)
    found = figures.find(pdf, refs[0]["ref"])
    assert found is not None and found[1] == "image/png"


def test_readable_converts_what_the_models_cannot_read() -> None:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2000, 1000), (1, 2, 3)).save(buf, "WEBP")
    data, media = figures.readable(buf.getvalue(), "image/webp")
    assert media == "image/jpeg"
    assert Image.open(io.BytesIO(data)).size == (1600, 800)
    assert figures.readable(PHOTO, "image/png") == (PHOTO, "image/png")


def test_describe_writes_the_reading_under_each_figure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prax import models

    spec = models.ModelSpec(
        name="server-vl", kind="openai", base_url="http://127.0.0.1:1/v1", model="vl"
    )
    monkeypatch.setattr(models, "resolve", lambda s: spec if s == "vision" else None)
    seen: list[bytes] = []

    class FakeRuntime:
        def chat(self, system, user, **kw):
            seen.append(kw["images"][0][0])
            assert user == figures.FIGURE_PROMPT
            return "A photograph of a woman in a dark jacket, facing the camera.", {}

    monkeypatch.setattr(models, "runtime", lambda s: FakeRuntime())
    figs = figures.html_figures(PAGE.encode())
    text = figures.place("Prose.\n", figs)
    out = figures.describe(PAGE.encode(), text)
    assert out.count("*Figure, as read by vl@127.0.0.1:1:* A photograph") == 2
    assert seen == [PHOTO, PLOT]
    assert figures.refs(out)[0]["described_by"] == ["vl@127.0.0.1:1"]
    # the same model again reads nothing; a chunk carries the reading
    assert figures.describe(PAGE.encode(), out) == out
    fig_chunks = [c for c in chunking.chunk(out) if c.kind == "figure"]
    assert fig_chunks[0].data["readings"][0]["model"] == "vl@127.0.0.1:1"


@needs_trafilatura
def test_the_door_serves_a_figure_and_a_re_read_that_changes_nothing_is_same(
    client: TestClient, con: sqlite3.Connection
) -> None:
    doc_id = store.register(con, PAGE.encode(), mime="text/html", title="k")["doc_id"]
    assert queue.run(con, [doc_id]).actions == {"created": 1}
    ref = figures.refs(store.get_document(con, doc_id)["text"])[0]["ref"]
    r = client.get(f"/doc/{doc_id}/figure/{ref}")
    assert r.status_code == 200 and r.content == PHOTO
    assert r.headers["content-type"].startswith("image/png")
    assert "immutable" in r.headers["cache-control"]
    assert client.get(f"/doc/{doc_id}/figure/{'0' * 64}").status_code == 404
    chunk = next(c for c in store.list_chunks(con, doc_id) if c["kind"] == "figure")
    assert chunk["data"]["ref"] == ref
    # a second run of the same extractor: nothing new, chunks stay
    before = [c["chunk_id"] for c in store.list_chunks(con, doc_id)]
    assert queue.run(con, [doc_id], force=True).actions == {"same": 1}
    assert [c["chunk_id"] for c in store.list_chunks(con, doc_id)] == before
    assert store.get_meta(con, doc_id)["parse_history"][-1]["outcome"] == "same"
    # figure-refs on a text that has its figures already: same, and the
    # parser's stamp stays (an annotating extractor never takes it over)
    assert queue.run(con, [doc_id], extractor="figure-refs", force=True).actions == {
        "same": 1
    }
    assert store.get_meta(con, doc_id)["text_source"].startswith("trafilatura/")
    history = store.get_meta(con, doc_id)["parse_history"]
    assert history[-1]["extractor"] == "figure-refs/1"
