"""The ``marker`` extractor: a client of marker's server (the ``marker``
role of ``prax up``), whose output becomes a text artifact like any other
— page markers the chunker reads, marker's image references dropped,
prax's figure references placed, the display equations kept for the
``formula`` chunks. A small server stands in for marker here."""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from test_parsers import AMBRITS_PDF

from prax import chunking, config, parsers, store, up
from prax.parsers import figures, queue

needs_pymupdf = pytest.mark.skipif(
    importlib.util.find_spec("pymupdf") is None, reason="pymupdf not installed"
)

MARKED = textwrap.dedent(
    """
    {0}------------------------------------------------

    # **PHYSICAL MODELING OF DRUMS**

    The membrane obeys

    $$\\frac{\\partial^2 u}{\\partial t^2} = c^2 \\nabla^2 u \\quad (1)$$

    ![](_page_0_Figure_3.jpeg)

    Figure 1: The rig.

    {1}------------------------------------------------

    where $c$ is the wave speed of (1).

    ![](_page_1_Picture_7.jpeg)
    """
).strip("\n")


class FakeMarker(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h1>Marker API</h1>")

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        span = re.search(rb'name="page_range"\r\n\r\n(\d+)-(\d+)', body)
        FakeMarker.seen.append(
            {
                "path": self.path,
                "pdf": b"%PDF" in body,
                "paginate": b'name="paginate_output"\r\n\r\ntrue' in body,
                "mode": b'name="mode"\r\n\r\nfast' in body,
                "page_range": (int(span.group(1)), int(span.group(2)))
                if span
                else None,
            }
        )
        output = MARKED
        if span:
            # the pages asked for, numbered as the document numbers them
            a, b = int(span.group(1)), int(span.group(2))
            output = "\n\n".join(
                f"{{{n}}}" + "-" * 48 + f"\n\nPage {n + 1} of the book."
                for n in range(a, b + 1)
            )
        images: dict[str, str] = {}
        answer = getattr(FakeMarker, "answer", None)
        if answer is not None:  # a test's own page text and crops
            output, images = answer()
        out = json.dumps(
            {"format": "markdown", "output": output, "images": images, "success": True}
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_: Any) -> None:
        pass


@pytest.fixture()
def marker_server(monkeypatch: pytest.MonkeyPatch) -> Any:
    server = HTTPServer(("127.0.0.1", 0), FakeMarker)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(
        "PRAX_MARKER_URL", f"http://127.0.0.1:{server.server_address[1]}"
    )
    FakeMarker.seen.clear()
    yield server
    server.shutdown()


def test_marker_is_explicit_and_only_there_with_its_server(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAX_MARKER_URL", "http://127.0.0.1:9")  # nothing there
    ext = parsers.by_name("marker")
    assert ext.explicit_only and ext.accepts("application/pdf")
    assert not ext.available()
    assert ext.stamp == "marker/?"  # the server does not say its version


def test_the_stamp_reads_the_version_from_the_role_venv(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "marker-venv"
    (venv / "Lib" / "site-packages" / "marker_pdf-2.0.0.dist-info").mkdir(parents=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text(
        f"run: {{marker: {{venv: {venv.as_posix()}}}}}\n", encoding="utf-8"
    )
    assert parsers.by_name("marker").stamp == "marker/2.0.0"
    monkeypatch.setenv("PRAX_MARKER_MODE", "balanced")
    assert parsers.by_name("marker").stamp == "marker/2.0.0+balanced"
    monkeypatch.setenv("PRAX_MARKER_MODE", "nonsense")
    assert parsers.by_name("marker").stamp == "marker/2.0.0"  # fast, the default


def test_page_markers_are_the_chunkers() -> None:
    out = parsers._marker_pages(MARKED, 2)
    assert "{0}" not in out and "{1}" not in out
    assert out.count("--- end of page.page_number=1 ---") == 1
    assert out.rstrip().endswith("--- end of page.page_number=2 ---")
    assert out.index("(1)$$") < out.index("page_number=1") < out.index("wave speed")


@needs_pymupdf
def test_the_extractor_turns_the_servers_answer_into_an_artifact(
    data_dir: Path, marker_server: Any
) -> None:
    ext = parsers.by_name("marker")
    assert ext.available()
    text = ext(AMBRITS_PDF.read_bytes())
    assert FakeMarker.seen and FakeMarker.seen[0]["path"] == "/marker/upload"
    assert FakeMarker.seen[0]["pdf"] and FakeMarker.seen[0]["paginate"]
    assert FakeMarker.seen[0]["mode"]
    assert "_page_0_Figure_3.jpeg" not in text  # marker's images: no files here
    assert "$$\\frac{\\partial^2 u}" in text  # the mathematics, as LaTeX
    assert "--- end of page.page_number=1 ---" in text
    # what the chunker makes of it: a formula chunk with its number, pages
    chunks = chunking.chunk(text)
    formula = [c for c in chunks if c.kind == "formula"]
    assert len(formula) == 1
    assert chunking.parse_formula(formula[0].text)["number"] == "1"
    assert formula[0].page == 1
    assert any(c.page == 2 for c in chunks if c.kind == "text")
    # prax's own figure references were placed by the same code as pymupdf4llm's
    refs = list(figures.REF.finditer(text))
    real = figures.pdf_figures(
        parsers._pymupdf_open(AMBRITS_PDF.read_bytes()).__enter__()
    )
    assert len(refs) == len(real)


def test_the_default_chain_never_probes_markers_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """candidates() for a PDF builds the default chain; marker is explicit
    only, and its availability is a probe of its server (two seconds a
    refused connection on Windows): the door's hand-out asked it for every
    waiting document and GET /work/parse took thirteen seconds. The cheap
    tests come first, and a probe's answer serves a few seconds."""
    probes = []
    monkeypatch.setattr(parsers, "_marker_up", lambda: probes.append(1) or False)
    chain = [e.name for e in parsers.candidates("application/pdf")]
    assert "marker" not in chain and probes == []
    # asked for by name it is probed, once per few seconds
    monkeypatch.setattr(parsers, "_marker_up", parsers.__dict__["_marker_up"])
    parsers._marker_probes.clear()
    monkeypatch.setenv("PRAX_MARKER_URL", "http://127.0.0.1:9")
    calls = []
    real_get = __import__("httpx").get

    def counting_get(*a, **kw):
        calls.append(a[0])
        return real_get(*a, **kw)

    monkeypatch.setattr(__import__("httpx"), "get", counting_get)
    assert parsers.candidates("application/pdf", preferred="marker") == []
    assert parsers.candidates("application/pdf", preferred="marker") == []
    assert len(calls) == 1


@needs_pymupdf
def test_a_long_document_goes_to_marker_a_window_of_pages_at_a_time(
    data_dir: Path, marker_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two 530-page books were over marker's page cap and kept their
    plain text. marker's server takes a page range and numbers the pages
    as the document does, so a long document goes up a window at a time
    and the parts join as one — the page markers in order, one to the
    end, as the chunker wants them."""
    monkeypatch.setenv("PRAX_LAYOUT_WINDOW", "3")  # the 8-page fixture, three uploads
    text = parsers.by_name("marker")(AMBRITS_PDF.read_bytes())
    assert [s["page_range"] for s in FakeMarker.seen] == [(0, 2), (3, 5), (6, 7)]
    assert all(s["paginate"] and s["pdf"] for s in FakeMarker.seen)
    pages = re.findall(r"--- end of page\.page_number=(\d+) ---", text)
    assert pages == [str(n) for n in range(1, 9)]
    assert "Page 1 of the book." in text and "Page 8 of the book." in text
    assert text.index("Page 4 of the book.") > text.index("page_number=3 ---")
    assert text.index("Page 4 of the book.") < text.index("page_number=4 ---")
    # a short document still goes up whole
    FakeMarker.seen.clear()
    monkeypatch.setenv("PRAX_LAYOUT_WINDOW", "50")
    parsers.by_name("marker")(AMBRITS_PDF.read_bytes())
    assert [s["page_range"] for s in FakeMarker.seen] == [None]


@needs_pymupdf
def test_a_server_that_fails_is_an_extraction_error(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Failing(FakeMarker):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("content-length", 0)))
            out = json.dumps({"success": False, "error": "no layout model"}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(out)

    server = HTTPServer(("127.0.0.1", 0), Failing)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(
        "PRAX_MARKER_URL", f"http://127.0.0.1:{server.server_address[1]}"
    )
    try:
        with pytest.raises(parsers.ExtractionError, match="no layout model"):
            parsers.by_name("marker")(AMBRITS_PDF.read_bytes())
    finally:
        server.shutdown()
    monkeypatch.setenv("PRAX_MARKER_URL", "http://127.0.0.1:9")
    with pytest.raises(parsers.ExtractionError, match="marker's server"):
        parsers.by_name("marker")(AMBRITS_PDF.read_bytes())


def test_the_marker_role_of_prax_up(data_dir: Path, tmp_path: Path) -> None:
    venv = tmp_path / "mv"
    exe = venv / ("Scripts" if up.sys.platform == "win32" else "bin")
    exe.mkdir(parents=True)
    name = "marker_server.exe" if up.sys.platform == "win32" else "marker_server"
    (exe / name).write_bytes(b"MZ")
    with pytest.raises(up.UpError, match="which venv"):
        up.roles({"marker": {}})
    with pytest.raises(up.UpError, match="no marker_server"):
        up.roles({"marker": {"venv": str(tmp_path / "nowhere")}})
    (role,) = up.roles({"marker": {"venv": str(venv), "port": 9001, "ngl": 0}})
    assert role.argv[0] == str(exe / name)
    assert role.argv[-1] == "9001" and role.health == "http://127.0.0.1:9001/"
    assert role.env["SURYA_INFERENCE_BACKEND"] == "llamacpp"
    assert role.env["LLAMA_CPP_NGL"] == "0" and role.env["LLAMA_CPP_BINARY"]
    assert not role.on_demand
    (role,) = up.roles({"marker": {"venv": str(venv), "on_demand": True}})
    assert role.on_demand and role.argv[-1] == str(up.MARKER_PORT)
    # declared on demand: the supervisor starts it paused
    sup = up.Supervisor([role], data_dir=data_dir)
    assert "marker" in sup.paused


@needs_pymupdf
def test_a_marker_read_asks_for_the_formula_readings_next(
    data_dir: Path, marker_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The edge of the process graph a marker read adds: when its text holds
    display equations and the formulas step names a free model, the door
    places the `formulas` reading itself, in the finished request's stead;
    with the step off, nothing is asked for."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / config.CONFIG_NAME).write_text(
        "models:\n"
        "  srv: {kind: openai, base_url: http://127.0.0.1:1/v1, model: q}\n"
        "steps: {formulas: {model: srv}}\n",
        encoding="utf-8",
    )
    from prax import models, store, worker
    from prax.api import app

    models.reset()
    with TestClient(app) as client:
        con = client.app.state.con
        pdf = store.register(
            con, AMBRITS_PDF.read_bytes(), mime="application/pdf", title="Ambrits"
        )["doc_id"]
        store.index_text(con, pdf, "old text " * 40, text_source="pymupdf4llm/1")
        assert (
            client.post(f"/doc/{pdf}/reading", json={"extractor": "marker"}).status_code
            == 200
        )

        class Door:
            name = "test"

            def get_json(self, path: str, params: Any = None) -> Any:
                return client.get(path, params=params).json()

            def post_json(self, path: str, body: Any = None) -> Any:
                return client.post(path, json=body).json()

            def get_bytes(self, path: str) -> bytes:
                return client.get(path).content

        worker.run_once(Door(), steps=("parse",))  # type: ignore[arg-type]
        meta = store.get_meta(con, pdf)
        assert meta["text_source"] == "marker/?"
        assert meta["parse_history"][-1]["outcome"] == "upgraded"
        # the follow-up: a formulas request, placed by the door
        assert meta["reading"]["extractor"] == "formulas"
        assert (
            meta["reading"]["state"] == "requested" and meta["reading"]["by"] == "door"
        )
        assert client.get("/readings").json()["waiting"] == 1
        # the formulas step off: a second marker read asks for nothing
        (data_dir / config.CONFIG_NAME).write_text("steps: {formulas: {model: none}}\n")
        models.reset()
        client.delete(f"/doc/{pdf}/reading")
        store.index_text(con, pdf, "other old text " * 40, text_source="pymupdf4llm/1")
        client.post(f"/doc/{pdf}/reading", json={"extractor": "marker"})
        worker.run_once(Door(), steps=("parse",))  # type: ignore[arg-type]
        meta = store.get_meta(con, pdf)
        assert (
            meta["reading"]["extractor"] == "marker"
            and meta["reading"]["state"] == "done"
        )


def test_a_scanned_pages_pictures_are_markers_crops_filed_by_the_door(
    con: sqlite3.Connection,
    data_dir: Path,
    marker_server: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scanned page holds no image object a figure could be served from:
    marker's crop of the figure is inlined by the extractor, filed in the
    archive by the door, referenced as a figure like any, served out of the
    archive, kept by a figure-refs pass, and fetched from the door by a
    worker's reading."""
    import base64
    import io

    import pymupdf
    from PIL import Image

    with pymupdf.open() as doc:  # two pages with no text layer, like a scan
        doc.new_page()
        doc.new_page()
        scan = doc.tobytes()
    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (200, 30, 30)).save(buf, "JPEG")
    crop = buf.getvalue()
    b64 = base64.b64encode(crop).decode()
    fake_out = (
        "{0}" + "-" * 48 + "\n\nA page of the scan.\n\n![](_page_0_Figure_1.jpeg)\n\n"
        "Figure 1: The rig, as printed.\n\n![](_page_0_Picture_2.jpeg)\n\n"
        "{1}" + "-" * 48 + "\n\nThe second page.\n\n![](_page_1_Figure_0.jpeg)\n"
    )
    images = {
        "_page_0_Figure_1.jpeg": b64,
        "_page_0_Picture_2.jpeg": b64,
        "_page_1_Figure_0.jpeg": "not base64!!",
    }
    monkeypatch.setattr(
        FakeMarker, "answer", staticmethod(lambda: (fake_out, images)), raising=False
    )
    text = parsers.by_name("marker")(scan)
    # both pictures of the scanned page are inlined (the same crop twice is
    # one artifact later), the one that does not decode is dropped
    inlined = list(figures.DATA_IMAGE.finditer(text))
    assert len(inlined) == 2 and "_page_" not in text
    assert (
        inlined[0].group("alt") == "Picture on page 1: Figure 1: The rig, as printed."
    )
    assert inlined[1].group("alt") == "Picture on page 1"
    # the door files them before indexing: no blob in the artifact
    doc_id = store.register(con, scan, mime="application/pdf", title="scan")["doc_id"]
    assert queue.apply_parse(con, doc_id, stamp="marker/2.0.0", text=text) == "created"
    kept = store.get_document(con, doc_id)["text"]
    refs = figures.refs(kept)
    assert "data:image" not in kept and len(refs) == 2
    ref = refs[0]["ref"]
    assert refs[0]["caption"].startswith("Picture on page 1: Figure 1")
    assert store.figure_blob(ref) == (crop, "image/jpeg")
    assert figures.find(scan, ref) is None  # not in the original
    with_ref = [c for c in chunking.chunk(kept) if c.kind == "figure" and c.data]
    assert len(with_ref) == 2 and with_ref[0].data["ref"] == ref
    # a figure-refs pass keeps them (the original will never yield them)
    assert figures.REF.search(figures.add_refs(scan, kept)) is not None
    # what a worker's reading sees: the fetch hook stands in for the archive
    # — asked first for a filed picture, so the original is not searched
    # through (every image of a 200-page scan extracted and hashed, per
    # picture); asked last for any other reference
    asked: list[str] = []

    def hook(r: str) -> tuple[bytes, str] | None:
        asked.append(r)
        return store.figure_blob(r)

    with (
        figures.fetching(hook),
        mock.patch.object(figures, "_find_in", wraps=figures._find_in) as scanned,
    ):
        assert figures.find(scan, ref, filed=True) == (crop, "image/jpeg")
        assert asked == [ref] and scanned.call_count == 0
        assert figures.find(scan, ref) == (crop, "image/jpeg")
        assert asked == [ref, ref] and scanned.call_count == 1
    assert figures.find(scan, ref) is None
