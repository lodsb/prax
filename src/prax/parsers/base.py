"""What an extractor is, and the errors it may raise."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Self

from prax.text import markup


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
    # exact types a prefix in ``mimes`` would take and this one must not
    excludes: tuple[str, ...] = ()

    def accepts(self, mime: str) -> bool:
        if mime in self.excludes:
            return False
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


class NotYet(ExtractionError):
    """The extractor cannot run right now — the server it reads through is
    loading its model, or is not up — and the document is not at fault:
    the worker leaves the request waiting and says so, instead of
    recording an error against the document."""


# ------------------------------------------------------------ pages
# A scanned book is read in windows (stage Y): each pass keeps the pages
# that have text, OCRs the next ``parse.ocr_max_pages`` of the ones without,
# and says how many are still to go. The page marks in the text
# (``markup.page_break``, pymupdf4llm's own) are what say which is which.

EMPTY_PAGE = 20  # fewer characters than this and a page has no text worth it


class Partial(str):
    """A text read in part: the OCR reached some of the pages without a
    text layer, and ``pages_left`` of them wait for the next window. The
    door stores and indexes what there is and asks for the next."""

    pages_left: int
    pages: int

    def __new__(cls, text: str, *, pages_left: int, pages: int) -> Self:
        made = super().__new__(cls, text)
        made.pages_left = pages_left
        made.pages = pages
        return made


def pages_by_mark(text: str) -> dict[int, str]:
    """A text's pages by number: what stands before each page mark. A
    text without marks is one page, the first."""
    out: dict[int, str] = {}
    start = 0
    for m in markup.PAGE_MARK_ANY.finditer(text):
        out[int(m.group("page"))] = text[start : m.start()].strip()
        start = m.end()
    if not out and text.strip():
        out[1] = text.strip()
    return out


def join_pages(pages: list[str]) -> str:
    """Pages back into one text, each followed by its mark."""
    return "".join(f"{t.strip()}{markup.page_break(n)}" for n, t in enumerate(pages, 1))


def empty_pages(pages: list[str]) -> list[int]:
    """The indexes of the pages with no text worth the name."""
    return [i for i, t in enumerate(pages) if len(t.strip()) < EMPTY_PAGE]
