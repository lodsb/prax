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
| plain           | text/*          | decode as UTF-8; a source file (by extension, |
|                 |                 | else Magika) becomes one fenced code block    |
| claude-vision   | image/*         | Claude describes the image and transcribes    |
|                 |                 | its text, handwriting included; explicit only |

Adding one: write a function ``bytes -> str``, wrap it in ``Extractor`` and
append it to ``REGISTRY``. Order within a MIME type is the preference and
fallback order; ``explicit_only`` extractors are used only when named. An
extractor whose output changes without a package release bumps its
``revision`` so the queue's ``--upgrade`` re-selects what it wrote.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Extractor:
    name: str
    mimes: tuple[str, ...]  # exact types, or a prefix ending in "/"
    fn: Callable[..., str]
    package: str | None = None  # distribution whose version is stamped
    explicit_only: bool = False  # never chosen by default or as a fallback
    revision: int = 1  # our own changes to what the extractor produces
    hints: bool = False  # the function takes filename= as a keyword

    def accepts(self, mime: str) -> bool:
        return any(
            mime == m or (m.endswith("/") and mime.startswith(m)) for m in self.mimes
        )

    def available(self) -> bool:
        if self.package is None:
            return True
        try:
            importlib.import_module(self.package)
        except ImportError:
            return False
        return True

    @property
    def version(self) -> str:
        if self.package is None:
            base = "1"
        else:
            try:
                base = importlib.metadata.version(self.package)
            except importlib.metadata.PackageNotFoundError:
                base = "?"
        return base if self.revision == 1 else f"{base}-r{self.revision}"

    @property
    def stamp(self) -> str:
        """What ``meta.text_source`` records: ``"<name>/<version>"``."""
        return f"{self.name}/{self.version}"

    def __call__(self, data: bytes, *, filename: str | None = None) -> str:
        if self.hints:
            return self.fn(data, filename=filename)
        return self.fn(data)


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
    limit_mb = float(os.environ.get("PRAX_MAX_LAYOUT_MB", "40"))
    if len(data) > limit_mb * 1e6:
        raise ExtractionError(
            f"{len(data) / 1e6:.0f} MB exceeds PRAX_MAX_LAYOUT_MB={limit_mb:g};"
            " plain extraction instead"
        )
    pymupdf4llm = importlib.import_module("pymupdf4llm")
    max_pages = int(os.environ.get("PRAX_MAX_LAYOUT_PAGES", "400"))
    with _pymupdf_open(data) as doc:
        if not _has_text_layer(doc):
            raise ExtractionError("no text layer in the first pages; needs OCR")
        if doc.page_count > max_pages:
            raise ExtractionError(
                f"{doc.page_count} pages exceeds PRAX_MAX_LAYOUT_PAGES={max_pages};"
                " plain extraction instead"
            )
        return pymupdf4llm.to_markdown(doc, use_ocr=False, page_separators=True)


def _pymupdf4llm_ocr(data: bytes) -> str:
    """Markdown with RapidOCR on pages that have no text layer.

    OCR costs seconds per page on a CPU, so documents above
    ``PRAX_OCR_MAX_PAGES`` (default 60) are refused with ``ExtractionError``
    and left for a deliberate run with a higher budget.
    """
    pymupdf4llm = importlib.import_module("pymupdf4llm")
    budget = int(os.environ.get("PRAX_OCR_MAX_PAGES", "60"))
    with _pymupdf_open(data) as doc:
        if doc.page_count > budget:
            raise ExtractionError(
                f"{doc.page_count} pages exceeds the OCR budget of {budget}"
                " (PRAX_OCR_MAX_PAGES)"
            )
        return pymupdf4llm.to_markdown(doc, use_ocr=True, page_separators=True)


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
    return f"# {title}\n\n{text}" if title and title not in text[:200] else text


def _claude_vision(data: bytes, *, filename: str | None = None) -> str:
    from prax.parsers import vision

    return vision.describe(data, filename=filename)


def _plain(data: bytes, *, filename: str | None = None) -> str:
    text = data.decode("utf-8", errors="replace")
    lang = code_language(text, filename)
    if lang is None or text.lstrip().startswith("```"):
        return text
    return f"```{lang}\n{text.strip()}\n```"


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


REGISTRY: list[Extractor] = [
    Extractor("pymupdf4llm", ("application/pdf",), _pymupdf4llm, "pymupdf4llm"),
    Extractor("pymupdf", ("application/pdf",), _pymupdf, "pymupdf"),
    Extractor(
        "pymupdf4llm-ocr",
        ("application/pdf",),
        _pymupdf4llm_ocr,
        "pymupdf4llm",
        explicit_only=True,
    ),
    Extractor("docling", ("application/pdf",), _docling, "docling", explicit_only=True),
    Extractor(
        "trafilatura",
        ("text/html", "application/xhtml+xml"),
        _trafilatura,
        "trafilatura",
        revision=2,  # Markdown output with fenced code blocks
    ),
    Extractor("plain", ("text/",), _plain, revision=2, hints=True),
    Extractor(
        "claude-vision",
        ("image/",),
        _claude_vision,
        "anthropic",
        explicit_only=True,
        hints=True,
    ),
]


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
