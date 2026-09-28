"""marker, the layout model that runs as a server of its own: asked
over HTTP, its pages and pictures put into prax's format.
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path

from prax import config
from prax.parsers import figures
from prax.text import markup

from .base import ExtractionError, NotYet
from .pdf import _pymupdf_open

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


_MARKER_PROBE_SECONDS = 5.0  # one probe of marker's server serves this long
_marker_probes: dict[str, tuple[float, bool]] = {}  # by url: (when, up)


def _marker_up() -> bool:
    """Whether marker's server answers; one probe per few seconds, since
    a refused connection costs two seconds on Windows and the extractor
    is asked several times a pass."""
    import time

    import httpx

    url = _marker_url()
    at, up = _marker_probes.get(url, (0.0, False))
    if time.monotonic() - at < _MARKER_PROBE_SECONDS:
        return up
    try:
        up = httpx.get(f"{url}/", timeout=3).status_code == 200
    except httpx.HTTPError:
        up = False
    _marker_probes[url] = (time.monotonic(), up)
    return up


def _marker_pages(text: str, count: int) -> str:
    """marker's ``{n}`` and a rule before each page (0-based) become the
    ``--- end of page.page_number=n ---`` lines (1-based, after the page)
    the chunker reads locators from."""

    def mark(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return "\n\n" if n == 0 else markup.page_break(n)

    out = _MARKER_PAGE.sub(mark, text).strip()
    if count:
        out += markup.page_break(count).rstrip() + "\n"
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

    if not _marker_up():  # before any work: the request waits for the server
        raise NotYet(
            f"marker's server at {_marker_url()} is not answering"
            " (prax up --start marker); the request waits"
        )
    window = max(1, config.whole("parse.layout_window", "PRAX_LAYOUT_WINDOW", 100))
    with _pymupdf_open(data) as doc:
        count = doc.page_count
        figs = figures.pdf_figures(doc)
        # the pages without a text layer: their pictures are marker's crops
        scanned = {p.number for p in doc if len(p.get_text().strip()) < 20}
    # a long document a window of pages at a time, as pymupdf4llm reads
    # it: marker's server converts the range asked for and numbers the
    # pages as the document does, so the parts join as one
    ranges = (
        [None]
        if count <= window
        else [(a, min(a + window, count) - 1) for a in range(0, count, window)]
    )
    parts = []
    images: dict[str, str] = {}
    for span in ranges:
        form = {
            "output_format": "markdown",
            "mode": _marker_mode(),
            "paginate_output": "true",
        }
        pages = count
        if span is not None:
            form["page_range"] = f"{span[0]}-{span[1]}"
            pages = span[1] - span[0] + 1
        try:
            r = httpx.post(
                f"{_marker_url()}/marker/upload",
                files={"file": ("document.pdf", data, "application/pdf")},
                data=form,
                timeout=httpx.Timeout(30.0, read=max(600.0, 20.0 * pages)),
            )
        except httpx.HTTPError as exc:
            raise ExtractionError(f"marker's server at {_marker_url()}: {exc}") from exc
        if r.status_code != 200:
            raise ExtractionError(f"marker's server answered {r.status_code}")
        body = r.json()
        if not body.get("success"):
            raise ExtractionError(f"marker failed: {str(body.get('error', ''))[:200]}")
        parts.append(str(body.get("output") or ""))
        images.update(body.get("images") or {})
    text = _marker_scan_pictures("\n\n".join(parts), images, scanned)
    text = _MARKER_IMAGE.sub("", text)
    text = _marker_pages(text, count)
    return figures.place(text, figs)


_MARKER_IMAGE_REF = re.compile(
    r"^!\[(?P<alt>[^\]\n]*)\]"
    r"\((?P<name>_page_(?P<page>\d+)_[A-Za-z]+_\d+\.\w+)\)[ \t]*$",
    re.MULTILINE,
)
_MARKER_CAPTION = re.compile(
    r"^\W*(fig\.?|figure|abb\.?|abbildung|plate)\s*\d+", re.IGNORECASE
)


def _marker_scan_pictures(text: str, images: dict[str, str], scanned: set[int]) -> str:
    """marker's own crops of the figures on the scanned pages, inlined as
    data URLs for the door to file (``figures.file_inline``): a scanned
    page holds no image object a figure could be served from, so the crop
    is the figure. Captioned ``Picture on page N``, with the "Figure N"
    line under it when there is one; the crops of printed pages are
    dropped as before (their images are found in the original)."""
    if not images or not scanned:
        return text
    lines = text.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        m = _MARKER_IMAGE_REF.match(line.strip())
        if not m or int(m.group("page")) not in scanned:
            out.append(line)
            continue
        b64 = images.get(m.group("name")) or ""
        try:
            ok = bool(b64) and bool(base64.b64decode(b64, validate=True))
        except (ValueError, binascii.Error):
            ok = False
        if not ok:
            continue
        page = int(m.group("page")) + 1
        caption = f"{figures.FILED}{page}"
        for nxt in lines[i + 1 : i + 4]:
            s = nxt.strip()
            if s and _MARKER_CAPTION.match(s):
                caption += ": " + " ".join(s.split())[:200].replace("]", ")")
                break
            if s:
                break
        ext = m.group("name").rsplit(".", 1)[-1].lower()
        media = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
        }.get(ext, "image/jpeg")
        out.append(f"![{caption}](data:{media};base64,{b64.strip()})")
    return "\n".join(out)
