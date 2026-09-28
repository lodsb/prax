"""Reading a PDF: its text layer through MuPDF (pymupdf4llm, plain
pymupdf), OCR for scanned pages, the vision model for pages OCR cannot
read, and Docling.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
from pathlib import Path
from typing import Any

from prax import config
from prax.parsers import figures
from prax.text import markup

from .base import (
    ExtractionError,
    Partial,
    empty_pages,
    join_pages,
    pages_by_mark,
)

# ---------------------------------------------------------------- backends


def _pymupdf_open(data: bytes) -> Any:  # a pymupdf.Document, imported lazily
    pymupdf = importlib.import_module("pymupdf")
    return pymupdf.open(stream=data, filetype="pdf")


PROBE_PAGES = 5  # the first pages sampled to decide whether a PDF has a text layer
PROBE_SPREAD = 10  # and as many more, spread over the rest of it


def _has_text_layer(doc: Any) -> bool:
    """True if a sampled page carries extractable text: the first
    ``PROBE_PAGES`` and ``PROBE_SPREAD`` more spread evenly over the rest,
    because a journal issue with a scanned cover and front matter has
    its text from page seven on (231 pages, refused on the first five,
    left to the plain extractor). A scan without OCR has none anywhere;
    running layout analysis over hundreds of image-only pages takes
    minutes and yields nothing, so those are refused up front and left
    for the explicit OCR extractor.
    """
    n = doc.page_count
    probe = list(range(min(n, PROBE_PAGES)))
    if n > PROBE_PAGES:
        step = max(1, (n - PROBE_PAGES) // PROBE_SPREAD)
        probe += list(range(PROBE_PAGES, n, step))[:PROBE_SPREAD]
    return any(doc[i].get_text().strip() for i in probe)


def _pymupdf4llm(data: bytes) -> str:
    """Markdown via MuPDF's layout analysis, for born-digital PDFs.

    Layout analysis holds page renderings in memory, so a long document
    is read a window of pages at a time (``parse.layout_window``, 100:
    a 532-page book in two minutes with the process flat at ~570 MB,
    where one pass over it all used to be refused and the plain
    extractor left it as one line per line of print, no headings, the
    equations in pieces). The page numbers in the separators are the
    document's own either way; a heading's level is ranked among the
    heading sizes of its own window, so a window without a chapter
    title may rank its sections one level up — the price of the window,
    small at a hundred pages. Originals above ``PRAX_MAX_LAYOUT_MB``
    (default 200; it was 40 before the window bounded the memory — a
    71 MB manual of 52 pages takes 43 s and 514 MB) are still refused
    so the plain extractor handles them instead of the process being
    killed.
    """
    limit_mb = config.number("parse.max_layout_mb", "PRAX_MAX_LAYOUT_MB", 200)
    if len(data) > limit_mb * 1e6:
        raise ExtractionError(
            f"{len(data) / 1e6:.0f} MB exceeds PRAX_MAX_LAYOUT_MB={limit_mb:g};"
            " plain extraction instead"
        )
    pymupdf4llm = importlib.import_module("pymupdf4llm")
    window = max(1, config.whole("parse.layout_window", "PRAX_LAYOUT_WINDOW", 100))
    with _pymupdf_open(data) as doc:
        if not _has_text_layer(doc):
            raise ExtractionError("no text layer in the first pages; needs OCR")
        if doc.page_count <= window:
            text = pymupdf4llm.to_markdown(doc, use_ocr=False, page_separators=True)
        else:
            parts = []
            for start in range(0, doc.page_count, window):
                pages = list(range(start, min(start + window, doc.page_count)))
                parts.append(
                    pymupdf4llm.to_markdown(
                        doc, pages=pages, use_ocr=False, page_separators=True
                    )
                )
            text = "".join(parts)
        return figures.place(text, figures.pdf_figures(doc))


OCR_DEFAULT_LANGUAGE = "ch"  # RapidOCR's own default: Chinese and English


def _ocr_language() -> str:
    """Which script the OCR recognizer reads (``parse.ocr_language``): one
    of RapidOCR's recognizer languages, ``ch`` (Chinese and English, the
    default), ``en``, ``latin``, ``arabic``, ``cyrillic``, ``devanagari``,
    ``japan``, ``korean``, ``el``, ``th``... A scan in another script read
    with the wrong recognizer comes out as letter salad, silently."""
    lang = config.setting(
        "parse.ocr_language", "PRAX_OCR_LANGUAGE", OCR_DEFAULT_LANGUAGE
    )
    return str(lang or OCR_DEFAULT_LANGUAGE).strip().lower()


def _ocr_variant() -> str:
    """The stamp's ``+<language>`` when it is not the default one."""
    lang = _ocr_language()
    return "" if lang == OCR_DEFAULT_LANGUAGE else lang


def _ocr_gpu() -> bool:
    """``parse.ocr_gpu``: run the OCR models on DirectML (needs
    ``onnxruntime-directml``); a page takes a fraction of a second instead
    of one."""
    flag = str(config.setting("parse.ocr_gpu", "PRAX_OCR_GPU", "0")).lower()
    return flag in ("1", "true", "yes", "on")


# scripts the font pymupdf4llm writes OCR text with (Droid Sans Fallback:
# Latin, Greek, Cyrillic, CJK) cannot encode: for these prax runs the
# recognizer itself and writes the lines as text
_OWN_OCR_SCRIPTS = frozenset({"arabic", "devanagari", "ta", "te", "th", "ka"})
_RIGHT_TO_LEFT = frozenset({"arabic"})


def _ocr_engine() -> Any:
    """Point pymupdf4llm's RapidOCR backend at the configured recognizer,
    and return that engine.

    pymupdf4llm builds one ``RapidOCR()`` with the defaults and keeps it in
    its backend module; the language and the execution provider can only
    be chosen by building that engine ourselves and putting it there, once
    per choice. The recognizer for a language is the newest one RapidOCR
    ships (PP-OCRv5 for most scripts); detection stays the default, which
    finds text in any script.
    """
    choice = (_ocr_language(), _ocr_gpu())
    backend = importlib.import_module("pymupdf4llm.ocr.rapidocr_391_backend")
    if getattr(backend, "_prax_choice", None) == choice:
        return backend.ENGINE or backend.init_engine()
    lang, gpu = choice
    params: dict[str, Any] = {}
    if lang != OCR_DEFAULT_LANGUAGE:
        typings = importlib.import_module("rapidocr.utils.typings")
        try:
            params["Rec.lang_type"] = typings.LangRec(lang)
        except ValueError as exc:
            known = ", ".join(m.value for m in typings.LangRec)
            raise ExtractionError(
                f"parse.ocr_language {lang!r} is not a RapidOCR language ({known})"
            ) from exc
        params["Rec.ocr_version"] = typings.OCRVersion(_ocr_model_version(lang))
        params["Rec.model_type"] = typings.ModelType("mobile")
    if gpu:
        params["EngineConfig.onnxruntime.use_dml"] = True
    rapidocr = importlib.import_module("rapidocr")
    # the backend module keeps its engine in a global; prax sets it
    setattr(backend, "ENGINE", rapidocr.RapidOCR(params=params) if params else None)  # noqa: B010
    setattr(backend, "_prax_choice", choice)  # noqa: B010
    return getattr(backend, "ENGINE", None) or backend.init_engine()


def _ocr_page(page: Any, engine: Any, *, right_to_left: bool) -> str:
    """One page's picture read by the recognizer: its lines in reading
    order. For scripts the OCR font cannot write; no layout analysis,
    which a scanned book rarely has to give."""
    import numpy as np

    pix = page.get_pixmap(dpi=150)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[
        :, :, :3
    ]
    result = engine(img)
    boxes = [] if result.boxes is None else list(result.boxes)
    texts = [] if result.txts is None else list(result.txts)
    return "\n".join(_ocr_rows(boxes, texts, right_to_left=right_to_left))


def _ocr_rows(boxes: list[Any], texts: list[str], *, right_to_left: bool) -> list[str]:
    """Recognized boxes grouped into rows by their vertical position and
    ordered along the row in the script's direction."""
    items = []
    for box, text in zip(boxes, texts, strict=False):
        if not text or not str(text).strip():
            continue
        ys = [float(p[1]) for p in box]
        xs = [float(p[0]) for p in box]
        items.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(xs), str(text)))
    if not items:
        return []
    items.sort(key=lambda t: t[0])
    heights = sorted(t[1] for t in items)
    slack = max(4.0, heights[len(heights) // 2] * 0.6)
    rows: list[list[tuple[float, float, float, str]]] = []
    for it in items:
        if rows and abs(rows[-1][-1][0] - it[0]) <= slack:
            rows[-1].append(it)
        else:
            rows.append([it])
    lines = []
    for row in rows:
        row.sort(key=lambda t: t[2], reverse=right_to_left)
        lines.append(" ".join(t[3] for t in row))
    return lines


def _ocr_model_version(lang: str) -> str:
    """The newest PP-OCR version with a mobile recognizer for ``lang`` in
    RapidOCR's model catalogue."""
    import yaml

    rapidocr = importlib.import_module("rapidocr")
    catalogue = Path(str(rapidocr.__file__)).parent / "default_models.yaml"
    models = yaml.safe_load(catalogue.read_text(encoding="utf-8"))
    for version in ("PP-OCRv6", "PP-OCRv5", "PP-OCRv4", "PP-OCRv3"):
        names = models.get("onnxruntime", {}).get(version, {}).get("rec", {})
        if any(n.startswith(f"{lang}_") for n in names):
            return version
    raise ExtractionError(f"RapidOCR ships no recognizer for {lang!r}")


def _pymupdf4llm_ocr(data: bytes, *, previous: str | None = None) -> str:
    """Markdown with RapidOCR on pages that have no text layer.

    OCR costs seconds per page on a CPU, so a pass reads at most
    ``parse.ocr_max_pages`` (60) pages without a text layer: a longer scan
    is read in windows (stage Y). The pages with text come from
    ``previous`` (the current text, the pages read before included) or,
    the first time, from pymupdf4llm without OCR; the next window of the
    empty ones is read; the text comes back as a ``Partial`` while pages
    wait, and the door asks for the next window. The recognizer's language
    and device are settings (``parse.ocr_language``, ``parse.ocr_gpu``);
    the language is part of the text-source stamp.
    """
    pymupdf4llm = importlib.import_module("pymupdf4llm")
    budget = config.whole("parse.ocr_max_pages", "PRAX_OCR_MAX_PAGES", 60)
    with _pymupdf_open(data) as doc:
        count = doc.page_count
        held = pages_by_mark(previous or "")
        if not held:
            held = pages_by_mark(pymupdf4llm.to_markdown(doc, page_separators=True))
        pages = [held.get(n, "") for n in range(1, count + 1)]
        empty = empty_pages(pages)
        window = empty[:budget]
        if window:
            engine = _ocr_engine()
            lang = _ocr_language()
            if lang in _OWN_OCR_SCRIPTS:
                for i in window:
                    pages[i] = _ocr_page(
                        doc[i], engine, right_to_left=lang in _RIGHT_TO_LEFT
                    )
            else:
                read = pages_by_mark(
                    pymupdf4llm.to_markdown(
                        doc, pages=window, use_ocr=True, page_separators=True
                    )
                )
                for i in window:
                    pages[i] = read.get(i + 1, pages[i])
    text = join_pages(pages)
    left = len(empty) - len(window)
    return Partial(text, pages_left=left, pages=count) if left else text


def _vision_pages_mode() -> str:
    mode = str(config.setting("parse.vision_pages", "PRAX_VISION_PAGES", "scans"))
    if mode not in ("scans", "all"):
        raise ExtractionError(f"parse.vision_pages must be scans or all, not {mode!r}")
    return mode


def _vision_pages_variant() -> str:
    from prax.parsers import vision

    mode = _vision_pages_mode()
    return vision.model_name() + ("" if mode == "scans" else f"+{mode}")


def _vision_pages(data: bytes) -> str:
    """The document page by page through the vision model.

    A page with a text layer keeps it (as MuPDF reads it); a page without
    one — a scan, a photo of a notebook, a score — is rendered and
    transcribed by the vision step's model, which reads handwriting and
    describes figures where OCR gives up. ``parse.vision_pages=all``
    renders every page (printed pages with handwritten notes in the
    margin). Page markers as the OCR extractor writes them, so the chunker
    knows every line's page. The model takes ten to twenty seconds a page
    locally, so documents above ``parse.vision_max_pages`` are refused and
    left for a deliberate run with a higher budget.
    """
    from prax.parsers import vision

    mode = _vision_pages_mode()
    budget = config.whole("parse.vision_max_pages", "PRAX_VISION_MAX_PAGES", 200)
    dpi = config.whole("parse.vision_dpi", "PRAX_VISION_DPI", 150)
    with _pymupdf_open(data) as doc:
        if doc.page_count > budget:
            raise ExtractionError(
                f"{doc.page_count} pages exceeds the vision budget of {budget}"
                " (PRAX_VISION_MAX_PAGES)"
            )
        out: list[str] = []
        for page in doc:
            text = page.get_text().strip()
            if mode == "scans" and len(text) >= 20:
                out.append(text)
            else:
                pix = page.get_pixmap(dpi=dpi)
                out.append(vision.transcribe_page(pix.tobytes("jpg", jpg_quality=88)))
            out.append(markup.page_break(page.number + 1))
    return "".join(out)


def _pymupdf(data: bytes) -> str:
    with _pymupdf_open(data) as doc:
        return "\n\n".join(page.get_text() for page in doc)


def _docling(data: bytes) -> str:
    from io import BytesIO

    converter_mod = importlib.import_module("docling.document_converter")
    base = importlib.import_module("docling.datamodel.base_models")
    stream = base.DocumentStream(name="document.pdf", stream=BytesIO(data))
    result = converter_mod.DocumentConverter().convert(stream)
    return result.document.export_to_markdown()
