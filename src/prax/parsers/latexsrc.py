"""A paper's text from its LaTeX source on arXiv: the ``latex-source``
reading, the worker's half of what ``prax import latex`` does by hand.

A PDF's text layer loses the mathematics (MuPDF reads a display equation
as a scatter of symbols); the source says it exactly, without a model or
the card. The worker fetches the paper's e-print from arXiv, unpacks it
beside the PDF, flattens and converts it with pandoc
(``prax.importers.latex``) and hands the text back stamped
``latex-source/<pandoc>-r<revision>``, the stamp the importer writes, so
a text from either is the same to the rest of prax.

The paper is named by the reading's mode (``PRAX_ARXIV_ID``, the
document's ``meta.arxiv`` as the door asks for it) or else by the stamp
arXiv prints in the margin of its PDFs' first page. A paper with no
source on arXiv (a PDF-only submission), a source pandoc cannot convert,
or one that comes out a fraction of the PDF's text is an
``ExtractionError``: the door then asks for marker instead
(``pipeline.follow_ups``).
"""

from __future__ import annotations

import functools
import gzip
import io
import re
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from prax import config

from .base import ExtractionError
from .pdf import _pymupdf_open

# the readings a client does on its side as well, with the same output:
# ``prax import latex`` converts a paper's source as this reading does, so
# a text a sender stamps so is its own, kept against an unasked parse
SENDERS_TOO = frozenset({"latex-source"})

E_PRINT = "https://arxiv.org/e-print/{}"
AGENT = "prax latex-source reading (a personal library)"
FETCH_SECONDS = 120
MAX_BYTES = 200 * 1024 * 1024  # an e-print past this is a dataset, not a paper
# a text from the source this much shorter than the PDF's is a conversion
# that lost the paper (pandoc stopped at a macro it could not read; one
# such came back as a single character, 2026-10-17)
LEAST_SHARE = 0.3

# a new-style id (2501.13484, 1308.3432v2) or an old one (math.GT/0309136)
ARXIV_ID = re.compile(
    r"(?<![\w.])(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?(?![\w])"
)
_STAMP = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")


def _named() -> str | None:
    """The id the reading was asked with (its mode), if any."""
    value = str(config.setting("parse.arxiv_id", "PRAX_ARXIV_ID", "") or "").strip()
    m = ARXIV_ID.fullmatch(value) if value else None
    return m.group(1) if m else None


def arxiv_id_of(data: bytes) -> str | None:
    """The arXiv id a PDF prints in its first page's margin, if any."""
    try:
        with _pymupdf_open(data) as doc:
            if not doc.page_count:
                return None
            text = doc[0].get_text()
    except Exception:  # noqa: BLE001 - a PDF MuPDF cannot open has no stamp to read
        return None
    m = _STAMP.search(text)
    return m.group(1) if m else None


def _fetch(arxiv: str) -> bytes:
    req = urllib.request.Request(E_PRINT.format(arxiv), headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_SECONDS) as r:
            blob = bytes(r.read(MAX_BYTES + 1))
            kind = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise ExtractionError(f"arXiv has no e-print for {arxiv}: {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ExtractionError(f"arXiv: {exc}") from exc
    if len(blob) > MAX_BYTES:
        raise ExtractionError(f"the e-print of {arxiv} is over {MAX_BYTES >> 20} MB")
    if blob[:4] == b"%PDF" or "pdf" in kind:
        raise ExtractionError(f"{arxiv} has no source on arXiv, only a PDF")
    return blob


def unpack(blob: bytes, into: Path) -> None:
    """An e-print into a folder: a (gzipped) tar of the source, or one
    gzipped .tex. Nothing is written outside ``into``."""
    raw = gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            tar.extractall(into, filter="data")
    except tarfile.ReadError:
        (into / "main.tex").write_bytes(raw)


def _main(folder: Path) -> Path | None:
    from prax.importers import latex

    found = latex.main_tex(folder)
    if found is not None:
        return found
    for sub in sorted(p for p in folder.rglob("*") if p.is_dir()):
        found = latex.main_tex(sub)
        if found is not None:
            return found
    return None


@functools.cache
def pandoc_stamp() -> str:
    """The version part of the stamp: pandoc's version and the conversion's
    revision (``3.9-r2``), ``?`` without pandoc."""
    from prax.importers import latex

    binary = latex.pandoc()
    if binary is None:
        return "?"
    return f"{latex.pandoc_version(binary)}-r{latex.REVISION}"


def available() -> bool:
    from prax.importers import latex

    return latex.pandoc() is not None


def _latex_source(data: bytes, *, previous: str | None = None) -> str:
    """The paper's text from its arXiv source (the module's docstring)."""
    from prax.importers import latex

    arxiv = _named() or arxiv_id_of(data)
    if arxiv is None:
        raise ExtractionError(
            "no arXiv id: the reading names none and the PDF prints none"
        )
    binary = latex.pandoc()
    if binary is None:
        raise ExtractionError("pandoc is needed (pip install prax[latex])")
    blob = _fetch(arxiv)
    with tempfile.TemporaryDirectory(prefix="prax-latex-") as tmp:
        folder = Path(tmp)
        unpack(blob, folder)
        main = _main(folder)
        if main is None:
            raise ExtractionError(f"the source of {arxiv} has no \\begin{{document}}")
        pdf = main.parent / "_prax_original.pdf"
        pdf.write_bytes(data)
        ms = latex.Manuscript(main.parent, pdf, main, arxiv, None, ())
        try:
            text = latex.convert(ms, binary)
        except Exception as exc:
            raise ExtractionError(
                f"pandoc could not convert {arxiv}: {exc}"[:400]
            ) from exc
    if previous and len(text.strip()) < LEAST_SHARE * len(previous.strip()):
        raise ExtractionError(
            f"the source of {arxiv} converted to {len(text.strip())} characters,"
            f" against {len(previous.strip())} in the PDF's text"
        )
    if not text.strip():
        raise ExtractionError(f"the source of {arxiv} converted to nothing")
    return text
