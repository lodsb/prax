"""Figures: the images that belong to a document's content, in its text.

A captured web page or a PDF carries pictures — a photograph with its
caption, a plot, a schematic, a panel — that the text alone loses. This
module finds the ones that are part of the content (not the site's chrome
or a running logo), writes each into the Markdown where it sits as

    ![caption](figure:<sha256 of the image bytes>)

and serves the bytes back out of the original on request, so nothing new
is stored (the original already holds them; the reference is a hash,
invariant 2). The chunker makes such a line, its caption and any
description one ``figure`` chunk. A second pass (``describe``) asks the
vision model what each figure shows and writes that under the image line:

    *Figure, as read by <model>:* what the picture shows …

What counts as a figure. HTML: an image inside ``<figure>`` (author's
markup, with its ``<figcaption>``), or an image with a real ``alt`` text,
or a big one — and only images the snapshot carries inline (data URLs;
the door does not fetch). PDF: a placed raster image at least
``MIN_PDF_PT`` points on each side that is not repeated on many pages
(a logo), with the nearest "Figure N" block below it as caption. Vector
drawings are not found this way; a page rendered for ``vision-pages``
covers those.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
from dataclasses import dataclass
from typing import Any

MIN_HTML_BYTES = 4_000  # smaller inline images are icons
BIG_HTML_BYTES = 40_000  # a photo, caption or not
MIN_ALT_WORDS = 3
MIN_PDF_PT = 90  # points on each side
MAX_PDF_REPEATS = 3  # an image placed on more pages than this is a logo
MAX_FIGURES = 60  # per document
MAX_SIDE = 1600  # pixels, for the vision model
_CAPTION = re.compile(r"^\W*(fig\.?|figure|abb\.?|abbildung)\s*\d+", re.IGNORECASE)

REF = re.compile(
    r"^!\[(?P<alt>[^\]\n]*)\]\(figure:(?P<ref>[0-9a-f]{16,64})\)[ \t]*$", re.MULTILINE
)
READ_BY = re.compile(r"^\*Figure, as read by (?P<model>.+?):\*", re.MULTILINE)
FIGURES_HEADING = "## Figures"


@dataclass
class Figure:
    ref: str  # sha256 of the bytes, hex
    data: bytes
    media_type: str
    caption: str
    anchor: str | None = None  # text just before it, to place it in the Markdown
    page: int | None = None  # PDF: 1-based


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _norm(s: str) -> str:
    return re.sub(r"[\s*_`#>]+", " ", s).strip().lower()


# ------------------------------------------------------------------ HTML


def _data_url(src: str) -> tuple[bytes, str] | None:
    if not src.startswith("data:image/"):
        return None
    head, _, payload = src.partition(",")
    media = head[5:].split(";")[0]
    try:
        data = base64.b64decode(payload) if ";base64" in head else payload.encode()
    except ValueError:
        return None
    return data, media


def html_figures(html: bytes) -> list[Figure]:
    """The content images of a page with their captions, in document order."""
    from lxml import html as lxml_html

    try:
        # decoded here: given bytes, lxml guesses the charset and mangles UTF-8
        root = lxml_html.fromstring(html.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - not HTML we can walk
        return []
    out: list[Figure] = []
    seen: set[str] = set()
    last_text = ""
    for el in root.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag in ("script", "style"):
            continue
        if tag != "img":
            text = (el.text or "").strip()
            if len(text) >= 40:
                last_text = text
            continue
        found = _data_url(el.get("src") or el.get("data-src") or "")
        if found is None:
            continue
        data, media = found
        if len(data) < MIN_HTML_BYTES or media == "image/svg+xml":
            continue
        figure = next((a for a in el.iterancestors() if a.tag == "figure"), None)
        caption = ""
        if figure is not None:
            cap = figure.find(".//figcaption")
            if cap is not None:
                caption = " ".join(cap.text_content().split())
        alt = " ".join((el.get("alt") or "").split())
        if not caption:
            caption = alt
        wordy = len(alt.split()) >= MIN_ALT_WORDS
        if figure is None and not wordy and len(data) < BIG_HTML_BYTES:
            continue  # chrome: no author's markup, no words, not a photo
        ref = sha(data)
        if ref in seen:
            continue
        seen.add(ref)
        out.append(Figure(ref, data, media, caption, anchor=last_text or None))
        if len(out) >= MAX_FIGURES:
            break
    return out


# ------------------------------------------------------------------- PDF


def pdf_figures(doc: Any) -> list[Figure]:
    """The placed raster images of a PDF worth a reference, with the
    nearest "Figure N" block below each as its caption."""
    placements: dict[int, list[tuple[int, Any]]] = {}
    for page in doc:
        try:
            infos = page.get_image_info(xrefs=True)
        except Exception:  # noqa: BLE001, S112 - a page MuPDF cannot walk
            continue
        for info in infos:
            xref = info.get("xref") or 0
            bbox = info.get("bbox")
            if not xref or not bbox:
                continue
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if w < MIN_PDF_PT or h < MIN_PDF_PT:
                continue
            placements.setdefault(xref, []).append((page.number, bbox))
    out: list[Figure] = []
    seen: set[str] = set()
    for xref, places in placements.items():
        if len({p for p, _ in places}) > MAX_PDF_REPEATS:
            continue  # on many pages: a logo, a rule, a running head
        try:
            img = doc.extract_image(xref)
        except Exception:  # noqa: BLE001, S112 - an image MuPDF cannot decode
            continue
        data = img.get("image") or b""
        if len(data) < MIN_HTML_BYTES:
            continue
        ref = sha(data)
        if ref in seen:
            continue
        seen.add(ref)
        page_no, bbox = places[0]
        page = doc[page_no]
        caption = _pdf_caption(page, bbox)
        ext = str(img.get("ext") or "png").lower()
        media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "jpx": "image/jp2"}.get(
            ext, f"image/{ext}"
        )
        caption = caption or f"Figure on page {page_no + 1}"
        out.append(Figure(ref, data, media, caption, page=page_no + 1))
        if len(out) >= MAX_FIGURES:
            break
    return out


def _pdf_caption(page: Any, bbox: Any) -> str:
    """The nearest text block starting like a caption below the image."""
    best: tuple[float, str] | None = None
    try:
        blocks = page.get_text("blocks")
    except Exception:  # noqa: BLE001
        return ""
    for b in blocks:
        y0, text = b[1], " ".join(str(b[4]).split())
        if not _CAPTION.match(text):
            continue
        gap = y0 - bbox[3]
        if -20 <= gap <= 160 and (best is None or gap < best[0]):
            best = (gap, text)
    return best[1] if best else ""


# --------------------------------------------------------------- placing


def line_for(fig: Figure) -> str:
    alt = fig.caption.replace("]", ")").replace("\n", " ").strip()
    return f"![{alt}](figure:{fig.ref})"


def place(markdown: str, figures: list[Figure]) -> str:
    """The Markdown with a reference line per figure: after the paragraph
    holding its anchor text, or before its caption line (PDF, by page),
    or under a "## Figures" heading at the end when neither is found.
    A figure the text already references is left alone."""
    if not figures:
        return markdown
    present = {m.group("ref") for m in REF.finditer(markdown)}
    lines = markdown.split("\n")
    inserts: dict[int, list[str]] = {}  # after line index -> lines
    leftovers: list[Figure] = []
    first_placed: int | None = None  # index in `figures` of the first one anchored
    for k, fig in enumerate(figures):
        if fig.ref in present:
            continue
        at = _anchor_line(lines, fig)
        if at is None:
            leftovers.append(fig)
        else:
            inserts.setdefault(at, []).append(line_for(fig))
            if first_placed is None:
                first_placed = k
    # an unplaced figure that comes before every placed one (a lead image)
    # goes under the title; the rest under a heading at the end
    lead: list[Figure] = []
    if first_placed is not None:
        lead = [f for f in leftovers if figures.index(f) < first_placed]
        leftovers = [f for f in leftovers if f not in lead]
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        if i == 0 and lead:
            for fig in lead:
                out += ["", line_for(fig)]
            out.append("")
        for ref_line in inserts.get(i, []):
            out += ["", ref_line, ""]
    if leftovers:
        out += ["", FIGURES_HEADING, ""]
        for fig in leftovers:
            out += [line_for(fig), ""]
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text)


def _anchor_line(lines: list[str], fig: Figure) -> int | None:
    """The index of the line after which the figure goes."""
    if fig.page is not None:
        # the page's segment; the caption line inside it if there is one
        first, last = _page_span(lines, fig.page)
        if first is None:
            return None
        key = _norm(fig.caption)[:40]
        if key and _CAPTION.match(fig.caption):
            for i in range(first, last + 1):
                if _norm(lines[i]).startswith(key):
                    return i - 1  # the image goes right above its caption
        return last  # the end of the page's text
    if fig.anchor:
        key = _norm(fig.anchor)[:50]
        if key:
            for i, line in enumerate(lines):
                if key in _norm(line):
                    j = i
                    while j + 1 < len(lines) and lines[j + 1].strip():
                        j += 1  # the end of that paragraph
                    return j
    return None


_PAGE_MARK = re.compile(r"^--- end of page\.page_number=(\d+) ---\s*$")


def _page_span(lines: list[str], page: int) -> tuple[int | None, int | None]:
    """First and last line index of a page's text: what precedes the
    marker "end of page N" is page N."""
    start = 0
    for i, line in enumerate(lines):
        m = _PAGE_MARK.match(line.strip())
        if not m:
            continue
        if int(m.group(1)) == page:
            return start, max(start, i - 1)
        start = i + 1
    return None, None


# ---------------------------------------------------------------- lookup


def find(data: bytes, ref: str) -> tuple[bytes, str] | None:
    """The bytes of the figure ``ref`` inside an original (a PDF or an HTML
    snapshot, told apart by their bytes), with their media type; None when
    the original has no such image."""
    if data[:5] == b"%PDF-":
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for fig in pdf_figures(doc):
                if fig.ref == ref:
                    return fig.data, fig.media_type
        return None
    for m in re.finditer(rb'src="(data:image/[^"]+)"', data):
        found = _data_url(m.group(1).decode("ascii", "replace"))
        if found and sha(found[0]) == ref:
            return found
    return None


def refs(text: str) -> list[dict[str, Any]]:
    """The figure references in a text: ``{ref, caption, described_by}``
    in order, ``described_by`` the models whose reading follows the line."""
    out = []
    for m in REF.finditer(text):
        tail = text[m.end() : m.end() + 4000]
        block = tail.split("\n\n", 1)[0]
        out.append(
            {
                "ref": m.group("ref"),
                "caption": m.group("alt"),
                "described_by": [
                    r.group("model").strip() for r in READ_BY.finditer(block)
                ],
            }
        )
    return out


# ------------------------------------------------------------ describing

FIGURE_PROMPT = """\
This is a figure from a document in a personal research library (a paper,
a manual, an article, a note). In three to six sentences, say what it
shows so that someone searching the library would find it and someone who
cannot see it would understand it: the kind of figure (photograph, plot,
schematic, block diagram, screenshot, table as image, drawing), its
subject, and the specific content — axes and what the curves do, the
blocks and the signal path, the components and their values, the device
and its controls, the people or things in a photograph. Transcribe short
labels and numbers where they carry the meaning. No preamble, no
"the image shows".
"""


def readable(data: bytes, media_type: str) -> tuple[bytes, str]:
    """The image as PNG or JPEG under ``MAX_SIDE`` pixels: what both the
    local server (stb_image: no AVIF, no WebP) and the Claude API read."""
    from PIL import Image

    if media_type in ("image/png", "image/jpeg"):
        with Image.open(io.BytesIO(data)) as im:
            if max(im.size) <= MAX_SIDE:
                return data, media_type
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if max(im.size) > MAX_SIDE:
            im.thumbnail((MAX_SIDE, MAX_SIDE))
        if im.mode not in ("RGB", "RGBA", "L"):
            im = im.convert("RGBA" if "A" in im.mode else "RGB")
        buf = io.BytesIO()
        if im.mode == "RGB":
            im.save(buf, "JPEG", quality=88)
            return buf.getvalue(), "image/jpeg"
        im.save(buf, "PNG")
        return buf.getvalue(), "image/png"


def describe(data: bytes, previous: str) -> str:
    """The text with every figure reference followed by the vision model's
    reading of it (a figure that model has read already is left alone).
    ``data`` is the original the figures come from."""
    from prax.parsers import ExtractionError, vision

    model = vision.model_name()
    wanted = [r for r in refs(previous) if model not in r["described_by"]]
    if not wanted:
        return previous
    lines = previous.split("\n")
    done = 0
    for r in wanted:
        found = find(data, r["ref"])
        if found is None:
            continue
        image, _media = readable(*found)
        text, who = vision.read(image, FIGURE_PROMPT, max_tokens=600)
        text = " ".join(text.split())
        if not text:
            continue
        # the reading goes right under the image line, inside its paragraph
        for i, line in enumerate(lines):
            if line.rstrip().endswith(f"(figure:{r['ref']})"):
                lines.insert(i + 1, f"*Figure, as read by {who}:* {text}")
                done += 1
                break
    if not done:
        raise ExtractionError("no figure could be read")
    return "\n".join(lines)
