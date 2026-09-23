"""Figures: the images that belong to a document's content, in its text.

A captured web page or a PDF carries pictures — a photograph with its
caption, a plot, a schematic, a panel — that the text alone loses. This
module finds the ones that are part of the content (not the site's chrome
or a running logo), writes each into the Markdown where it sits as

    ![caption](figure:<sha256 of the image bytes>)

and serves the bytes back out of the original on request, so nothing new
is stored (the original already holds them; the reference is a hash,
invariant 2). A reading is asked for with what the document says around
the figure — its title, the caption, the text on either side — because
what a plot is *of* is written there and not in the picture. The chunker
makes such a line, its caption and any description one ``figure`` chunk.
A second pass (``describe``) asks the vision model what each figure shows
and writes that under the image line:

    *Figure, as read by <model>:* what the picture shows …

What counts as a figure. HTML: an image inside ``<figure>`` (author's
markup, with its ``<figcaption>``), or an image with a real ``alt`` text,
or a big one — and only images the snapshot carries inline (data URLs;
the door does not fetch). PDF: a placed raster image at least
``MIN_PDF_PT`` points on each side, not the whole page (a scan) and not
repeated on many pages (a logo), with the nearest "Figure N" block below
it as caption. Vector
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
MAX_PDF_PAGE_SHARE = 0.8  # an image covering more of the page is the page (a scan)
MAX_PDF_REPEATS = 3  # an image placed on more pages than this is a logo
MAX_FIGURES = 60  # per document
MAX_SIDE = 1600  # pixels, for the vision model
_CAPTION = re.compile(r"^\W*(fig\.?|figure|abb\.?|abbildung)\s*\d+", re.IGNORECASE)
# the page a line belongs to, as pymupdf4llm marks it (prax.chunking)
_PAGE_MARK = re.compile(r"^--- end of page\.page_number=(\d+) ---\s*$")
# a caption *line*, which is stricter than _CAPTION: the number is
# followed by a delimiter, so "Figure 2 shows a recorded performance" —
# prose about a figure — is not taken for the figure's own caption
_CAPTION_LINE = re.compile(
    r"^\W*(?:fig\.?|figure|abb\.?|abbildung)\s*\d+(?:\.\d+)?\s*[.:;)\]|–—-](?!\d)",
    re.IGNORECASE,
)

REF = re.compile(
    r"^!\[(?P<alt>[^\]\n]*)\]\(figure:(?P<ref>[0-9a-f]{16,64})\)[ \t]*$", re.MULTILINE
)
READ_BY = re.compile(r"^\*Figure, as read by (?P<model>.+?):\*", re.MULTILINE)
FIGURES_HEADING = "## Figures"
UNCAPTIONED = "Figure on page "  # a PDF image no caption claims
# a picture of a scanned page: no object in the original holds it, so
# marker's crop is kept as its own content-addressed artifact, and its
# caption says so — a figure-refs pass keeps such a reference, since the
# original will never yield it
FILED = "Picture on page "
# a picture a parser inlines in its text for the door to file: what the
# door turns into a figure reference before the text is indexed
DATA_IMAGE = re.compile(
    r"^!\[(?P<alt>[^\]\n]*)\]"
    r"\((?P<url>data:image/[a-z+.-]+;base64,[A-Za-z0-9+/=\s]+)\)[ \t]*$",
    re.MULTILINE,
)
MAX_FILED_BYTES = 8_000_000  # a picture bigger than this is not filed

# a caption whose picture no extractor could pull out of the PDF: the
# region above it is rendered instead (``add_crops``)
CROP_DPI = 150
CROP_GAP = 24  # points of white space above a figure that end it
CROP_MIN_AREA = 2_000  # square points: less is a rule or a stray path
CROP_MIN_SIDE = 40  # points: less is not a picture
CROP_MAX_PAGE_SHARE = 0.9  # more of the page than this is the page itself
CROP_NEAR = 12  # points a drawing may sit outside the caption's column
CROP_ADJACENT = 3  # lines: a picture this close to a caption is its own
_EMPHASIS = re.compile(r"[*_`]+")
_FIGURE_NUMBER = re.compile(
    r"^\W*(?:fig\.?|figure|abb\.?|abbildung)\s*(\d+)", re.IGNORECASE
)


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
        if len(page.get_text().strip()) < 20:
            continue  # a scanned page: its images are the page, not figures
        page_area = max(1.0, page.rect.width * page.rect.height)
        for info in infos:
            xref = info.get("xref") or 0
            bbox = info.get("bbox")
            if not xref or not bbox:
                continue
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if w < MIN_PDF_PT or h < MIN_PDF_PT:
                continue
            if w * h >= MAX_PDF_PAGE_SHARE * page_area:
                continue  # the whole page as one picture: a scan, not a figure
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
        caption = caption or f"{UNCAPTIONED}{page_no + 1}"
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


# Where a figure that is not in the original comes from — a filed picture
# (FILED): on the door, the archive; on a worker, the door's own figure
# route. Set for the duration of a reading (``with fetching(fn)``).
_fetch: list[Any] = [None]


class fetching:
    """``with figures.fetching(fn):`` — ``fn(ref) -> (bytes, media) | None``
    answers for the figures the original does not hold."""

    def __init__(self, fn: Any) -> None:
        self.fn = fn
        self.before: Any = None

    def __enter__(self) -> None:
        self.before = _fetch[0]
        _fetch[0] = self.fn

    def __exit__(self, *exc: object) -> None:
        _fetch[0] = self.before


def media_of(data: bytes) -> str:
    """An image's media type from its first bytes (what a filed picture is
    served as)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


def find(data: bytes, ref: str, *, filed: bool = False) -> tuple[bytes, str] | None:
    """The bytes of the figure ``ref`` inside an original (a PDF or an HTML
    snapshot, told apart by their bytes), with their media type; else what
    the fetch hook answers (a filed picture: the archive on the door, the
    door's figure route on a worker); None when nothing has it. A
    ``filed`` picture (its caption says so: ``FILED``) is asked of the
    hook first — no object in the original holds it, and looking for it
    there means extracting and hashing every image of the original: 20 s
    a picture over a 200-page scan, for each of its 500 pictures."""
    if filed:
        found = _fetched(ref)
        return found if found is not None else _find_in(data, ref)
    found = _find_in(data, ref)
    return found if found is not None else _fetched(ref)


def _fetched(ref: str) -> tuple[bytes, str] | None:
    if _fetch[0] is None:
        return None
    try:
        return _fetch[0](ref)
    except Exception:  # noqa: BLE001 - the door away is no figure
        return None


def _find_in(data: bytes, ref: str) -> tuple[bytes, str] | None:
    if data[:5] == b"%PDF-":
        for fig in of(data):
            if fig.ref == ref:
                return fig.data, fig.media_type
        return None
    for m in re.finditer(rb'src="(data:image/[^"]+)"', data):
        found = _data_url(m.group(1).decode("ascii", "replace"))
        if found and sha(found[0]) == ref:
            return found
    return None


def file_inline(text: str, archive: Any) -> tuple[str, int]:
    """The text with every picture a parser inlined (``DATA_IMAGE``) filed
    through ``archive(bytes) -> sha`` and referenced as ``figure:<sha>``
    in its place; how many were filed. A picture too big, or one whose
    data URL does not decode, is dropped from the text."""
    n = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal n
        found = _data_url(m.group("url").replace("\n", "").replace(" ", ""))
        if found is None or len(found[0]) > MAX_FILED_BYTES:
            return ""
        ref = archive(found[0])
        n += 1
        alt = m.group("alt").replace("]", ")")
        return f"![{alt}](figure:{ref})"

    text = DATA_IMAGE.sub(sub, text)
    # a data line the pattern did not take (malformed): no blob stays behind
    text = re.sub(
        r"^!\[[^\]\n]*\]\(data:image/[^)\n]*\)[ \t]*$", "", text, flags=re.MULTILINE
    )
    return text, n


def of(data: bytes) -> list[Figure]:
    """The figures of an original, a PDF or an HTML snapshot told apart by
    their bytes."""
    if data[:5] == b"%PDF-":
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return pdf_figures(doc)
    return html_figures(data)


def crop_region(page: Any, caption: Any) -> Any | None:
    """The picture above a caption, as a rectangle of the page.

    Every drawing and image whose box sits over the caption and overlaps
    its column, taken from the caption upwards and stopped by a gap of
    white space, so the paragraph above is not swept in. None when there
    is nothing there, when what is there is too small to be a picture, or
    when it covers so much of the page that it is the page.
    """
    import pymupdf

    boxes = [pymupdf.Rect(d["rect"]) for d in page.get_drawings()]
    for image in page.get_images(full=True):
        try:
            box = page.get_image_bbox(image)
        except Exception:  # noqa: BLE001, S112 - an image MuPDF cannot place
            continue
        if box:
            boxes.append(pymupdf.Rect(box))
    over = [
        b
        for b in boxes
        if b.y1 <= caption.y0 + 2
        and b.x1 > caption.x0 - CROP_NEAR
        and b.x0 < caption.x1 + CROP_NEAR
        and b.get_area() > 4
    ]
    if not over:
        return None
    over.sort(key=lambda b: -b.y1)
    kept = [over[0]]
    for b in over[1:]:
        if min(k.y0 for k in kept) - b.y1 > CROP_GAP:
            break
        kept.append(b)
    rect = kept[0]
    for b in kept[1:]:
        rect |= b
    rect.y1 = min(caption.y0 - 1, rect.y1)
    page_area = max(1.0, page.rect.width * page.rect.height)
    if rect.width < CROP_MIN_SIDE or rect.height < CROP_MIN_SIDE:
        return None
    if rect.get_area() < CROP_MIN_AREA:
        return None
    if rect.get_area() > CROP_MAX_PAGE_SHARE * page_area:
        return None
    return rect


def find_caption(page: Any, caption: str) -> Any | None:
    """Where a caption sits on its page. The text carries the extractor's
    Markdown emphasis (``_Block diagram_``) and the page does not, so the
    search is over the plain words, and shortens until it finds them."""
    plain = " ".join(_EMPHASIS.sub("", caption).split())
    for n in (48, 24, 14):
        if len(plain) < 6:
            break
        hits = page.search_for(plain[:n])
        if hits:
            return hits[0]
    return None


def bare_captions(lines: list[str]) -> set[int]:
    """The caption lines whose figure has no picture anywhere in the text.

    A caption alone is not enough to go by: pymupdf4llm places
    ``![Figure 2: …](figure:<sha>)`` for an image it could extract and
    leaves the caption in the prose as well, so the same figure appears
    twice. A caption is bare when no picture line carries its number, and
    none sits within a line or two of it.
    """
    pictured: set[str] = set()
    picture_at: list[int] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not (REF.match(stripped) or DATA_IMAGE.match(stripped)):
            continue
        picture_at.append(i)
        alt = stripped[2 : stripped.find("]")] if "]" in stripped else ""
        number = _FIGURE_NUMBER.match(alt)
        if number:
            pictured.add(number.group(1))
    out: set[int] = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not _CAPTION_LINE.match(stripped) or REF.match(stripped):
            continue
        number = _FIGURE_NUMBER.match(stripped)
        if number and number.group(1) in pictured:
            continue  # its picture is already in the text
        if any(abs(i - at) <= CROP_ADJACENT for at in picture_at):
            continue  # a picture right beside it is its own
        out.add(i)
    return out


def add_crops(data: bytes, previous: str, *, dpi: int = CROP_DPI) -> str:
    """The current text with a picture under every caption that lacked
    one: the region above the caption on its own page, rendered and
    inlined as a data URL for the door to file (``file_inline``).

    Only a caption whose figure has no picture anywhere
    (``bare_captions``) is touched, and only where something is actually
    drawn above it. A caption the page does not carry, or one with
    nothing over it, is left exactly as it was.
    """
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        lines = previous.split("\n")
        bare = bare_captions(lines)
        page_no = 1
        out: list[str] = []
        made = 0
        for i, line in enumerate(lines):
            mark = _PAGE_MARK.match(line)
            stripped = line.strip()
            if made >= MAX_FIGURES or i not in bare or page_no > doc.page_count:
                out.append(line)
                if mark:
                    page_no = int(mark.group(1)) + 1
                continue
            page = doc[page_no - 1]
            where = find_caption(page, stripped)
            rect = crop_region(page, where) if where is not None else None
            if rect is None:
                out.append(line)
                continue
            png = page.get_pixmap(clip=rect, dpi=dpi).tobytes("png")
            if len(png) > MAX_FILED_BYTES:
                out.append(line)
                continue
            alt = " ".join(_EMPHASIS.sub("", stripped).split()).replace("]", ")")
            url = base64.b64encode(png).decode("ascii")
            out.append(f"![{alt}](data:image/png;base64,{url})")
            made += 1
        return "\n".join(out)
    finally:
        doc.close()


def add_refs(data: bytes, previous: str) -> str:
    """The current text with the original's figures referenced in it — the
    cheap half of the retroactive pass: no layout analysis, no model, the
    text as it is plus the image lines it lacked, minus references the
    original no longer yields (an earlier rule's mistakes)."""
    found = of(data)
    return place(prune(previous, {f.ref for f in found}), found)


def prune(text: str, keep: set[str]) -> str:
    """The text without the figure lines whose reference is not in
    ``keep``, their readings under them gone too."""
    out: list[str] = []
    skipping = False
    for line in text.split("\n"):
        m = REF.match(line)
        if m:
            # a filed picture is never in the original: kept
            skipping = m.group("ref") not in keep and not m.group("alt").startswith(
                FILED
            )
            if skipping:
                continue
        elif skipping and READ_BY.match(line):
            continue
        else:
            skipping = False
        out.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    # an emptied "## Figures" section goes too
    return re.sub(rf"\n*{re.escape(FIGURES_HEADING)}\s*$", "\n", text)


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

# The model is shown the figure and, when the document has them, the
# things around it: what the document is called, the figure's caption and
# the text on either side of the image line. Without them a plot is "six
# stacked curves that likely illustrate spectral characteristics"; with
# them it is "the frequency responses of the five learned CNN kernels
# against the ground truth filter" — the names are in the document, not
# in the picture, and they are what a search later looks for. The
# instruction not to state what is not visible is what keeps the context
# from being described instead of the figure.
FIGURE_PROMPT = """\
This is a figure from a document in a personal research library (a paper,
a manual, an article, a note).{context}
In three to six sentences, say what the figure shows so that someone
searching the library would find it and someone who cannot see it would
understand it.{naming} Describe the specific content: the kind of figure
(photograph, plot, schematic, block diagram, screenshot, table as image,
drawing), its subject, axes and what the curves do, the blocks and the
signal path, the components and their values, the device and its
controls, the people or things in a photograph. Transcribe short labels
and numbers where they carry the meaning.{honesty} No preamble, no
"the image shows".
"""

NAMING = """\
 Begin by naming what it is of, in the document's own terms — the caption
and the text around it tell you what the thing is called."""

HONESTY = """\
 Everything you say about the picture must be visible in it: use the text
to name things, never to add what the image does not show, and where the
two disagree, the image is what you describe. Do not summarise the
surrounding text and do not guess at what the figure "likely" means.
Never mention the caption or the text: write about the picture only."""

# A frame of a recording (the extension's video capture): the picture is a
# moment of a talk, and what is on screen is most often a slide whose text
# is what a search should find — so the reading transcribes it.
FRAME_PROMPT = """\
This is a frame of a recorded talk or video at {moment}, taken as a
screenshot and kept in a personal research library beside the
transcript.{context}
Say what is on screen so that someone searching the library would find it
and someone who cannot see it would understand it. If it is a slide,
transcribe its text as written — title, bullet points, labels, equations,
code — and then say what its diagram, plot or picture shows. If it is a
demonstration, an instrument, a screen recording or a person, say what is
shown and what is being done. Two to eight sentences; a slide's text in
full.{honesty} No preamble, no "the frame shows".
"""

FRAME_HONESTY = """\
 Everything you say must be visible in the frame: the words spoken around
it tell you what things are called, never what the picture must contain,
and where the two disagree, the picture is what you describe. Do not
summarise the talk and do not guess at what the frame "likely" means.
Never mention the transcript: write about the picture only."""

TIME_CAPTION = re.compile(r"^(?P<t>(?:\d{1,2}:)?\d{1,2}:\d{2})\s*[—–-]\s*")

AROUND_CHARS = 700  # of the text on each side of the image line
TITLE_CHARS = 120


def document_title(text: str) -> str:
    """What the document calls itself: its first Markdown heading."""
    for line in text[:4000].split("\n"):
        if line.startswith("#"):
            return line.lstrip("#").strip()[:TITLE_CHARS]
    return ""


def around(lines: list[str], at: int) -> str:
    """The text on either side of the image line at ``at``, without the
    readings of any figure (a model must not be shown its own earlier
    work, or another figure's)."""
    keep = [
        ln
        for ln in lines[: max(0, at)][-40:] + ["…"] + lines[at + 1 :][:40]
        if not READ_BY.match(ln) and not REF.match(ln)
    ]
    text = " ".join(" ".join(keep).split())
    if len(text) > 2 * AROUND_CHARS:
        half = AROUND_CHARS
        text = text[:half] + " … " + text[-half:]
    return text


def figure_prompt(title: str, caption: str, near: str) -> str:
    """The prompt for one figure, with what the document says around it."""
    told = []
    if title:
        told.append(f'It is from "{title}".')
    if caption:
        told.append(f"Its caption: {caption}")
    if near:
        told.append(f"The text around it: {near}")
    if not told:
        return FIGURE_PROMPT.format(context="", naming="", honesty="")
    return FIGURE_PROMPT.format(
        context="\n\n" + "\n".join(told) + "\n",
        naming=NAMING.rstrip("\n").replace("\n", " "),
        honesty=HONESTY.rstrip("\n").replace("\n", " "),
    )


def frame_prompt(title: str, caption: str, near: str) -> str:
    """The prompt for a frame of a recording: its moment (the caption's
    time mark), the title, the words spoken around it."""
    m = TIME_CAPTION.match(caption or "")
    moment = m.group("t") if m else "an unknown moment"
    told = []
    if title:
        told.append(f'It is from "{title}".')
    if near:
        told.append(f"The words spoken around this moment: {near}")
    return FRAME_PROMPT.format(
        moment=moment,
        context=("\n\n" + "\n".join(told) + "\n") if told else "",
        honesty=FRAME_HONESTY.rstrip("\n").replace("\n", " "),
    )


def is_frame(caption: str) -> bool:
    """A figure captioned with its moment: a frame of a recording."""
    return bool(TIME_CAPTION.match(caption or ""))


def _read_by(line: str, who: str) -> bool:
    """Is this line ``who``'s reading of a figure?"""
    m = READ_BY.match(line)
    return bool(m and m.group("model").strip() == who)


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


WHICH = ("captioned", "all", "again", "all-again")


def describe(data: bytes, previous: str) -> str:
    """The text with every figure reference followed by the vision model's
    reading of it. ``parse.figures`` says which figures: ``captioned``
    (the default; a PDF image no caption claims is usually decoration),
    ``all``, or either of them with ``-again`` — ``again`` and
    ``all-again`` read the figures this model has read before too, its
    earlier reading replaced, which is how a library takes up a better
    prompt or a better model of the same name. ``data`` is the original
    the figures come from."""
    from prax import config
    from prax.parsers import ExtractionError, vision

    model = vision.model_name()
    which = str(config.setting("parse.figures", "PRAX_FIGURES", "captioned"))
    if which not in WHICH:
        raise ExtractionError(f"parse.figures must be one of {WHICH}, not {which!r}")
    again = which.endswith("again")
    every = which.startswith("all")
    wanted = [
        r
        for r in refs(previous)
        if (again or model not in r["described_by"])
        and (every or not r["caption"].startswith(UNCAPTIONED))
    ]
    if not wanted:
        return previous
    lines = previous.split("\n")
    title = document_title(previous)
    from prax.parsers import video

    recording = video.is_transcript(data)  # its figures are frames of a talk
    done = 0
    broken: list[str] = []
    for r in wanted:
        found = find(data, r["ref"], filed=r["caption"].startswith(FILED))
        if found is None:
            continue
        try:
            image, _media = readable(*found)
        except (OSError, ValueError) as exc:
            # a picture Pillow cannot decode (a truncated stream, an odd
            # colour space): the others in the document still get read
            broken.append(f"{r['ref'][:12]}: {exc}")
            continue
        at = next(
            (
                i
                for i, line in enumerate(lines)
                if line.rstrip().endswith(f"(figure:{r['ref']})")
            ),
            -1,
        )
        near = around(lines, at) if at >= 0 else ""
        if recording and is_frame(r["caption"]):
            prompt = frame_prompt(title, r["caption"], near)
        else:
            prompt = figure_prompt(title, r["caption"], near)
        text, who = vision.read(image, prompt, max_tokens=600)
        text = " ".join(text.split())
        if not text:
            continue
        # the reading goes right under the image line, inside its
        # paragraph; reading again replaces this model's earlier one and
        # leaves another model's where it is
        for i, line in enumerate(lines):
            if line.rstrip().endswith(f"(figure:{r['ref']})"):
                at = i + 1
                if again:
                    # through the readings under this image line: this
                    # model's earlier one goes, another model's stays
                    while at < len(lines) and READ_BY.match(lines[at]):
                        if _read_by(lines[at], who):
                            del lines[at]
                        else:
                            at += 1
                lines.insert(at, f"*Figure, as read by {who}:* {text}")
                done += 1
                break
    if not done:
        why = f" ({'; '.join(broken[:3])})" if broken else ""
        raise ExtractionError("no figure could be read" + why)
    return "\n".join(lines)
