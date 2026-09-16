"""Pluggable text extractors, keyed by name and selected by MIME type.

An extractor turns the archived original bytes of a document into text.
The queue (``prax.parsers.queue``) records which one produced the current
text in ``documents.meta.text_source`` as ``"<name>/<version>"`` so a later,
better extractor can find and upgrade what an earlier one wrote (chunks are
disposable, rationale R3).

Registered extractors (heavy imports happen on first use, never at import
time, so the serving path never loads them):

| name            | MIME            | what                                          |
|-----------------|-----------------|-----------------------------------------------|
| pymupdf4llm     | application/pdf | MuPDF, Markdown with headings and tables      |
| pymupdf         | application/pdf | MuPDF plain text; 40x faster, no structure    |
| pymupdf4llm-ocr | application/pdf | as pymupdf4llm plus RapidOCR on scanned pages;|
|                 |                 | explicit only, page budget PRAX_OCR_MAX_PAGES |
| docling         | application/pdf | IBM Docling layout + table models; explicit   |
|                 |                 | only, seconds per page                        |
| trafilatura     | text/html       | article Markdown, boilerplate stripped, code  |
|                 |                 | blocks fenced                                 |
| docx            | .docx           | Word: zip of XML read here, headings, lists,  |
|                 |                 | tables; no dependency                         |
| office          | .doc .rtf .odt  | LibreOffice converts to .docx, then as above; |
|                 |                 | needs LibreOffice installed                   |
| plain           | text/*          | decode as UTF-8; a source file (by extension, |
|                 |                 | else Magika) becomes one fenced code block;   |
|                 |                 | code regions inside prose are fenced          |
| figures         | .pdf .html      | the vision model reads every figure the text   |
|                 |                 | references and writes what it shows under it  |
| figure-refs     | .pdf .html      | the original's figures referenced in the      |
|                 |                 | current text, nothing else touched (the       |
|                 |                 | retroactive pass; the parsers find them now)  |
| vision-pages    | .pdf            | scanned pages read by the vision step's model |
|                 |                 | (handwriting, scores, what OCR cannot read);  |
|                 |                 | pages with a text layer keep it; explicit     |
|                 |                 | only, PRAX_VISION_PAGES=all reads every page  |
| vision          | image/*         | the vision step's model (Claude, or llama-    |
|                 |                 | server with a projector) describes the image  |
|                 |                 | and transcribes its text, handwriting         |
|                 |                 | included; explicit only; stamped with the     |
|                 |                 | model                                         |
| claude-vision   | image/*         | the same, pinned to Claude (the earlier name) |

Adding one: write a function ``bytes -> str``, wrap it in ``Extractor`` and
append it to ``REGISTRY``. Order within a MIME type is the preference and
fallback order; ``explicit_only`` extractors are used only when named. An
extractor whose output changes without a package release bumps its
``revision`` so the queue's ``--upgrade`` re-selects what it wrote.
"""

from __future__ import annotations

import functools
import importlib
import importlib.metadata
import mimetypes
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prax import config
from prax.parsers import figures


@dataclass(frozen=True)
class Extractor:
    name: str
    mimes: tuple[str, ...]  # exact types, or a prefix ending in "/"
    fn: Callable[..., str]
    package: str | None = None  # distribution whose version is stamped
    explicit_only: bool = False  # never chosen by default or as a fallback
    revision: int = 1  # our own changes to what the extractor produces
    hints: bool = False  # the function takes filename= as a keyword
    check: Callable[[], bool] | None = None  # more than an import: a program
    variant: Callable[[], str] | None = None  # a setting that changes the output
    previous: bool = False  # takes previous= (the current text) and may keep it
    annotates: bool = False  # adds to the current text: its source stamp stays
    # what the annotation amounts to: after it, the named extractor's text
    # is at that revision (figure refs placed = pymupdf4llm r2), so the
    # source stamp moves there and the document is not read again for it
    covers: tuple[tuple[str, int], ...] = ()
    # the version when it is not a package of this process (a server's)
    version_of: Callable[[], str] | None = None

    def accepts(self, mime: str) -> bool:
        return any(
            mime == m or (m.endswith("/") and mime.startswith(m)) for m in self.mimes
        )

    def available(self) -> bool:
        if self.check is not None and not self.check():
            return False
        if self.package is None:
            return True
        try:
            importlib.import_module(self.package)
        except ImportError:
            return False
        return True

    @property
    def version(self) -> str:
        if self.version_of is not None:
            base = self.version_of()
        elif self.package is None:
            base = "1"
        else:
            try:
                base = importlib.metadata.version(self.package)
            except importlib.metadata.PackageNotFoundError:
                base = "?"
        if self.revision != 1:
            base = f"{base}-r{self.revision}"
        variant = self.variant() if self.variant else ""
        return f"{base}+{variant}" if variant else base

    @property
    def stamp(self) -> str:
        """What ``meta.text_source`` records: ``"<name>/<version>"``, with
        a ``+<variant>`` when a setting changed what the extractor reads
        (``pymupdf4llm-ocr/1.28.2+arabic``)."""
        return f"{self.name}/{self.version}"

    def __call__(
        self, data: bytes, *, filename: str | None = None, previous: str | None = None
    ) -> str:
        kw: dict[str, Any] = {}
        if self.hints:
            kw["filename"] = filename
        if self.previous:
            kw["previous"] = previous
        return self.fn(data, **kw)


class ExtractionError(RuntimeError):
    """The extractor ran but produced nothing usable."""


# ---------------------------------------------------------------- backends


def _pymupdf_open(data: bytes) -> Any:  # a pymupdf.Document, imported lazily
    pymupdf = importlib.import_module("pymupdf")
    return pymupdf.open(stream=data, filetype="pdf")


PROBE_PAGES = 5  # pages sampled to decide whether a PDF has a text layer


def _has_text_layer(doc: Any) -> bool:
    """True if any of the first ``PROBE_PAGES`` pages carries extractable text.

    A scan without OCR has none; running layout analysis over hundreds of
    image-only pages takes minutes and yields nothing, so such documents are
    refused up front and left for the explicit OCR extractor.
    """
    n = min(doc.page_count, PROBE_PAGES)
    return any(doc[i].get_text().strip() for i in range(n))


def _pymupdf4llm(data: bytes) -> str:
    """Markdown via MuPDF's layout analysis, for born-digital PDFs.

    Layout analysis holds page renderings in memory; originals above
    ``PRAX_MAX_LAYOUT_MB`` (default 40) are refused so the plain extractor
    handles them instead of the process being killed.
    """
    limit_mb = config.number("parse.max_layout_mb", "PRAX_MAX_LAYOUT_MB", 40)
    if len(data) > limit_mb * 1e6:
        raise ExtractionError(
            f"{len(data) / 1e6:.0f} MB exceeds PRAX_MAX_LAYOUT_MB={limit_mb:g};"
            " plain extraction instead"
        )
    pymupdf4llm = importlib.import_module("pymupdf4llm")
    max_pages = config.whole("parse.max_layout_pages", "PRAX_MAX_LAYOUT_PAGES", 400)
    with _pymupdf_open(data) as doc:
        if not _has_text_layer(doc):
            raise ExtractionError("no text layer in the first pages; needs OCR")
        if doc.page_count > max_pages:
            raise ExtractionError(
                f"{doc.page_count} pages exceeds PRAX_MAX_LAYOUT_PAGES={max_pages};"
                " plain extraction instead"
            )
        text = pymupdf4llm.to_markdown(doc, use_ocr=False, page_separators=True)
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
    backend.ENGINE = rapidocr.RapidOCR(params=params) if params else None
    backend._prax_choice = choice
    return backend.ENGINE or backend.init_engine()


def _ocr_pages(doc: Any, engine: Any, *, right_to_left: bool) -> str:
    """The document as text, page by page: a page with a text layer as
    MuPDF reads it, a scanned page as the recognizer's lines in reading
    order, and pymupdf4llm's page markers between pages so the chunker
    knows the page of every line. For scripts the OCR font cannot write;
    no layout analysis, which a scanned book rarely has to give."""
    import numpy as np

    out: list[str] = []
    for page in doc:
        text = page.get_text().strip()
        if len(text) < 20:  # no text layer worth the name: read the picture
            pix = page.get_pixmap(dpi=150)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.h, pix.w, pix.n
            )[:, :, :3]
            result = engine(img)
            boxes = [] if result.boxes is None else list(result.boxes)
            texts = [] if result.txts is None else list(result.txts)
            text = "\n".join(_ocr_rows(boxes, texts, right_to_left=right_to_left))
        out.append(text)
        out.append(f"\n\n--- end of page.page_number={page.number + 1} ---\n\n")
    return "".join(out)


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
    catalogue = Path(rapidocr.__file__).parent / "default_models.yaml"
    models = yaml.safe_load(catalogue.read_text(encoding="utf-8"))
    for version in ("PP-OCRv6", "PP-OCRv5", "PP-OCRv4", "PP-OCRv3"):
        names = models.get("onnxruntime", {}).get(version, {}).get("rec", {})
        if any(n.startswith(f"{lang}_") for n in names):
            return version
    raise ExtractionError(f"RapidOCR ships no recognizer for {lang!r}")


def _pymupdf4llm_ocr(data: bytes) -> str:
    """Markdown with RapidOCR on pages that have no text layer.

    OCR costs seconds per page on a CPU, so documents above
    ``PRAX_OCR_MAX_PAGES`` (default 60) are refused with ``ExtractionError``
    and left for a deliberate run with a higher budget. The recognizer's
    language and device are settings (``parse.ocr_language``,
    ``parse.ocr_gpu``); the language is part of the text-source stamp.
    """
    pymupdf4llm = importlib.import_module("pymupdf4llm")
    budget = config.whole("parse.ocr_max_pages", "PRAX_OCR_MAX_PAGES", 60)
    with _pymupdf_open(data) as doc:
        if doc.page_count > budget:
            raise ExtractionError(
                f"{doc.page_count} pages exceeds the OCR budget of {budget}"
                " (PRAX_OCR_MAX_PAGES)"
            )
        engine = _ocr_engine()
        lang = _ocr_language()
        if lang in _OWN_OCR_SCRIPTS:
            return _ocr_pages(doc, engine, right_to_left=lang in _RIGHT_TO_LEFT)
        return pymupdf4llm.to_markdown(doc, use_ocr=True, page_separators=True)


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
            out.append(f"\n\n--- end of page.page_number={page.number + 1} ---\n\n")
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


MARKER_URL = "http://127.0.0.1:8765"
_MARKER_PAGE = re.compile(r"\n*\{(\d+)\}-{20,}\n*")  # {n} and 48 dashes before page n
_MARKER_IMAGE = re.compile(
    r"^!\[[^\]\n]*\]\(_page_\d+_[A-Za-z]+_\d+\.\w+\)[ \t]*\n?", re.MULTILINE
)


def _marker_url() -> str:
    """``parse.marker_url`` [``PRAX_MARKER_URL``]: marker's server, the
    ``marker`` role of ``prax up`` (howto 3h) or one elsewhere."""
    return str(
        config.setting("parse.marker_url", "PRAX_MARKER_URL", MARKER_URL)
    ).rstrip("/")


def _marker_mode() -> str:
    """``parse.marker_mode`` [``PRAX_MARKER_MODE``]: ``fast`` (layout on the
    CPU, the recognition model for the maths and the tables; 2 s a page
    on a card) or ``balanced`` (the vision model lays out too)."""
    mode = str(config.setting("parse.marker_mode", "PRAX_MARKER_MODE", "fast")).lower()
    return mode if mode in ("fast", "balanced") else "fast"


def _marker_variant() -> str:
    return "" if _marker_mode() == "fast" else _marker_mode()


def _marker_version() -> str:
    """marker-pdf's version for the stamp: read from the venv ``run.marker``
    names when the server is this host's, else ``parse.marker_version``,
    else unknown. The server itself does not say."""
    venv = config.setting("run.marker.venv")
    if venv:
        for info in (
            Path(str(venv)).expanduser().glob("[Ll]ib/**/marker_pdf-*.dist-info")
        ):
            return info.name[len("marker_pdf-") : -len(".dist-info")]
    return str(config.setting("parse.marker_version", "PRAX_MARKER_VERSION", "?"))


def _marker_up() -> bool:
    import httpx

    try:
        return httpx.get(f"{_marker_url()}/", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def _marker_pages(text: str, count: int) -> str:
    """marker's ``{n}`` and a rule before each page (0-based) become the
    ``--- end of page.page_number=n ---`` lines (1-based, after the page)
    the chunker reads locators from."""

    def mark(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return "\n\n" if n == 0 else f"\n\n--- end of page.page_number={n} ---\n\n"

    out = _MARKER_PAGE.sub(mark, text).strip()
    if count:
        out += f"\n\n--- end of page.page_number={count} ---\n"
    return out


def _marker(data: bytes) -> str:
    """The PDF through marker's server (``marker`` in ``run:``): the
    mathematics as LaTeX — ``$$…$$`` on its own line for a display
    equation, which the chunker makes a ``formula`` chunk of — tables as
    Markdown, headings kept. marker's own image references point at files
    it did not write here and are dropped; prax's figure references are
    placed as for every PDF, by hash out of the original. Measured
    2026-09-16: 2 s a page on a 4090, 33 on the CPU
    (``docs/eval/marker-equations-2026-09-15.md``)."""
    import httpx

    max_pages = config.whole("parse.max_layout_pages", "PRAX_MAX_LAYOUT_PAGES", 400)
    with _pymupdf_open(data) as doc:
        count = doc.page_count
        if count > max_pages:
            raise ExtractionError(
                f"{count} pages exceeds PRAX_MAX_LAYOUT_PAGES={max_pages}"
            )
        figs = figures.pdf_figures(doc)
    try:
        r = httpx.post(
            f"{_marker_url()}/marker/upload",
            files={"file": ("document.pdf", data, "application/pdf")},
            data={
                "output_format": "markdown",
                "mode": _marker_mode(),
                "paginate_output": "true",
            },
            timeout=httpx.Timeout(30.0, read=max(600.0, 20.0 * count)),
        )
    except httpx.HTTPError as exc:
        raise ExtractionError(f"marker's server at {_marker_url()}: {exc}") from exc
    if r.status_code != 200:
        raise ExtractionError(f"marker's server answered {r.status_code}")
    body = r.json()
    if not body.get("success"):
        raise ExtractionError(f"marker failed: {str(body.get('error', ''))[:200]}")
    text = _MARKER_IMAGE.sub("", str(body.get("output") or ""))
    text = _marker_pages(text, count)
    return figures.place(text, figs)


def _comments_wanted() -> bool:
    """``parse.comments`` [``PRAX_COMMENTS``]: a page's comment section
    kept under its own heading (on by default: a thread under an article
    is where the corrections and the jokes are)."""
    from prax import config

    value = str(config.setting("parse.comments", "PRAX_COMMENTS", "true")).lower()
    return value not in ("0", "false", "no", "off")


def _trafilatura_variant() -> str:
    return "" if _comments_wanted() else "nocomments"


COMMENTS_HEADING = "## Comments"


def _trafilatura(data: bytes) -> str:
    trafilatura = importlib.import_module("trafilatura")
    text = trafilatura.extract(
        data,
        output_format="markdown",  # headings, lists and fenced code blocks
        include_tables=True,
        include_links=False,
        include_comments=False,
    )
    if not text:
        raise ExtractionError("trafilatura found no main content")
    meta = trafilatura.extract_metadata(data)
    title = getattr(meta, "title", None) if meta else None
    text = f"# {title}\n\n{text}" if title and title not in text[:200] else text
    if _comments_wanted():
        # the comment section, when the snapshot has one: its own heading,
        # so every comment is a chunk under "Comments" and the article's
        # own text stays what a search hit or an extraction reads first
        try:
            bare = trafilatura.bare_extraction(data, include_comments=True)
        except Exception:  # noqa: BLE001 - the article is what matters
            bare = None
        comments = (getattr(bare, "comments", None) or "").strip() if bare else ""
        first, _, rest = comments.partition("\n")
        if first.strip().lower() in ("comments", "comment", "responses", "replies"):
            comments = rest.strip()  # the section's own heading: ours says it
        if comments:
            text = f"{text.rstrip()}\n\n{COMMENTS_HEADING}\n\n{comments}\n"
    return figures.place(text, figures.html_figures(data))


def _figures(
    data: bytes, *, filename: str | None = None, previous: str | None = None
) -> str:
    """The vision model's reading of every figure the text references,
    written under each; needs the document parsed first."""
    if not previous:
        raise ExtractionError("no text to put readings in: parse the document first")
    return figures.describe(data, previous)


def _figures_model() -> str:
    from prax.parsers import vision

    return vision.model_name()


def _figure_refs(
    data: bytes, *, filename: str | None = None, previous: str | None = None
) -> str:
    """The original's figures referenced in the current text, nothing else
    changed: the retroactive pass over a library parsed before figures
    were found, without re-reading the pages."""
    if not previous:
        raise ExtractionError("no text to put the figures in: parse the document first")
    return figures.add_refs(data, previous)


def _vision(
    data: bytes, *, filename: str | None = None, previous: str | None = None
) -> str:
    from prax.parsers import vision

    return vision.describe(data, filename=filename, previous=previous)


def _claude_vision(data: bytes, *, filename: str | None = None) -> str:
    from prax.parsers import vision

    return vision.describe(data, filename=filename, claude_only=True)


def _vision_model() -> str:
    from prax.parsers import vision

    return vision.model_name()


def _plain(data: bytes, *, filename: str | None = None) -> str:
    text = data.decode("utf-8", errors="replace")
    if "```" in text:
        return text  # already fenced (Markdown)
    lang = code_language(text, filename)
    if lang is not None:
        return f"```{lang}\n{text.strip()}\n```"
    return fence_code_regions(text)


# ---------------------------------------------------- mixed prose and code
# A forum thread pasted into a note, a chat log with a function in the
# middle: the file as a whole is prose, so Magika says "text" and the
# chunker windows straight through the code. Lines are scored for code
# signals, runs of code lines become fenced blocks (one chunk each), and the
# prose around them stays prose.

_CODE_LINE_START = re.compile(
    r"^\s*(//|/\*|\*/|\*\s|#include|#define|#pragma|#!|import\s|from\s+\S+\s+import"
    r"|def\s|class\s|return\b|if\s*\(|else\b|for\s*\(|while\s*\(|do\b|switch\s*\("
    r"|case\b|inline\b|static\b|const\b|void\b|int\b|double\b|float\b|bool\b|char\b"
    r"|auto\b|var\b|let\b|function\b|end\b|endfunction\b|elif\b|try:|except\b"
    r"|\}|\{|\)|\]|[A-Za-z_][\w:.]*\s*=\s*[^=]|[A-Za-z_][\w:.]*\s*\+=|self\.|this->)"
)
_CODE_LINE_END = re.compile(r"[;{}]\s*$|\)\s*$|\]\s*$|,\s*$")
_CODE_OPERATORS = re.compile(
    r"==|!=|<=|>=|&&|\|\||->|::|\+\+|--|\+=|-=|\*=|/=|<<|>>|\(\)"
)
_PROSE_END = re.compile(r"[.!?:]\s*$")
_WORD = re.compile(r"[A-Za-z]{2,}")
CODE_MIN_LINES = 4  # shorter regions stay prose: an inline snippet


def code_line_score(line: str) -> float:
    """0 for prose, 1 for code, from cheap surface signals; blank lines are
    neutral (0.5) so they neither start nor end a region."""
    s = line.strip()
    if not s:
        return 0.5
    score = 0.0
    if _CODE_LINE_START.match(line):
        score += 0.5
    if _CODE_LINE_END.search(s):
        score += 0.3
    if _CODE_OPERATORS.search(s):
        score += 0.3
    if line.startswith(("    ", "\t")):
        score += 0.15
    words = _WORD.findall(s)
    symbols = sum(1 for ch in s if ch in "{}()[];=<>+*/&|^%!#")
    if symbols >= 3:
        score += 0.2
    if len(words) >= 8 and symbols <= 1 and _PROSE_END.search(s):
        score -= 0.6
    elif len(words) >= 12 and symbols <= 2:
        score -= 0.3
    if (
        " " in s
        and len(words) >= 6
        and not _CODE_LINE_START.match(line)
        and symbols == 0
    ):
        score -= 0.3
    return max(0.0, min(1.0, score))


def code_regions(lines: list[str], *, threshold: float = 0.5) -> list[tuple[int, int]]:
    """(start, end) line ranges that look like code: runs of scoring lines,
    allowing single prose-looking lines inside a run (a comment written as
    a sentence), trimmed to code lines at both ends, at least
    ``CODE_MIN_LINES`` long."""
    scores = [code_line_score(ln) for ln in lines]
    # everything inside a /* ... */ block comment is code, whatever it says
    inside = False
    for k, ln in enumerate(lines):
        stripped = ln.strip()
        if inside or stripped.startswith("/*"):
            scores[k] = 1.0
            inside = not ("*/" in stripped and not stripped.endswith("/*"))
        if not inside and stripped.startswith("/*") and "*/" not in stripped:
            inside = True
    regions: list[tuple[int, int]] = []
    i = 0
    n = len(lines)
    while i < n:
        if scores[i] < threshold or not lines[i].strip():
            i += 1
            continue
        j = i
        miss = 0
        last_code = i
        while j < n:
            sc = scores[j]
            if not lines[j].strip():
                j += 1
                continue
            if sc >= threshold:
                last_code = j
                miss = 0
            else:
                miss += 1
                if miss > 1:
                    break
            j += 1
        start, end = i, last_code + 1
        code_lines = sum(1 for k in range(start, end) if lines[k].strip())
        if code_lines >= CODE_MIN_LINES:
            regions.append((start, end))
        i = max(end, i + 1)
    return regions


def fence_code_regions(text: str) -> str:
    """Wrap code regions of a mixed prose/code text in Markdown fences, with
    the language Magika gives the region when it is confident."""
    lines = text.split("\n")
    regions = code_regions(lines)
    if not regions:
        return text
    out: list[str] = []
    pos = 0
    for start, end in regions:
        out.extend(lines[pos:start])
        block = "\n".join(lines[start:end])
        lang = code_language(block, None) or ""
        out.append(f"```{lang}")
        out.append(block)
        out.append("```")
        pos = end
    out.extend(lines[pos:])
    return "\n".join(out)


# Source files a Zotero attachment or a drop folder may hold, by extension.
CODE_EXTENSIONS = {
    "py": "python", "c": "c", "h": "c", "cpp": "cpp", "cc": "cpp", "hpp": "cpp",
    "js": "javascript", "ts": "typescript", "java": "java", "m": "matlab",
    "scd": "supercollider", "sc": "supercollider", "lua": "lua", "rs": "rust",
    "go": "go", "jl": "julia", "r": "r", "sh": "bash", "ps1": "powershell",
    "pd": "puredata", "dsp": "faust", "lib": "faust", "cs": "csharp",
    "swift": "swift", "kt": "kotlin", "sql": "sql", "json": "json", "yaml": "yaml",
    "yml": "yaml", "toml": "toml", "css": "css", "max": "max",
}  # fmt: skip
MAGIKA_MIN_SCORE = 0.75
MAGIKA_SAMPLE = 64_000  # bytes; the classifier looks at the head and tail
# Content-based detection accepts programming languages only: a bibliographic
# note full of "Key: value" lines scores as YAML, and data formats are prose
# to the reader anyway. An extension still wins for these.
MAGIKA_DATA_LABELS = frozenset(
    {"yaml", "json", "toml", "xml", "ini", "csv", "tsv", "markdown", "txt", "html"}
)


def code_language(text: str, filename: str | None = None) -> str | None:
    """The language when ``text`` is source code, else None. The filename's
    extension decides when it is telling; otherwise Magika (Google's small
    content-type model, optional) classifies the bytes and its language
    label is used when it is confident the content is code."""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[1].lower()
        if ext in CODE_EXTENSIONS:
            return CODE_EXTENSIONS[ext]
        if ext in ("txt", "md", "markdown", "rst", "html", "htm", "csv", "tsv"):
            return None
    if not text.strip():
        return None
    try:
        magika = importlib.import_module("magika")
    except ImportError:
        return None
    result = _magika(magika).identify_bytes(text[:MAGIKA_SAMPLE].encode("utf-8"))
    out = result.output
    if (
        out.group == "code"
        and result.score >= MAGIKA_MIN_SCORE
        and out.label not in MAGIKA_DATA_LABELS
    ):
        return str(out.label)
    return None


_MAGIKA: Any = None


def _magika(module: Any) -> Any:
    global _MAGIKA
    if _MAGIKA is None:
        _MAGIKA = module.Magika()
    return _MAGIKA


# ------------------------------------------------------------ Word documents
# A .docx is a zip of XML and needs no dependency: word/document.xml holds the
# body in document order. Old .doc files, RTF and OpenDocument go through
# LibreOffice, which a person installs for themselves; prax only calls it.

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OFFICE_TIMEOUT = 180.0
# Heading styles as Word writes them in the languages this library holds; the
# outline level below is checked first and is language-independent.
_HEADING = re.compile(
    r"^(heading|berschrift|überschrift|titre|titolo|encabezado|rubrik|kop)(\d)$"
)


def _docx_runs(paragraph: Any) -> str:
    """The text of one paragraph: runs joined, tabs and breaks kept. Field
    codes and tracked deletions carry their own tags and are left out."""
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{W}t":
            parts.append(node.text or "")
        elif node.tag == f"{W}tab":
            parts.append("\t")
        elif node.tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _docx_heading(paragraph: Any) -> int:
    """The heading level of a paragraph, 0 for body text."""
    properties = paragraph.find(f"{W}pPr")
    if properties is None:
        return 0
    outline = properties.find(f"{W}outlineLvl")
    if outline is not None:
        try:
            return min(int(outline.get(f"{W}val", "")) + 1, 6)
        except ValueError:
            pass
    style = properties.find(f"{W}pStyle")
    name = ((style.get(f"{W}val") if style is not None else "") or "").lower()
    flat = re.sub(r"[\s_-]", "", name)
    if flat == "title":
        return 1
    if flat == "subtitle":
        return 2
    found = _HEADING.match(flat)
    return min(int(found.group(2)), 6) if found else 0


def _docx_table(table: Any) -> str:
    """A table as Markdown, which is what the chunker reads as a table."""
    rows: list[list[str]] = []
    for row in table.findall(f"{W}tr"):
        cells = []
        for cell in row.findall(f"{W}tc"):
            texts = [t for t in (_docx_runs(p) for p in cell.findall(f"{W}p")) if t]
            cells.append(" ".join(texts).replace("|", r"\|"))
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, *rest = rows
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * width) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rest]
    return "\n".join(lines)


def _docx_blocks(parent: Any) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for child in parent:
        if child.tag == f"{W}p":
            text = _docx_runs(child)
            if not text:
                continue
            level = _docx_heading(child)
            if level:
                blocks.append(("h", "#" * level + " " + text))
            elif child.find(f"{W}pPr/{W}numPr") is not None:
                blocks.append(("li", "- " + text))
            else:
                blocks.append(("p", text))
        elif child.tag == f"{W}tbl":
            table = _docx_table(child)
            if table:
                blocks.append(("table", table))
        elif child.tag == f"{W}sdt":  # a content control wraps real content
            content = child.find(f"{W}sdtContent")
            if content is not None:
                blocks.extend(_docx_blocks(content))
    return blocks


def _docx(data: bytes) -> str:
    """A Word document as Markdown: headings by outline level or style name,
    numbered and bulleted paragraphs as list items, tables as Markdown
    tables, the rest as paragraphs."""
    import io
    import zipfile
    from xml.etree import ElementTree

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except KeyError as exc:  # a zip, but not a Word one
        raise ValueError("not a Word document (no word/document.xml)") from exc
    except zipfile.BadZipFile as exc:  # .doc and .rtf land here: the office
        raise ValueError(  # extractor converts those first
            "not a Word document (not a zip: an old .doc or .rtf goes"
            " through the office extractor)"
        ) from exc
    body = ElementTree.fromstring(xml).find(f"{W}body")
    if body is None:
        return ""
    return _join_blocks(_docx_blocks(body))


def _join_blocks(blocks: list[tuple[str, str]]) -> str:
    """Blocks as Markdown: a blank line between them, list items adjacent."""
    lines: list[str] = []
    for i, (kind, text) in enumerate(blocks):
        if i and not (kind == "li" and blocks[i - 1][0] == "li"):
            lines.append("")
        lines.append(text)
    return "\n".join(lines).strip()


# ---- OpenDocument text: the same idea as .docx, other namespaces
_ODT_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODT_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODT_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"


def _odt_runs(node: Any) -> str:
    """The text of one paragraph or heading: spans flattened, ``text:s``
    (n spaces), tabs and line breaks kept. Whitespace inside a text node
    collapses to one space, as ODF reads it; the explicit elements are the
    only way to say more."""
    parts: list[str] = []
    if node.text:
        parts.append(re.sub(r"\s+", " ", node.text))
    for child in node:
        tag = child.tag
        if tag == f"{_ODT_TEXT}s":
            parts.append(" " * int(child.get(f"{_ODT_TEXT}c", "1") or 1))
        elif tag == f"{_ODT_TEXT}tab":
            parts.append("\t")
        elif tag == f"{_ODT_TEXT}line-break":
            parts.append("\n")
        elif tag == f"{_ODT_TEXT}note":  # a footnote: its body, not its number
            body = child.find(f"{_ODT_TEXT}note-body")
            if body is not None:
                parts.append(" (" + " ".join(_odt_runs(p) for p in body) + ")")
        else:
            parts.append(_odt_runs(child))
        if child.tail:
            parts.append(re.sub(r"\s+", " ", child.tail))
    return "".join(parts).strip()


def _odt_blocks(parent: Any) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for child in parent:
        tag = child.tag
        if tag == f"{_ODT_TEXT}h":
            text = _odt_runs(child)
            if text:
                level = min(int(child.get(f"{_ODT_TEXT}outline-level", "1") or 1), 6)
                blocks.append(("h", "#" * level + " " + text))
        elif tag == f"{_ODT_TEXT}p":
            text = _odt_runs(child)
            if text:
                blocks.append(("p", text))
        elif tag == f"{_ODT_TEXT}list":
            for item in child.iter(f"{_ODT_TEXT}list-item"):
                text = " ".join(
                    t
                    for t in (_odt_runs(p) for p in item.findall(f"{_ODT_TEXT}p"))
                    if t
                )
                if text:
                    blocks.append(("li", "- " + text))
        elif tag == f"{_ODT_TABLE}table":
            rows: list[list[str]] = []
            for row in child.iter(f"{_ODT_TABLE}table-row"):
                cells = [
                    " ".join(_odt_runs(p) for p in cell.iter(f"{_ODT_TEXT}p")).replace(
                        "|", r"\|"
                    )
                    for cell in row.findall(f"{_ODT_TABLE}table-cell")
                ]
                if any(cells):
                    rows.append(cells)
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                head, *rest = rows
                table = [
                    "| " + " | ".join(head) + " |",
                    "|" + "|".join(["---"] * width) + "|",
                ]
                table += ["| " + " | ".join(r) + " |" for r in rest]
                blocks.append(("table", "\n".join(table)))
        elif tag in (f"{_ODT_TEXT}section", f"{_ODT_OFFICE}text"):
            blocks.extend(_odt_blocks(child))
    return blocks


def _odt(data: bytes) -> str:
    """An OpenDocument text as Markdown: headings by outline level, lists,
    tables, paragraphs; ``content.xml`` in the zip, nothing installed."""
    import io
    import zipfile
    from xml.etree import ElementTree

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("content.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ValueError("not an OpenDocument text (no content.xml)") from exc
    body = ElementTree.fromstring(xml).find(f"{_ODT_OFFICE}body")
    if body is None:
        return ""
    return _join_blocks(_odt_blocks(body))


def _rtf(data: bytes) -> str:
    """Rich Text as plain text: striprtf reads the control words, keeps the
    words; RTF carries little structure worth a heading."""
    from striprtf.striprtf import rtf_to_text

    text = data.decode("cp1252", "replace")
    if not text.lstrip().startswith("{\\rtf"):
        raise ValueError("not an RTF file")
    out = rtf_to_text(text, errors="ignore")
    return re.sub(r"\n{3,}", "\n\n", out).strip()


@functools.cache
def soffice_path() -> str | None:
    """LibreOffice's binary, when this machine has one."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for guess in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if Path(guess).exists():
            return guess
    return None


def _office(data: bytes, *, filename: str | None = None) -> str:
    """An old binary .doc: LibreOffice converts it to .docx in a scratch
    directory with a profile of its own (so it never clashes with the
    person's running LibreOffice) and ``_docx`` reads that. Its output goes
    to a file, not a pipe: soffice.exe hands the work to a child that keeps
    a pipe open past the conversion, and a pipe would make the wait outlive
    the work."""
    import subprocess
    import tempfile

    soffice = soffice_path()
    if soffice is None:
        raise RuntimeError(
            "LibreOffice is not installed: it is what converts an old .doc"
            " (libreoffice.org; prax only calls it)"
        )
    suffix = Path(filename or "").suffix.lower() or ".doc"
    with tempfile.TemporaryDirectory(prefix="prax-office-") as tmp:
        work = Path(tmp)
        source = work / f"input{suffix}"
        source.write_bytes(data)
        said_path = work / "soffice.log"
        with said_path.open("wb") as said_file:
            subprocess.run(
                [
                    soffice,
                    f"-env:UserInstallation=file:///{(work / 'profile').as_posix()}",
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    str(work),
                    str(source),
                ],
                stdin=subprocess.DEVNULL,
                stdout=said_file,
                stderr=subprocess.STDOUT,
                timeout=OFFICE_TIMEOUT,
                check=False,
            )
        converted = work / "input.docx"
        if not converted.exists():
            said = said_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(
                f"LibreOffice did not convert the file: {said.strip()[:200]}"
            )
        return _docx(converted.read_bytes())


def guess_mime(name: str | None, fallback: str = "application/octet-stream") -> str:
    """The MIME type of a file name. Python's table is the machine's table
    and misses the office types on some of them, so those are named here."""
    suffix = Path(name or "").suffix.lower()
    if suffix in EXTRA_MIME_TYPES:
        return EXTRA_MIME_TYPES[suffix]
    return mimetypes.guess_type(name or "")[0] or fallback


EXTRA_MIME_TYPES = {
    ".docx": DOCX_MIME,
    ".doc": "application/msword",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".md": "text/markdown",
    ".epub": "application/epub+zip",
}


REGISTRY: list[Extractor] = [
    Extractor(
        "pymupdf4llm",
        ("application/pdf",),
        _pymupdf4llm,
        "pymupdf4llm",
        revision=2,  # figure references
    ),
    Extractor("pymupdf", ("application/pdf",), _pymupdf, "pymupdf"),
    Extractor(
        "pymupdf4llm-ocr",
        ("application/pdf",),
        _pymupdf4llm_ocr,
        "pymupdf4llm",
        explicit_only=True,
        variant=_ocr_variant,  # the language is in the stamp
    ),
    Extractor("docling", ("application/pdf",), _docling, "docling", explicit_only=True),
    Extractor(
        "marker",
        ("application/pdf",),
        _marker,
        explicit_only=True,
        check=_marker_up,  # its server, not a package of this process
        version_of=_marker_version,
        variant=_marker_variant,  # +balanced when the vision model lays out too
    ),
    Extractor(
        "vision-pages",
        ("application/pdf",),
        _vision_pages,
        "pymupdf",
        explicit_only=True,
        variant=_vision_pages_variant,  # the model, and "all" when every page is read
    ),
    Extractor(
        "trafilatura",
        ("text/html", "application/xhtml+xml"),
        _trafilatura,
        "trafilatura",
        revision=4,  # r2 Markdown with fenced code; r3 figure references; r4 comments
        variant=_trafilatura_variant,  # +nocomments when parse.comments is off
    ),
    Extractor(
        "figures",
        ("text/html", "application/xhtml+xml", "application/pdf"),
        _figures,
        explicit_only=True,
        hints=True,
        revision=2,  # r2 the reading is asked for with the text around the figure
        variant=_figures_model,
        previous=True,  # writes into the current text
        annotates=True,
    ),
    Extractor(
        "figure-refs",
        ("text/html", "application/xhtml+xml", "application/pdf"),
        _figure_refs,
        explicit_only=True,
        hints=True,
        previous=True,
        annotates=True,
        covers=(("pymupdf4llm", 2), ("trafilatura", 3)),  # the figure references
    ),
    Extractor("docx", (DOCX_MIME,), _docx),
    Extractor("odt", ("application/vnd.oasis.opendocument.text",), _odt),
    Extractor("rtf", ("application/rtf", "text/rtf"), _rtf, "striprtf"),
    Extractor(
        "office",
        ("application/msword",),
        _office,
        hints=True,
        check=lambda: soffice_path() is not None,
    ),
    Extractor("plain", ("text/",), _plain, revision=3, hints=True),
    Extractor(
        "vision",
        ("image/",),
        _vision,
        explicit_only=True,
        hints=True,
        variant=_vision_model,
        previous=True,  # a second model's reading joins the first, never replaces it
    ),
    Extractor(
        "claude-vision",
        ("image/",),
        _claude_vision,
        "anthropic",
        explicit_only=True,
        hints=True,
    ),
]


_STAMP = re.compile(
    r"^(?P<name>[^/]+)/(?P<pkg>[^+\-]*)(?:-r(?P<rev>\d+))?(?:\+(?P<variant>.*))?$"
)


def stamp_parts(stamp: str) -> tuple[str, int] | None:
    """``(extractor name, revision)`` of a text-source stamp such as
    ``pymupdf4llm/1.28.2-r2+arabic``; None for a source that is no
    extractor's (``zotero-ft-cache``)."""
    m = _STAMP.match(stamp or "")
    if not m:
        return None
    return m.group("name"), int(m.group("rev") or 1)


def covered(stamp: str, by: Extractor) -> str | None:
    """The stamp a text is at after ``by`` annotated it: the same name,
    package version and variant, the revision raised to what ``by``
    covers for that extractor. None when ``by`` covers nothing of it or
    the stamp is already there."""
    m = _STAMP.match(stamp or "")
    if not m:
        return None
    revision = int(m.group("rev") or 1)
    to = dict(by.covers).get(m.group("name"))
    if to is None or to <= revision:
        return None
    variant = f"+{m.group('variant')}" if m.group("variant") else ""
    return f"{m.group('name')}/{m.group('pkg')}-r{to}{variant}"


def behind(stamp: str) -> Extractor | None:
    """The extractor that would read this document again differently: the
    one the stamp names, when prax's own revision of it has moved on since
    (the package's version is not the question — a pip upgrade changes
    nothing prax decided). Explicit-only extractors are never behind: what
    was asked for once is not asked for again by itself."""
    parts = stamp_parts(stamp)
    if parts is None:
        return None
    name, revision = parts
    for e in REGISTRY:
        if e.name == name:
            return e if (not e.explicit_only and e.revision != revision) else None
    return None


def by_name(name: str) -> Extractor:
    for e in REGISTRY:
        if e.name == name:
            return e
    raise KeyError(f"no extractor named {name!r}; known: {[e.name for e in REGISTRY]}")


def candidates(mime: str, preferred: str | None = None) -> list[Extractor]:
    """Installed extractors for ``mime`` in the order to try them.

    With ``preferred`` the list is that extractor alone (if it fits the
    type), so an explicit choice never silently falls back to another one.
    """
    if preferred is not None:
        e = by_name(preferred)
        return [e] if e.accepts(mime) and e.available() else []
    return [
        e for e in REGISTRY if e.accepts(mime) and e.available() and not e.explicit_only
    ]


def for_mime(mime: str, preferred: str | None = None) -> Extractor | None:
    """The first extractor ``candidates`` would try, or None."""
    found = candidates(mime, preferred)
    return found[0] if found else None


def names() -> list[str]:
    return [e.name for e in REGISTRY]
