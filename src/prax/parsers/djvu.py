"""DjVu through DjVuLibre's tools: the text layer page by page, and
the pages without one rendered and read by the OCR engine the scanned
PDFs use.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from pathlib import Path

from prax import config

from .base import (
    EMPTY_PAGE,
    ExtractionError,
    Partial,
    empty_pages,
    join_pages,
    pages_by_mark,
)
from .pdf import _RIGHT_TO_LEFT, _ocr_engine, _ocr_language, _ocr_rows


@functools.cache
def djvu_tool(name: str) -> str | None:
    """A DjVuLibre program (``djvutxt``, ``ddjvu``), when this machine has
    it: on the PATH, or where the Windows installer and the usual packages
    put it."""
    found = shutil.which(name)
    if found:
        return found
    for folder in (
        r"C:\Program Files (x86)\DjVuLibre",
        r"C:\Program Files\DjVuLibre",
        "/usr/bin",
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ):
        for candidate in (Path(folder) / f"{name}.exe", Path(folder) / name):
            if candidate.exists():
                return str(candidate)
    return None


def _djvu_version() -> str:
    # djvused names the release in its help ("DJVUSED --- DjVuLibre-3.5.29");
    # djvutxt refuses --help, so the stamp said "unknown" (2026-09-28)
    tool = djvu_tool("djvused") or djvu_tool("djvutxt")
    if tool is None:
        return "missing"
    out = subprocess.run(
        [tool, "--help"], capture_output=True, text=True, timeout=10, check=False
    )
    m = re.search(r"DjVuLibre[- ]([0-9][0-9.]*)", out.stdout + out.stderr)
    return m.group(1) if m else "unknown"


DJVU_OCR_SCALE = 50  # percent of a page's full resolution: 300-600 dpi scans


def _djvu(data: bytes, *, previous: str | None = None) -> str:
    """A DjVu document's text, page by page (``djvutxt``): the text layer a
    scanned book usually carries, with the page marks between pages so the
    chunker knows every line's page. A page without a text layer is
    rendered (``ddjvu``) and read by the OCR engine the scanned PDFs use,
    ``parse.ocr_max_pages`` pages a pass: a scan of 746 pages is read in
    windows, the pages read before kept from ``previous`` (the current
    text), and the text comes back as a ``Partial`` while pages wait.
    DjVuLibre is a program, not a package: installed where the door parses
    (winget ``DjVuLibre.DjView``, apt ``djvulibre-bin``, brew
    ``djvulibre``)."""
    djvutxt = djvu_tool("djvutxt")
    if djvutxt is None:
        raise ExtractionError("DjVuLibre is not installed here (djvutxt)")
    import tempfile

    with tempfile.TemporaryDirectory(prefix="prax-djvu-") as tmp:
        src = Path(tmp) / "doc.djvu"
        src.write_bytes(data)
        done = subprocess.run(
            [djvutxt, str(src)],
            capture_output=True,
            timeout=600,
            check=False,
        )
        if done.returncode != 0:
            why = done.stderr.decode("utf-8", "replace").strip()[:200]
            raise ExtractionError(f"djvutxt failed: {why or done.returncode}")
        # djvutxt separates the pages of its output with a form feed; how
        # many pages there are is djvused's to say, as a scanned last page
        # and the end of the output look the same
        pages = done.stdout.decode("utf-8", "replace").split("\f")
        count = _djvu_pages(src)
        if count is not None:
            pages = (pages + [""] * count)[:count]
        elif pages and not pages[-1].strip():
            pages = pages[:-1]
        # a page read in an earlier window keeps what it was read as
        held = pages_by_mark(previous or "")
        pages = [
            held.get(n, "") if len(held.get(n, "").strip()) >= EMPTY_PAGE else text
            for n, text in enumerate(pages, 1)
        ]
        empty = empty_pages(pages)
        budget = config.whole("parse.ocr_max_pages", "PRAX_OCR_MAX_PAGES", 60)
        window = empty[:budget]
        if window and djvu_tool("ddjvu") is not None:
            pages = _djvu_ocr(src, pages, window)
            left = len(empty) - len(window)
        else:
            left = 0  # nothing to read, or nothing to read it with
    text = join_pages(pages)
    return Partial(text, pages_left=left, pages=len(pages)) if left else text


def _djvu_pages(src: Path) -> int | None:
    """How many pages the document has (``djvused -e n``), or None."""
    djvused = djvu_tool("djvused")
    if djvused is None:
        return None
    done = subprocess.run(
        [djvused, str(src), "-e", "n"], capture_output=True, timeout=60, check=False
    )
    text = done.stdout.decode("ascii", "replace").strip()
    return int(text) if done.returncode == 0 and text.isdigit() else None


def _djvu_ocr(src: Path, pages: list[str], window: list[int]) -> list[str]:
    """These pages (by index), read from their pictures."""
    ddjvu = djvu_tool("ddjvu")
    if ddjvu is None:
        return pages  # the text layer is what there is
    import numpy as np
    from PIL import Image

    engine = _ocr_engine()
    lang = _ocr_language()
    out = list(pages)
    for i in window:
        image = src.with_name(f"page-{i + 1}.tif")
        subprocess.run(
            [
                ddjvu,
                "-format=tiff",
                f"-page={i + 1}",
                f"-scale={DJVU_OCR_SCALE}",
                str(src),
                str(image),
            ],
            capture_output=True,
            timeout=300,
            check=False,
        )
        if not image.exists():
            continue
        with Image.open(image) as picture:
            pixels = np.asarray(picture.convert("RGB"))
        result = engine(pixels)
        boxes = [] if result.boxes is None else list(result.boxes)
        texts = [] if result.txts is None else list(result.txts)
        out[i] = "\n".join(
            _ocr_rows(boxes, texts, right_to_left=lang in _RIGHT_TO_LEFT)
        )
    return out
