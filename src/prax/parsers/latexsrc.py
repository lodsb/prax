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
UNPACKED_BYTES = 500 * 1024 * 1024  # and what it unpacks to
MAX_MEMBERS = 10_000  # files in its archive
# a text from the source this much shorter than the PDF's is a conversion
# that lost the paper (pandoc stopped at a macro it could not read; one
# such came back as a single character, 2026-10-17)
LEAST_SHARE = 0.3

# a new-style id (2501.13484, 1308.3432v2) or an old one (math.GT/0309136)
ARXIV_ID = re.compile(
    r"(?<![\w.])(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?(?![\w])"
)
# the stamp arXiv prints in the margin, with its class and date
# ("arXiv:2501.13484v2  [cs.CL]  23 Jan 2025"): a bare "arXiv:NNNN" on the
# first page is as often a preprint the paper cites (the review of
# 2026-10-18)
_STAMP = re.compile(
    r"arXiv:\s*(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?"
    r"\s+\[[\w.-]+\]\s+\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}"
)
_BARE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")
# the share of the source's long words the PDF's text must hold for the
# two to be one paper: 0.83-0.99 for 62 of the library's papers read both
# ways, 0.20-0.71 for each against another (2026-10-18)
SAME_PAPER = 0.75
_LONG_WORD = re.compile(r"[a-zA-Z]{7,}")


def arxiv_of(value: object) -> str | None:
    """An arXiv id as the reading's mode takes it, without its version
    (``2501.13484v2`` is ``2501.13484``), or None for anything else."""
    m = ARXIV_ID.fullmatch(str(value or "").strip())
    return m.group(1) if m else None


def _named() -> str | None:
    """The id the reading was asked with (its mode), if any."""
    return arxiv_of(config.setting("parse.arxiv_id", "PRAX_ARXIV_ID", ""))


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
    if m:
        return m.group(1)
    # a journal's print names the preprint without arXiv's stamp: taken
    # when it is the only one on the page, and the converted text must
    # then be the paper (``same_paper``) before it is kept
    bare = {b.group(1) for b in _BARE.finditer(text)}
    return bare.pop() if len(bare) == 1 else None


def same_paper(source: str, pdf_text: str) -> bool:
    """True when a text converted from a source is the paper the PDF's text
    is: ``SAME_PAPER`` of the long words of its first 20,000 characters are
    in the PDF's text. A wrong id fetches another paper whole, of a length
    the share of characters cannot tell from the right one."""
    words = {w.lower() for w in _LONG_WORD.findall(source[:20_000])}
    if not words:
        return True  # nothing to judge by; the length check still holds
    held = {w.lower() for w in _LONG_WORD.findall(pdf_text)}
    return len(words & held) >= SAME_PAPER * len(words)


def _layer_text(data: bytes) -> str:
    """The PDF's own text layer, to judge a source by when the document
    has no text yet; empty for a scan or a PDF MuPDF cannot open."""
    try:
        with _pymupdf_open(data) as doc:
            return "".join(page.get_text() for page in doc)
    except Exception:  # noqa: BLE001 - nothing to judge by, as for a scan
        return ""


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


def _gunzip(blob: bytes) -> bytes:
    """A gzipped e-print, decompressed up to ``UNPACKED_BYTES``: a few
    kilobytes can stand for gigabytes (the review of 2026-10-18)."""
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as fh:
        raw = fh.read(UNPACKED_BYTES + 1)
    if len(raw) > UNPACKED_BYTES:
        raise ExtractionError(f"the e-print unpacks to over {UNPACKED_BYTES >> 20} MB")
    return raw


def unpack(blob: bytes, into: Path) -> None:
    """An e-print into a folder: a (gzipped) tar of the source, or one
    gzipped .tex. Nothing is written outside ``into`` (tarfile's ``data``
    filter), and no more than ``UNPACKED_BYTES`` in ``MAX_MEMBERS`` files."""
    raw = _gunzip(blob) if blob[:2] == b"\x1f\x8b" else blob
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            members = tar.getmembers()
            if len(members) > MAX_MEMBERS:
                raise ExtractionError(f"the e-print holds over {MAX_MEMBERS} files")
            if sum(m.size for m in members) > UNPACKED_BYTES:
                raise ExtractionError(
                    f"the e-print unpacks to over {UNPACKED_BYTES >> 20} MB"
                )
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
    against = previous or _layer_text(data)
    if against and not same_paper(text, against):
        raise ExtractionError(
            f"the source of {arxiv} is not the paper of the PDF's text"
            f" (under {SAME_PAPER:.0%} of its words in it)"
        )
    if previous and len(text.strip()) < LEAST_SHARE * len(previous.strip()):
        raise ExtractionError(
            f"the source of {arxiv} converted to {len(text.strip())} characters,"
            f" against {len(previous.strip())} in the PDF's text"
        )
    if not text.strip():
        raise ExtractionError(f"the source of {arxiv} converted to nothing")
    return text
