"""Pluggable text extractors, keyed by name and selected by MIME type.

An extractor turns the archived original bytes of a document into text.
The queue (``prax.parsers.queue``) records which one produced the current
text in ``documents.meta.text_source`` as ``"<name>/<version>"`` so a later,
better extractor can find and upgrade what an earlier one wrote (chunks are
disposable, rationale R3).

Registered extractors (heavy imports happen on first use, never at import
time, so the serving path never loads them):

| name          | MIME              | what                                       |
|---------------|-------------------|--------------------------------------------|
| pymupdf4llm   | application/pdf   | MuPDF, Markdown with headings and tables   |
| pymupdf       | application/pdf   | MuPDF plain text; 40x faster, no structure |
| docling       | application/pdf   | IBM Docling layout + table models; slow    |
| trafilatura   | text/html         | article text, boilerplate stripped         |
| plain         | text/*            | decode as UTF-8                            |

Adding one: write a function ``bytes -> str``, wrap it in ``Extractor`` and
append it to ``REGISTRY``. Order within a MIME type is the preference order.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Extractor:
    name: str
    mimes: tuple[str, ...]  # exact types, or a prefix ending in "/"
    fn: Callable[[bytes], str]
    package: str | None = None  # distribution whose version is stamped

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
            return "1"
        try:
            return importlib.metadata.version(self.package)
        except importlib.metadata.PackageNotFoundError:
            return "?"

    @property
    def stamp(self) -> str:
        """What ``meta.text_source`` records: ``"<name>/<version>"``."""
        return f"{self.name}/{self.version}"

    def __call__(self, data: bytes) -> str:
        return self.fn(data)


class ExtractionError(RuntimeError):
    """The extractor ran but produced nothing usable."""


# ---------------------------------------------------------------- backends


def _pymupdf_open(data: bytes) -> Any:  # a pymupdf.Document, imported lazily
    pymupdf = importlib.import_module("pymupdf")
    return pymupdf.open(stream=data, filetype="pdf")


def _pymupdf4llm(data: bytes) -> str:
    pymupdf4llm = importlib.import_module("pymupdf4llm")
    with _pymupdf_open(data) as doc:
        return pymupdf4llm.to_markdown(doc)


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
        data, include_tables=True, include_links=False, include_comments=False
    )
    if not text:
        raise ExtractionError("trafilatura found no main content")
    meta = trafilatura.extract_metadata(data)
    title = getattr(meta, "title", None) if meta else None
    return f"# {title}\n\n{text}" if title and title not in text[:200] else text


def _plain(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


REGISTRY: list[Extractor] = [
    Extractor("pymupdf4llm", ("application/pdf",), _pymupdf4llm, "pymupdf4llm"),
    Extractor("pymupdf", ("application/pdf",), _pymupdf, "pymupdf"),
    Extractor("docling", ("application/pdf",), _docling, "docling"),
    Extractor(
        "trafilatura",
        ("text/html", "application/xhtml+xml"),
        _trafilatura,
        "trafilatura",
    ),
    Extractor("plain", ("text/",), _plain),
]


def by_name(name: str) -> Extractor:
    for e in REGISTRY:
        if e.name == name:
            return e
    raise KeyError(f"no extractor named {name!r}; known: {[e.name for e in REGISTRY]}")


def for_mime(mime: str, preferred: str | None = None) -> Extractor | None:
    """The extractor to use for ``mime``: ``preferred`` if it fits and is
    installed, else the first installed one registered for the type."""
    if preferred is not None:
        e = by_name(preferred)
        return e if e.accepts(mime) and e.available() else None
    for e in REGISTRY:
        if e.accepts(mime) and e.available():
            return e
    return None


def names() -> list[str]:
    return [e.name for e in REGISTRY]
