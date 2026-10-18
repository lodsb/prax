"""The safety and correctness review of 2026-10-18
(``docs/eval/safety-review-2026-10-18.md``): each fix with a test that
fails on the code the review read. Numbers are the findings' there."""

from __future__ import annotations

import gzip
import io
import tarfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prax import store, work
from prax.capture import pipeline
from prax.importers import latex
from prax.importers import latex2html as l2h
from prax.parsers import ExtractionError, latexsrc, pdf, quality

# ------------------------------------------- 3. an e-print reads the host


def _paper(folder: Path, body: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    main = folder / "main.tex"
    main.write_text(
        "\\documentclass{article}\\begin{document}" + body + "\\end{document}",
        encoding="utf-8",
    )
    return main


def test_a_source_reads_no_file_outside_its_folder(tmp_path: Path) -> None:
    host = tmp_path / "host"
    host.mkdir()
    (host / "secret").write_text("HOST-SECRET-NO-SUFFIX", encoding="utf-8")
    (host / "notes.tex").write_text("HOST-SECRET-TEX", encoding="utf-8")
    paper = tmp_path / "paper"
    main = _paper(
        paper,
        f"Words. \\input{{{(host / 'secret').as_posix()}}}"
        " \\input{../host/notes} \\input{part}",
    )
    (paper / "part.tex").write_text("Its own part.", encoding="utf-8")
    flat = latex.flatten(main)
    assert "Its own part." in flat
    assert "HOST-SECRET" not in flat
    # nor a bibliography named by an absolute path
    (host / "refs.bib").write_text("@article{k, title={HOST-BIB}}", encoding="utf-8")
    assert latex._find(paper, (host / "refs").as_posix(), ".bib") is None
    assert latex._find(paper, "../host/refs", ".bib") is None


def test_a_source_that_reads_itself_or_doubles_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper = tmp_path / "paper"
    main = _paper(paper, "\\input{main} \\input{a}")
    (paper / "a.tex").write_text("\\input{b}\\input{b}", encoding="utf-8")
    (paper / "b.tex").write_text("\\input{a}x" * 2, encoding="utf-8")
    assert latex.flatten(main).count("x") <= 8  # no file inside itself
    monkeypatch.setattr(latex, "MAX_SOURCE_CHARS", 300)
    for n in range(6):  # each level reads the next twice: 2**6 copies
        (paper / f"l{n}.tex").write_text(
            f"\\input{{l{n + 1}}}\\input{{l{n + 1}}}" + "y" * 20, encoding="utf-8"
        )
    (paper / "l6.tex").write_text("z" * 20, encoding="utf-8")
    with pytest.raises(latex.SourceError):
        latex.flatten(_paper(paper, "\\input{l0}"))


@pytest.mark.skipif(latex.pandoc() is None, reason="pandoc is not installed")
def test_pandoc_reads_no_file_of_its_own(tmp_path: Path) -> None:
    secret = tmp_path / "listing.txt"
    secret.write_text("HOST-LISTING-LINE", encoding="utf-8")
    tex = (
        "\\documentclass{article}\\begin{document}Hi."
        f"\\lstinputlisting{{{secret.as_posix()}}}\\end{{document}}"
    )
    binary = latex.pandoc()
    assert binary is not None
    out = latex.to_markdown(tex, binary, cwd=tmp_path)
    assert "Hi." in out and "HOST-LISTING" not in out


# ---------------------------------------------- 13. an e-print's size


def test_an_e_print_that_unpacks_too_far_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(latexsrc, "UNPACKED_BYTES", 1000)
    bomb = gzip.compress(b"0" * 5000)
    with pytest.raises(ExtractionError, match="unpacks to over"):
        latexsrc.unpack(bomb, tmp_path / "a")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for n in range(3):
            info = tarfile.TarInfo(f"f{n}.tex")
            info.size = 600
            tar.addfile(info, io.BytesIO(b"x" * 600))
    (tmp_path / "b").mkdir()
    with pytest.raises(ExtractionError, match="unpacks to over"):
        latexsrc.unpack(buf.getvalue(), tmp_path / "b")
    assert not any((tmp_path / "b").iterdir())  # nothing written first
    monkeypatch.setattr(latexsrc, "MAX_MEMBERS", 2)
    monkeypatch.setattr(latexsrc, "UNPACKED_BYTES", 10**6)
    with pytest.raises(ExtractionError, match="files"):
        latexsrc.unpack(buf.getvalue(), tmp_path / "b")


# ------------------------------------- 2. the crawl cache and its paths


def test_the_crawl_cache_keeps_every_page_inside_it(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    f = l2h.Fetcher(cache)
    base = "https://example.org/~jos/book/"
    # a page as before: readable, where the cache of earlier crawls has it
    assert f._path(base + "Page.html") == cache / "example.org" / "~jos/book/Page.html"
    for evil in (
        "%2E%2E%2F" * 8 + "pwn.py",
        "..%2F..%2F..%2Fescape.html",
        "%2FC:%2FUsers%2Fx%2Fsitecustomize.py",
        "a%5C..%5C..%5Cb.html",
    ):
        path = f._path(base + evil)
        assert path.resolve().is_relative_to(cache.resolve()), evil
        assert path.parent.name == "_"
    assert f._path("https://../x.html").resolve().is_relative_to(cache.resolve())
    index = '<A HREF="..%2F..%2F..%2F..%2Fescape.html">x</A> <A HREF="Ok.html">y</A>'
    assert l2h.book_pages(base + "index.html", index) == (base + "Ok.html",)


# ------------------------------------------------------------ the wall

ADMIN = "admin-secret"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PRAX_TOKEN", ADMIN)
    from prax.api import app

    with TestClient(app) as c:
        yield c


def _named(client: TestClient) -> dict[str, str]:
    admin = {"Authorization": f"Bearer {ADMIN}"}
    secret = client.post("/tokens", json={"name": "mcp"}, headers=admin).json()
    return {"Authorization": f"Bearer {secret['secret']}"}


def _hits(client: TestClient, words: str, headers: dict[str, str]) -> list[object]:
    got = client.get(f"/search?q={words}", headers=headers).json()
    return list(got if isinstance(got, list) else got.get("results") or [])


def test_a_personal_page_whose_write_is_refused_leaves_nothing_open(
    client: TestClient,
) -> None:
    """1. The parent, a project, a document to annotate or a cycle refused
    the write after the page was indexed, open."""
    named = _named(client)
    for body in (
        {"parent": "no-such-page"},
        {"part_of": "no-such-project"},
        {"annotates": [987654]},
    ):
        got = client.put(
            "/page/secret-notes",
            json={"text": "zebrafinch notes " * 20, "sensitivity": "personal", **body},
            headers=named,
        )
        assert got.status_code == 404, body
        assert client.get("/page/secret-notes", headers=named).status_code == 404
        assert not _hits(client, "zebrafinch", named), body
    # a cycle: b under a, then a asked to sit under b with new text
    admin = {"Authorization": f"Bearer {ADMIN}"}
    client.put("/page/a", json={"text": "page a " * 30}, headers=admin)
    client.put("/page/b", json={"text": "page b " * 30, "parent": "a"}, headers=admin)
    got = client.put(
        "/page/a",
        json={
            "text": "zebrafinch again " * 20,
            "parent": "b",
            "sensitivity": "personal",
        },
        headers=admin,
    )
    assert got.status_code == 400
    assert "zebrafinch" not in client.get("/page/a", headers=admin).json()["text"]
    # and a write that goes through is personal before it is ever searched
    ok = client.put(
        "/page/kept-notes",
        json={"text": "nightingale notes " * 20, "sensitivity": "personal"},
        headers=admin,
    )
    assert ok.status_code == 200
    assert not _hits(client, "nightingale", named)


def test_a_named_token_cannot_replace_a_documents_text(client: TestClient) -> None:
    """4. Re-sending a document's bytes with a text of one's own rewrote
    it; only the administrator may now."""
    admin = {"Authorization": f"Bearer {ADMIN}"}
    named = _named(client)
    original = b"The original words of a note. " * 30
    first = client.post(
        "/ingest/file",
        files={"file": ("n.txt", original, "text/plain")},
        headers=admin,
    ).json()
    doc = first["doc_id"]
    before = client.get(f"/doc/{doc}/text", headers=admin).text
    again = client.post(
        "/ingest/file",
        data={"text": "REPLACED by a named token. " * 30, "text_source": "mine/1"},
        files={"file": ("n.txt", original, "text/plain")},
        headers=named,
    )
    assert again.status_code == 200 and again.json()["created"] is False
    assert client.get(f"/doc/{doc}/text", headers=admin).text == before
    # the administrator still may (an importer sending a better text)
    client.post(
        "/ingest/file",
        data={"text": "A better text of the note. " * 30, "text_source": "mine/1"},
        files={"file": ("n.txt", original, "text/plain")},
        headers=admin,
    )
    assert "better text" in client.get(f"/doc/{doc}/text", headers=admin).text


# ----------------------------------------------- 8. a versioned arXiv id

pymupdf = pytest.importorskip("pymupdf")

LAYER = "pymupdf4llm/1.28.2-r2"


def _typed(*lines: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    for n, line in enumerate(lines):
        page.insert_text((72, 72 + 20 * n), line)
    out = doc.tobytes()
    doc.close()
    return out


def _captured(con: store.sqlite3.Connection, data: bytes, **meta: object) -> int:
    doc = store.register(
        con, data, mime="application/pdf", meta={"source": "capture", **meta}
    )["doc_id"]
    store.index_text(con, doc, "# A paper\n\nIts words. " * 20, text_source=LAYER)
    return int(doc)


def _waiting(con: store.sqlite3.Connection, doc: int) -> list[tuple[str, str | None]]:
    return [(r["extractor"], r.get("mode")) for r in store.pending_readings(con, doc)]


def test_a_versioned_arxiv_id_is_asked_for_without_its_version(
    con: store.sqlite3.Connection,
) -> None:
    paper = _captured(con, _typed("A paper on arXiv."), arxiv="2501.13484v2")
    got = pipeline.follow_ups(con, paper, stamp=LAYER, action="created")
    assert got == "latex-source"
    assert _waiting(con, paper) == [("latex-source", "2501.13484")]
    assert latexsrc.arxiv_of("math.GT/0309136v1") == "math.GT/0309136"
    assert latexsrc.arxiv_of("no id") is None


# ------------------------------------ 10. a preprint the paper cites


def test_a_preprint_the_first_page_cites_is_not_the_papers_id() -> None:
    cites = _typed("As shown in arXiv:2401.00001 and in arXiv:2402.00002v3.")
    assert latexsrc.arxiv_id_of(cites) is None
    # the margin's stamp names the paper, whatever else the page cites
    stamped = _typed(
        "arXiv:1308.3432v2  [cs.LG]  9 Aug 2013", "after arXiv:2401.00001."
    )
    assert latexsrc.arxiv_id_of(stamped) == "1308.3432"
    # a journal's print naming its one preprint, judged by its text later
    assert latexsrc.arxiv_id_of(_typed("Preprint: arXiv:2401.00831")) == "2401.00831"


def test_another_papers_source_is_not_the_papers_text() -> None:
    ours = "Wavelet scattering networks transform acoustic signals invariantly. " * 30
    theirs = "Reinforcement learning agents maximise discounted cumulative rewards. "
    assert latexsrc.same_paper(ours, ours.upper() + " and further pages")
    assert not latexsrc.same_paper(theirs * 30, ours)


# --------------------------------- 9. a failed re-read of the source


def test_a_failed_re_read_of_the_source_leaves_its_latex_text(
    con: store.sqlite3.Connection,
) -> None:
    paper = _captured(con, _typed("A paper on arXiv."), arxiv="2406.06293")
    store.index_text(
        con, paper, "# A paper\n\n$$x$$ words. " * 20, text_source="latex-source/3.9-r2"
    )
    got = pipeline.follow_ups(con, paper, stamp="latex-source/3.9-r3", action="error")
    assert got is None
    assert ("marker", None) not in _waiting(con, paper)


# ------------------------------- 7. a slide over its background picture


def _over_picture(*, text_first: bool) -> bytes:
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
        pix.clear_with(90)
        if text_first:  # an OCR's visible text, beneath the scan's picture
            page.insert_text((72, 100), "an old OCR's words")
        page.insert_image(page.rect, pixmap=pix)
        if not text_first:  # a slide's words over its background
            page.insert_text((72, 100), "Motivation: what the talk is about")
    out = doc.tobytes()
    doc.close()
    return out


def test_a_slide_over_its_background_picture_is_not_a_scan() -> None:
    slides = pdf.profile(_over_picture(text_first=False))
    assert slides["scanned"] == 0.0 and slides["layer"]
    assert not quality.check(
        "# Motivation\n\nwhat the talk is about " * 20,
        stamp=LAYER,
        mime="application/pdf",
        profile=slides,
    )
    beneath = pdf.profile(_over_picture(text_first=True))
    assert beneath["scanned"] == 1.0
    with pymupdf.open(stream=_over_picture(text_first=False)) as doc:
        assert not pdf.picture_of_page(doc[0])


# ------------------------- 5. the door's own readings are not forced


def test_a_reading_the_door_asked_for_is_not_forced(client: TestClient) -> None:
    con = client.app.state.con
    data = _typed("A printed page.")
    doc = store.register(con, data, mime="application/pdf", title="p.pdf")["doc_id"]
    store.index_text(con, doc, "the layer's words " * 40, text_source=LAYER)
    store.request_reading(con, doc, "vision-pages", mode="all", by="door")
    admin = {"Authorization": f"Bearer {ADMIN}"}
    item = client.get("/work/parse", headers=admin).json()["items"][0]
    assert item["doc_id"] == doc and item["force"] is False
    work._leases.clear()
    store.cancel_reading(con, doc)
    store.request_reading(con, doc, "vision-pages", mode="all", by="human")
    item = client.get("/work/parse", headers=admin).json()["items"][0]
    assert item["force"] is True
    work._leases.clear()


# ---------------------------------- 6. a page drawn for an agent, bounded


def test_a_tall_page_is_drawn_within_the_bounds(tmp_path: Path) -> None:
    from prax.parsers import figures

    doc = pymupdf.open()
    doc.new_page(width=200, height=200 * 60)  # a receipt's roll, a scroll
    path = tmp_path / "tall.pdf"
    doc.save(path)
    doc.close()
    png, count = figures.page_png(path, 1, width=1600)
    assert count == 1 and png is not None
    with pymupdf.open(stream=png) as drawn:
        rect = drawn[0].rect
    assert rect.height <= figures.PAGE_SIDE_MAX
    assert rect.width * rect.height <= figures.PAGE_PIXELS_MAX


# ---------------------- 12. a figure as the client's model takes it


def test_a_figure_is_drawn_again_as_a_model_takes_it() -> None:
    from prax.parsers import figures

    big = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 5000, 300), False)
    big.clear_with(120)
    data, media = figures.for_a_model(big.tobytes("png"), "image/png", side=1600)
    assert media == "image/png" and max(pymupdf.Pixmap(data).irect[2:]) <= 1600
    small = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
    small.clear_with(10)
    png = small.tobytes("png")
    assert figures.for_a_model(png, "image/png") == (png, "image/png")
    # a format the client's model does not take is drawn as PNG
    pnm = small.tobytes("pnm")
    assert figures.for_a_model(pnm, "image/x-portable-anymap")[1] == "image/png"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30"/>'
    assert figures.for_a_model(svg, "image/svg+xml")[1] == "image/png"
    with pytest.raises(ValueError):
        figures.for_a_model(b"not a picture", "image/jp2")


# ------------------- 16, 18. crops that ran are not asked for again


def test_crops_that_filled_nothing_are_not_asked_for_again(
    con: store.sqlite3.Connection,
) -> None:
    paper = _captured(con, _typed("A paper."))
    store.index_text(
        con,
        paper,
        "Prose.\n\nFigure 1: The rig.\n\nMore prose. " * 10,
        text_source="latex-source/3.9-r2",
    )
    meta = store.get_meta(con, paper)
    meta["parse_history"] = [
        {"extractor": LAYER, "outcome": "created"},
        {"extractor": "latex-source/3.9-r2", "outcome": "upgraded"},
    ]
    store.set_meta(con, paper, meta)
    pipeline.assess(con, paper)
    assert store.select_for_reading(
        con, finding="bare-figures", fixed_by="figure-crops"
    ) == [paper]
    # the crops ran and found no picture: the text is the same
    meta = store.get_meta(con, paper)
    meta["parse_history"].append({"extractor": "figure-crops/1", "outcome": "same"})
    store.set_meta(con, paper, meta)
    pipeline.follow_ups(con, paper, stamp="figure-crops/1", action="same")
    assert not store.select_for_reading(
        con, finding="bare-figures", fixed_by="figure-crops"
    )
    # a formulas reading after them asks for no crops either
    meta = store.get_meta(con, paper)
    meta["parse_history"].append({"extractor": "formulas/1+q", "outcome": "upgraded"})
    store.set_meta(con, paper, meta)
    pipeline.follow_ups(con, paper, stamp="formulas/1+q", action="upgraded")
    assert ("figure-crops", None) not in _waiting(con, paper)
