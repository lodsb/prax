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
|                 |                 | else Magika) becomes one fenced code block;   |
|                 |                 | code regions inside prose are fenced          |
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
import re
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
    Extractor("plain", ("text/",), _plain, revision=3, hints=True),
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
