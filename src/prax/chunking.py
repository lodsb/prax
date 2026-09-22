"""Structure-aware chunking of a text artifact.

The text artifact is Markdown (what every extractor produces; plain text is
degenerate Markdown). It is parsed into elements — headings, pipe tables,
fenced code, figure captions, paragraphs, page markers — and grouped into
chunks:

* one chunk per **table**, with an adjacent "Table N" caption folded in and
  the parsed grid in ``data``;
* one chunk per **figure** caption ("Figure N …", "Fig. N …");
* one chunk per **formula**: a display equation alone on its line, with the
  LaTeX and the number the prose refers to it by in ``data``. Inline maths
  stays in the sentence it belongs to — a chunk is a region of the artifact
  and cannot tear one;
* one chunk per fenced **code** block;
* one chunk per **reference** — an entry of the reference list, under a
  References/Bibliography heading (``prax.references``: numbered, listed,
  author-year, Elsevier's one paragraph cut at its inline numbers), with
  what it names in ``data`` (number, surnames, year, title, a printed id)
  and, once the ``references`` pass has matched it, the library document
  it cites. A paragraph there that opens no entry continues the one before
  it; one before any entry is text;
* one chunk per **ask** block of a page — from ``<!-- prax:ask id=… "…"
  -->`` to its ``<!-- /prax:ask … -->`` (``prax.blocks``), the answer the
  door keeps there with the question, the block's id and when it was
  asked in ``data``. Like a reference it is set aside: never embedded,
  out of a search, so an answer is never its own evidence;
* one chunk for the **comment** section of a captured page, from the
  ``## Comments`` heading the HTML parser writes to the next heading of its
  own level or the end (``prax.furniture``). Set aside like a reference:
  never embedded, out of a search, out of what an extraction reads;
* one chunk per **ad**: a run of advertising in a capture — the sponsor
  read of a transcript, the offer block of a video's description — found by
  a sponsor's mark together with something to act on, and reaching over the
  pieces beside it that name the same brand (``prax.furniture``). Set aside
  too, and folded in the document view;
* one chunk for a recipe's **ingredients**: the region from its "Zutaten"
  or "Ingredients" heading to the last of its lists, whose ``data`` holds
  the servings and every line with its amount, unit and note
  (``prax.ingredients``). One box rather than four fragments, and not set
  aside: an ingredient is what a search for one should find;
* **text** chunks of consecutive paragraphs under the same heading path, up
  to ``TARGET_CHARS``; a paragraph longer than ``MAX_CHARS`` falls back to
  overlapping fixed windows.

Every chunk carries a locator ``{"char_start", "char_end", "page"?,
"time"?, "time_end"?}`` into
the artifact, and the invariant ``chunk.text == text[char_start:char_end]``
holds for every chunk, so ``get`` can return exactly what search matched.
Page numbers come from the ``--- end of page.page_number=N ---`` markers
pymupdf4llm emits; the markers themselves belong to no chunk. Times come
from a transcript's own markers — a paragraph that opens ``[12:34]``, a
frame whose caption opens ``12:34 —`` — and stay in the text: ``time`` is
where the chunk starts in the recording, ``time_end`` where the next
timed chunk does, so a cited passage can link to the moment.

Chunks are disposable (rationale R3): change this module, run
``prax maintain --rechunk``, nothing else moves.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from prax import blocks, furniture, ingredients, references

TARGET_CHARS = 1200  # flush a text chunk when the next paragraph would exceed this
MIN_CHARS = 300  # merge into the previous chunk when smaller than this at a boundary
MAX_CHARS = 2000  # paragraphs longer than this are windowed
MIN_CODE_CHARS = 200  # smaller fenced blocks are inline snippets: part of the text
WINDOW = 1000
OVERLAP = 150
COMMENTS_AFTER = 2000  # characters of document before a comment section

KINDS = (
    "text",
    "table",
    "figure",
    "code",
    "formula",
    "reference",
    "ask",
    "ad",
    "comment",
    "ingredients",
)

_PAGE_MARK = re.compile(r"^--- end of page\.page_number=(\d+) ---\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_FIGURE = re.compile(
    r"^(\**(Fig\.?|Figure)\s*\d+|!\[[^\]\n]*\]\(figure:)", re.IGNORECASE
)
_FIGURE_REF = re.compile(
    r"^!\[(?P<alt>[^\]\n]*)\]\(figure:(?P<ref>[0-9a-f]{16,64})\)", re.MULTILINE
)
_READ_BY = re.compile(
    r"^\*(?:Figure|Formula), as read by (?P<model>.+?):\* ?(?P<text>.*)$",
    re.MULTILINE,
)
# A display equation on a line of its own, as a parser that reads maths
# writes it: `$$ x = \frac{a}{b}, \quad (4) $$`. Inline maths stays in the
# sentence it belongs to — a chunk is a region of the artifact, and an
# inline formula cannot be one without tearing the text around it.
_FORMULA = re.compile(r"^\$\$(?P<latex>.+)\$\$$", re.DOTALL)
# the equation number a paper refers to it by, at the end: "\quad (4)"
_EQ_NUMBER = re.compile(r"\\(?:quad|qquad|hfill|tag)\s*\{?\(?(\d{1,3}[a-z]?)\)?\}?\s*$")
# What makes a line of maths a formula rather than a stray symbol a parser
# lifted out of a diagram (`$$\rightarrow K$$`): it states a relation, or it
# is long enough to be an expression in its own right. "E = mc^2" passes on
# the first, a displayed integral with no relation on the second.
_RELATION = re.compile(
    r"=|\\le\b|\\ge\b|\\leq\b|\\geq\b|<|>|\\approx|\\equiv|\\sim\b|\\propto"
)
FORMULA_CHARS = 40  # a relationless expression this long is still a formula

_TABLE_CAPTION = re.compile(r"^\**(Table|TABLE)\s*\d+")
# a transcript's time marks: "[12:34] the words", "[1:02:34] ...", and a
# frame's caption "12:34 — the words" (the brackets keep a paragraph that
# happens to open with a clock time out of it)
_TIME_PARA = re.compile(r"^\[(?P<t>(?:\d{1,2}:)?\d{1,2}:\d{2})\]\s")
_TIME_FIGURE = re.compile(r"^!\[(?P<t>(?:\d{1,2}:)?\d{1,2}:\d{2})\s*[—–-]\s")


def parse_time(mark: str) -> int:
    """``"12:34"`` → 754, ``"1:02:34"`` → 3754."""
    parts = [int(p) for p in mark.split(":")]
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def format_time(seconds: int) -> str:
    """754 → ``"12:34"``, 3754 → ``"1:02:34"``: the marks as the text has them."""
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _time_of(kind: str, text: str) -> int | None:
    s = text.lstrip()
    m = _TIME_PARA.match(s) if kind == "para" else _TIME_FIGURE.match(s)
    return parse_time(m.group("t")) if m else None


_EMPHASIS = re.compile(r"[*_`]+")
_FENCE = "```"


@dataclass
class Chunk:
    kind: str
    text: str
    char_start: int
    char_end: int
    page: int | None = None
    heading: list[str] = field(default_factory=list)
    data: dict[str, Any] | None = None
    time: int | None = None  # seconds into a recording (a transcript's mark)
    time_end: int | None = None  # where the next timed chunk starts

    def locator(self) -> dict[str, Any]:
        loc: dict[str, Any] = {"char_start": self.char_start, "char_end": self.char_end}
        if self.page is not None:
            loc["page"] = self.page
        if self.time is not None:
            loc["time"] = self.time
            if self.time_end is not None:
                loc["time_end"] = self.time_end
        return loc


@dataclass
class _Element:
    kind: str  # heading | table | code | figure | para | page | ask | blank
    start: int
    end: int
    text: str
    level: int = 0
    page: int | None = None
    time: int | None = None
    block: blocks.Block | None = None  # an ask element's block


# ---------------------------------------------------------------- parsing


def _lines_with_offsets(text: str) -> list[tuple[int, int, str]]:
    out = []
    pos = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        out.append((pos, pos + len(body), body))
        pos += len(line)
    return out


def _ask_regions(text: str) -> dict[int, tuple[int, blocks.Block]]:
    """The ask blocks of a page by the offset of their head line: each
    one is a single element from its head to its tail (the head alone
    when unfilled), the line break after it left out like any element's."""
    out: dict[int, tuple[int, blocks.Block]] = {}
    for b in blocks.blocks(text):
        end = b.tail[1] if b.tail is not None else b.head[1]
        while end > b.head[0] and text[end - 1] in "\r\n":
            end -= 1
        out[b.head[0]] = (end, b)
    return out


def _elements(text: str) -> list[_Element]:
    lines = _lines_with_offsets(text)
    els: list[_Element] = []
    asks = _ask_regions(text)
    i = 0
    n = len(lines)
    while i < n:
        start, end, body = lines[i]
        stripped = body.strip()
        if not stripped:
            i += 1
            continue
        if start in asks:
            aend, block = asks[start]
            els.append(_Element("ask", start, aend, text[start:aend], block=block))
            while i < n and lines[i][0] < aend:
                i += 1
            continue
        m = _PAGE_MARK.match(stripped)
        if m:
            els.append(_Element("page", start, end, body, page=int(m.group(1))))
            i += 1
            continue
        if stripped.startswith(_FENCE):
            j = i + 1
            while j < n and not lines[j][2].strip().startswith(_FENCE):
                j += 1
            j = min(j, n - 1)
            els.append(_Element("code", start, lines[j][1], text[start : lines[j][1]]))
            i = j + 1
            continue
        m = _HEADING.match(stripped)
        if m:
            title = _EMPHASIS.sub("", m.group(2)).strip()
            els.append(_Element("heading", start, end, title, level=len(m.group(1))))
            i += 1
            continue
        if _is_formula(stripped):
            j = i + 1
            while j < n and _READ_BY.match(lines[j][2].strip()):
                j += 1
            els.append(
                _Element(
                    "formula", start, lines[j - 1][1], text[start : lines[j - 1][1]]
                )
            )
            i = j
            continue
        if (
            stripped.startswith("|")
            and i + 1 < n
            and _TABLE_SEP.match(lines[i + 1][2].strip())
        ):
            j = i
            while j < n and lines[j][2].strip().startswith("|"):
                j += 1
            els.append(
                _Element("table", start, lines[j - 1][1], text[start : lines[j - 1][1]])
            )
            i = j
            continue
        # paragraph: consecutive non-blank lines that are not structural
        j = i
        while j < n:
            s = lines[j][2].strip()
            if (
                not s
                or _PAGE_MARK.match(s)
                or _HEADING.match(s)
                or s.startswith(_FENCE)
                or lines[j][0] in asks
            ):
                break
            if (
                s.startswith("|")
                and j + 1 < n
                and _TABLE_SEP.match(lines[j + 1][2].strip())
            ):
                break
            if j > i and _is_formula(s):
                break
            j += 1
        pend = lines[j - 1][1]
        ptext = text[start:pend]
        kind = "figure" if _FIGURE.match(stripped) else "para"
        els.append(_Element(kind, start, pend, ptext, time=_time_of(kind, ptext)))
        i = j
    _assign_pages(els)
    return els


def _assign_pages(els: list[_Element]) -> None:
    """Stamp every content element with its page: the markers say
    ``end of page N``, so what precedes the first marker is page 1 and what
    follows marker N is N+1. Without markers pages stay None."""
    markers = [e for e in els if e.kind == "page"]
    if not markers:
        return
    current = markers[0].page or 1  # text before "end of page N" is page N
    for e in els:
        if e.kind == "page":
            current = (e.page or 0) + 1
        else:
            e.page = current


def _is_formula(stripped: str) -> bool:
    """A line that is one display equation and nothing else."""
    if not (
        stripped.startswith("$$")
        and stripped.endswith("$$")
        and stripped.count("$$") == 2
    ):
        return False
    latex = stripped[2:-2].strip()
    return bool(_RELATION.search(latex)) or len(latex) >= FORMULA_CHARS


def parse_formula(text: str) -> dict[str, Any] | None:
    """A formula chunk's LaTeX, the number the paper refers to it by and
    any readings: ``{"latex", "number", "readings": [{"model", "text"}]}``.
    The reading is a model's plain-language account of the equation,
    written under it the way a figure's is, because the LaTeX itself
    embeds to noise and a person searching says "Shockley's diode
    equation", not ``\\frac{a-b}{2R}``."""
    lines = text.split("\n")
    m = _FORMULA.match(lines[0].strip())
    if not m:
        return None
    latex = m.group("latex").strip()
    number = None
    found = _EQ_NUMBER.search(latex)
    if found:
        number = found.group(1)
        latex = latex[: found.start()].rstrip().rstrip(",.").rstrip()
    return {
        "latex": latex,
        "number": number,
        "readings": [
            {"model": r.group("model").strip(), "text": r.group("text").strip()}
            for r in _READ_BY.finditer(text)
        ],
    }


def parse_figure(text: str) -> dict[str, Any] | None:
    """A figure chunk's reference, caption and readings, when it has an
    image line: ``{"ref", "caption", "readings": [{"model", "text"}]}``."""
    m = _FIGURE_REF.search(text)
    if not m:
        return None
    return {
        "ref": m.group("ref"),
        "caption": m.group("alt"),
        "readings": [
            {"model": r.group("model").strip(), "text": r.group("text").strip()}
            for r in _READ_BY.finditer(text)
        ],
    }


def ask_data(block: blocks.Block, text: str) -> dict[str, Any]:
    """What an ask chunk carries: the block's id and question, its
    options, when and by which pass it was last filled, whether it is
    filled at all and whether a hand has been in it since."""
    data: dict[str, Any] = {
        "id": block.id,
        "question": block.question,
        "filled": block.filled,
        "held": blocks.held(block, text),
    }
    if block.options:
        data["options"] = dict(block.options)
    for k in ("asked", "run", "sha"):
        if block.tail_attrs.get(k):
            data[k] = block.tail_attrs[k]
    return data


def parse_reference(text: str) -> dict[str, Any]:
    """What a reference entry names, for a chunk's ``data``: number,
    surnames, year, title, a printed DOI or arXiv id (each left out when
    absent). The match to a library document (``doc_id``, ``score``,
    ``how``) is the ``references`` pass's to add."""
    ref = references.parse(text)
    data: dict[str, Any] = {}
    if ref.number is not None:
        data["number"] = ref.number
    if ref.surnames:
        data["surnames"] = ref.surnames
    if ref.year:
        data["year"] = ref.year
    if ref.title:
        data["title"] = ref.title
    if ref.doi:
        data["doi"] = ref.doi
    if ref.arxiv:
        data["arxiv"] = ref.arxiv
    return data


def parse_table(markdown: str) -> dict[str, Any]:
    """A pipe table → ``{"header": [...], "rows": [[...], ...]}``."""
    rows: list[list[str]] = []
    for line in markdown.splitlines():
        s = line.strip()
        if not s.startswith("|") or _TABLE_SEP.match(s):
            continue
        cells = [_EMPHASIS.sub("", c).strip() for c in s.strip("|").split("|")]
        rows.append(cells)
    if not rows:
        return {"header": [], "rows": []}
    return {"header": rows[0], "rows": rows[1:]}


# --------------------------------------------------------------- regions


def _comment_region(els: list[_Element]) -> tuple[int, int] | None:
    """The comment section: its heading to the end of the document.

    Only to the end, and only under a document of some size. What a
    page's readers wrote is the last thing on it, which is where the
    HTML parser puts it. A heading called "Comments" with the document
    carrying on under it is the document's own (a paper's remarks, a
    tutorial on writing comments), and so is one that arrives before
    the document has said anything (an assessment whose last line is
    the examiner's comment).
    """
    for i, el in enumerate(els):
        if el.kind != "heading" or not furniture.is_comment_heading(el.text):
            continue
        if el.start < COMMENTS_AFTER:
            continue
        if any(e.kind == "heading" and e.level <= el.level for e in els[i + 1 :]):
            continue
        return i, len(els) - 1
    return None


def _ingredient_regions(els: list[_Element], taken: set[int]) -> list[tuple[int, int]]:
    """Each ingredient list: its heading, the line that says for how many,
    the lists, and the small headings that group them. It ends at the
    first piece that is prose."""
    out = []
    for i, el in enumerate(els):
        if i in taken or el.kind != "heading" or not ingredients.is_heading(el.text):
            continue
        last, seen = i, False
        for j in range(i + 1, len(els)):
            nxt = els[j]
            if nxt.kind == "heading" and nxt.level > el.level:
                last = j
                continue
            if nxt.kind == "para" and ingredients.is_list(nxt.text):
                last, seen = j, True
                continue
            if nxt.kind == "page":  # a page break inside the list
                continue
            break
        if seen:
            out.append((i, last))
            taken.update(range(i, last + 1))
    return out


def _regions(els: list[_Element]) -> dict[int, tuple[str, int, dict[str, Any] | None]]:
    """``{first element: (kind, last element, data)}`` for the regions a
    single chunk takes whole. The comment section is found first and the
    rest are looked for outside it: an advertisement under a comment is
    the commenter's business."""
    out: dict[int, tuple[str, int, dict[str, Any] | None]] = {}
    taken: set[int] = set()
    comments = _comment_region(els)
    if comments:
        first, last = comments
        out[first] = ("comment", last, None)
        taken.update(range(first, last + 1))
    pieces = ["" if i in taken else el.text for i, el in enumerate(els)]
    for first, last, data in furniture.ad_runs(pieces):
        out[first] = ("ad", last, data)
        taken.update(range(first, last + 1))
    for first, last in _ingredient_regions(els, taken):
        out[first] = ("ingredients", last, None)  # parsed from the text below
    return out


# --------------------------------------------------------------- windows


def windows(
    text: str, size: int = WINDOW, overlap: int = OVERLAP
) -> list[tuple[int, int]]:
    """Fixed overlapping windows as (start, end) offsets; the Stage 0 chunker."""
    if not text:
        return []
    out = []
    start = 0
    step = size - overlap
    while True:
        end = min(len(text), start + size)
        out.append((start, end))
        if end >= len(text):
            return out
        start += step


# --------------------------------------------------------------- chunking


def chunk(text: str) -> list[Chunk]:
    """Split a text artifact into structure-aware chunks (see module doc)."""
    chunks: list[Chunk] = []
    heading: list[tuple[int, str]] = []  # (level, title) stack
    pending: list[_Element] = []  # paragraphs of the text chunk being built

    def path() -> list[str]:
        return [t for _, t in heading]

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        start, end = pending[0].start, pending[-1].end
        page = pending[0].page  # a chunk may span pages; it is filed under its first
        # and under its first time mark, when its paragraphs carry any
        time = next((e.time for e in pending if e.time is not None), None)
        body = text[start:end]
        if len(body) > MAX_CHARS and len(pending) == 1:
            for ws, we in windows(body):
                chunks.append(
                    Chunk(
                        "text",
                        body[ws:we],
                        start + ws,
                        start + we,
                        page,
                        path(),
                        time=time,
                    )
                )
        else:
            chunks.append(Chunk("text", body, start, end, page, path(), time=time))
        pending = []

    def maybe_merge_small_tail() -> None:
        """A tiny trailing text chunk at a boundary joins its predecessor when
        both are plain text under the same heading and the same page."""
        if len(chunks) >= 2:
            a, b = chunks[-2], chunks[-1]
            if (
                a.kind == b.kind == "text"
                and a.heading == b.heading
                and len(b.text) < MIN_CHARS
                and len(a.text) + len(b.text) <= MAX_CHARS
            ):
                chunks[-2] = Chunk(
                    "text",
                    text[a.char_start : b.char_end],
                    a.char_start,
                    b.char_end,
                    a.page,
                    a.heading,
                    time=a.time if a.time is not None else b.time,
                )
                chunks.pop()

    els = _elements(text)
    regions = _regions(els)
    skip_to = 0
    for idx, el in enumerate(els):
        if idx < skip_to:
            continue
        region = regions.get(idx)
        if region is not None:
            kind, last, data = region
            flush()
            maybe_merge_small_tail()
            start, end = el.start, els[last].end
            body = text[start:end]
            if kind == "ingredients":
                data = ingredients.parse(body)
            chunks.append(
                Chunk(kind, body, start, end, el.page, path(), data, time=el.time)
            )
            skip_to = last + 1
            continue
        if el.kind == "page":
            # a substantial chunk ends with its page; a small one (a running
            # header, a sentence cut by the break) carries on into the next
            if pending and pending[-1].end - pending[0].start >= MIN_CHARS:
                flush()
                maybe_merge_small_tail()
            continue
        if el.kind == "code" and len(el.text) < MIN_CODE_CHARS:
            el = _Element("para", el.start, el.end, el.text, page=el.page)
            el.time = _time_of("para", el.text)
        if el.kind == "heading":
            flush()
            maybe_merge_small_tail()
            while heading and heading[-1][0] >= el.level:
                heading.pop()
            heading.append((el.level, el.text))
            continue
        if el.kind == "ask":
            flush()
            maybe_merge_small_tail()
            assert el.block is not None
            chunks.append(
                Chunk(
                    "ask",
                    text[el.start : el.end],
                    el.start,
                    el.end,
                    el.page,
                    path(),
                    ask_data(el.block, text),
                )
            )
            continue
        if (
            el.kind == "para"
            and heading
            and references.is_bibliography_heading(heading[-1][1])
        ):
            # the reference list: one chunk per entry, over the artifact's
            # own characters; a piece that opens no entry continues the
            # chunk before it (a wrapped title), or is text before the first
            spans = references.entry_spans(el.text)
            if spans and not spans[0][2] and chunks and chunks[-1].kind == "reference":
                last = chunks[-1]
                a, b, _ = spans[0]
                chunks[-1] = Chunk(
                    "reference",
                    text[last.char_start : el.start + b],
                    last.char_start,
                    el.start + b,
                    last.page,
                    last.heading,
                    parse_reference(text[last.char_start : el.start + b]),
                )
                spans = spans[1:]
            if spans and not spans[0][2]:
                pending.append(el)  # prose under the heading, before any entry
                continue
            flush()
            for a, b, _ in spans:
                start, end = el.start + a, el.start + b
                chunks.append(
                    Chunk(
                        "reference",
                        text[start:end],
                        start,
                        end,
                        el.page,
                        path(),
                        parse_reference(text[start:end]),
                    )
                )
            continue
        if el.kind == "para":
            if (
                pending
                and (pending[-1].end - pending[0].start) + len(el.text) > TARGET_CHARS
            ):
                flush()
            # a "Table N" caption right before a table belongs to the table
            nxt = els[idx + 1] if idx + 1 < len(els) else None
            if (
                nxt is not None
                and nxt.kind == "table"
                and _TABLE_CAPTION.match(el.text.strip())
            ):
                flush()
                pending = [el]  # picked up by the table branch
                continue
            pending.append(el)
            continue
        if el.kind == "table":
            caption_before = (
                pending
                if pending
                and _TABLE_CAPTION.match(pending[0].text.strip())
                and len(pending) == 1
                else []
            )
            if not caption_before:
                flush()
            start = caption_before[0].start if caption_before else el.start
            end = el.end
            pending = []
            nxt = els[idx + 1] if idx + 1 < len(els) else None
            if (
                nxt is not None
                and nxt.kind == "para"
                and _TABLE_CAPTION.match(nxt.text.strip())
            ):
                end = nxt.end
                els[idx + 1] = _Element("blank", nxt.start, nxt.end, "")  # consumed
            chunks.append(
                Chunk(
                    "table",
                    text[start:end],
                    start,
                    end,
                    el.page,
                    path(),
                    parse_table(el.text),
                )
            )
            continue
        if el.kind in ("figure", "code", "formula"):
            flush()
            data = None
            if el.kind == "figure":
                data = parse_figure(el.text)
            elif el.kind == "formula":
                data = parse_formula(el.text)
            chunks.append(
                Chunk(
                    el.kind,
                    text[el.start : el.end],
                    el.start,
                    el.end,
                    el.page,
                    path(),
                    data,
                    time=el.time,
                )
            )
            continue
        # blank (consumed caption): nothing
    flush()
    maybe_merge_small_tail()
    _assign_time_ends(chunks)
    for c in chunks:
        assert c.text == text[c.char_start : c.char_end]
    return chunks


def _assign_time_ends(chunks: list[Chunk]) -> None:
    """A timed chunk ends where the next timed one begins (a frame at the
    same moment as its paragraph does not end it: the next later mark does)."""
    timed = [c for c in chunks if c.time is not None]
    for i, c in enumerate(timed):
        later = next((d.time for d in timed[i + 1 :] if d.time > c.time), None)
        if later is not None:
            c.time_end = later


def rows(chunks: list[Chunk]) -> list[tuple[str, str, str, str, str | None]]:
    """``(text, kind, locator_json, heading_json, data_json)`` per chunk."""
    return [
        (
            c.text,
            c.kind,
            json.dumps(c.locator()),
            json.dumps(c.heading),
            json.dumps(c.data) if c.data is not None else None,
        )
        for c in chunks
    ]
