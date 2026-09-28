"""What an extractor is, and the errors it may raise."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
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


class NotYet(ExtractionError):
    """The extractor cannot run right now — the server it reads through is
    loading its model, or is not up — and the document is not at fault:
    the worker leaves the request waiting and says so, instead of
    recording an error against the document."""
