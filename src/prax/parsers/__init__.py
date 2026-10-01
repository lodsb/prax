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
|                 |                 | explicit only, PRAX_OCR_MAX_PAGES pages a pass|
|                 |                 | (a longer scan in windows)                    |
| docling         | application/pdf | IBM Docling layout + table models; explicit   |
|                 |                 | only, seconds per page                        |
| trafilatura     | text/html       | article Markdown, boilerplate stripped, code  |
|                 |                 | blocks fenced                                 |
| video           | text/html       | a video capture of the extension's: transcript|
|                 |                 | with time marks, frames as figures, chapters  |
|                 |                 | as headings (named by the document, meta.parser)|
| polish          | text/html       | an automatic transcript punctuated by the     |
|                 |                 | polish step's model, the fillers dropped      |
| docx            | .docx           | Word: zip of XML read here, headings, lists,  |
|                 |                 | tables; no dependency                         |
| office          | .doc .rtf .odt  | LibreOffice converts to .docx, then as above; |
|                 |                 | needs LibreOffice installed                   |
| plain           | text/* but HTML | decode as UTF-8; a source file (by extension, |
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

The extractors live in parts by what they read (``base``, ``code``,
``pdf``, ``marker``, ``web``, ``readings``, ``office``, ``djvu``), each
importing only the ones before it; this module holds the registry and
re-exports every name, so a caller writes ``parsers.<name>``. Adding one:
write a function ``bytes -> str`` in its part, wrap it in ``Extractor``
and append it to ``REGISTRY``. Order within a MIME type is the preference and
fallback order; ``explicit_only`` extractors are used only when named. An
extractor whose output changes without a package release bumps its
``revision`` so the queue's ``--upgrade`` re-selects what it wrote.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import mimetypes
import re
from pathlib import Path

from prax import config, packs
from prax.parsers import figures  # noqa: F401 - parsers.figures is used
from prax.text import mimes

# the parts, each importing only from the ones before it (the readings'
# own modules, figures, formulas, polish, vision, video and the queue,
# stand beside them)
ORDER = ("base", "code", "pdf", "marker", "web", "readings", "office", "djvu")

from . import djvu, office
from .base import (  # noqa: F401
    EMPTY_PAGE,
    ExtractionError,
    Extractor,
    NotYet,
    Partial,
    empty_pages,
    join_pages,
    pages_by_mark,
)
from .code import (  # noqa: F401
    _CODE_LINE_END,
    _CODE_LINE_START,
    _CODE_OPERATORS,
    _MAGIKA,
    _PROSE_END,
    _WORD,
    CODE_EXTENSIONS,
    CODE_MIN_LINES,
    MAGIKA_DATA_LABELS,
    MAGIKA_MIN_SCORE,
    MAGIKA_SAMPLE,
    _magika,
    _plain,
    code_language,
    code_line_score,
    code_regions,
    fence_code_regions,
)
from .djvu import (  # noqa: F401
    DJVU_OCR_SCALE,
    _djvu,
    _djvu_ocr,
    _djvu_pages,
    _djvu_version,
    djvu_tool,
)
from .marker import (  # noqa: F401
    _MARKER_CAPTION,
    _MARKER_IMAGE,
    _MARKER_IMAGE_REF,
    _MARKER_PAGE,
    _MARKER_PROBE_SECONDS,
    MARKER_URL,
    _marker,
    _marker_mode,
    _marker_pages,
    _marker_probes,
    _marker_scan_pictures,
    _marker_up,
    _marker_url,
    _marker_variant,
    _marker_version,
)
from .office import (  # noqa: F401
    _HEADING,
    _ODT_OFFICE,
    _ODT_TABLE,
    _ODT_TEXT,
    DOCX_MIME,
    OFFICE_TIMEOUT,
    ZIPPED_XML_MAX,
    W,
    _docx,
    _docx_blocks,
    _docx_heading,
    _docx_runs,
    _docx_table,
    _join_blocks,
    _odt,
    _odt_blocks,
    _odt_runs,
    _office,
    _rtf,
    _zipped_xml,
    soffice_path,
)
from .pdf import (  # noqa: F401
    _OWN_OCR_SCRIPTS,
    _RIGHT_TO_LEFT,
    OCR_DEFAULT_LANGUAGE,
    PROBE_PAGES,
    PROBE_SPREAD,
    _docling,
    _has_text_layer,
    _ocr_engine,
    _ocr_gpu,
    _ocr_language,
    _ocr_model_version,
    _ocr_page,
    _ocr_rows,
    _ocr_variant,
    _pymupdf,
    _pymupdf4llm,
    _pymupdf4llm_ocr,
    _pymupdf_open,
    _vision_pages,
    _vision_pages_mode,
    _vision_pages_variant,
)
from .readings import (
    _claude_vision,
    _figure_crops,
    _figure_refs,
    _figures,
    _figures_model,
    _formulas,
    _formulas_model,
    _polish,
    _polish_model,
    _vision,
    _vision_model,
)
from .web import (  # noqa: F401
    COMMENTS_HEADING,
    _comments_wanted,
    _trafilatura,
    _trafilatura_variant,
    _video,
)


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
    ".djvu": mimes.DJVU,  # no table has it
    ".djv": mimes.DJVU,
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
        previous=True,  # a long scan in windows: the pages read before stay
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
        "video",
        ("text/html", "application/xhtml+xml"),
        _video,
        explicit_only=True,  # chosen by the document that names it (meta.parser)
        check=lambda: importlib.util.find_spec("lxml") is not None,
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
        "formulas",
        ("text/", "application/xhtml+xml", "application/pdf"),  # a note too
        _formulas,
        explicit_only=True,
        hints=True,
        variant=_formulas_model,
        previous=True,  # writes into the current text
        annotates=True,
    ),
    Extractor(
        "polish",
        ("text/html", "application/xhtml+xml"),
        _polish,
        explicit_only=True,
        hints=True,
        variant=_polish_model,  # which model wrote the sentences
        previous=True,  # works on the current text and replaces it
    ),
    Extractor(
        "figure-crops",
        ("application/pdf",),
        _figure_crops,
        explicit_only=True,
        hints=True,
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
    Extractor(
        "rtf",
        ("application/rtf", "text/rtf"),
        _rtf,
        "striprtf",
        revision=2,  # r2 a Mac's CJK, Hebrew and Thai character sets read
    ),
    Extractor(
        "office",
        ("application/msword",),
        _office,
        hints=True,
        check=lambda: office.soffice_path() is not None,
    ),
    Extractor(
        "djvu",
        (mimes.DJVU, "image/x-djvu"),
        _djvu,
        check=lambda: djvu.djvu_tool("djvutxt") is not None,
        version_of=_djvu_version,
        previous=True,  # a long scan in windows: the pages read before stay
    ),
    Extractor(
        "plain",
        ("text/",),
        _plain,
        revision=3,
        hints=True,
        # a page trafilatura finds nothing in is a failed parse, never its
        # markup as text (a bot check, a PDF viewer's frame: 2026-09-29)
        excludes=("text/html",),
    ),
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
# the extractors of the packs this host runs (``packs:``, docs/packs.md),
# after the core's: a process keeps the ones it started with
REGISTRY += packs.extractors(config.words("packs"))


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
    # the cheap tests first: an explicit-only extractor's availability may
    # be a probe of its server (marker), and the door's hand-out asks for
    # the chain of every waiting document — six probes of a paused server
    # made GET /work/parse a thirteen-second request, every twenty seconds
    return [
        e for e in REGISTRY if e.accepts(mime) and not e.explicit_only and e.available()
    ]


def for_mime(mime: str, preferred: str | None = None) -> Extractor | None:
    """The first extractor ``candidates`` would try, or None."""
    found = candidates(mime, preferred)
    return found[0] if found else None


def names() -> list[str]:
    return [e.name for e in REGISTRY]
