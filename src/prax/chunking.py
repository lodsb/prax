"""Structure-aware chunking of a text artifact.

The text artifact is Markdown (what every extractor produces; plain text is
degenerate Markdown). It is parsed into elements — headings, pipe tables,
fenced code, figure captions, paragraphs, page markers — and grouped into
chunks:

* one chunk per **table**, with an adjacent "Table N" caption folded in and
  the parsed grid in ``data``;
* one chunk per **figure** caption ("Figure N …", "Fig. N …");
* one chunk per fenced **code** block;
* **text** chunks of consecutive paragraphs under the same heading path, up
  to ``TARGET_CHARS``; a paragraph longer than ``MAX_CHARS`` falls back to
  overlapping fixed windows.

Every chunk carries a locator ``{"char_start", "char_end", "page"?}`` into
the artifact, and the invariant ``chunk.text == text[char_start:char_end]``
holds for every chunk, so ``get`` can return exactly what search matched.
Page numbers come from the ``--- end of page.page_number=N ---`` markers
pymupdf4llm emits; the markers themselves belong to no chunk.

Chunks are disposable (rationale R3): change this module, run
``scripts/rechunk.py``, nothing else moves.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

TARGET_CHARS = 1200  # flush a text chunk when the next paragraph would exceed this
MIN_CHARS = 300  # merge into the previous chunk when smaller than this at a boundary
MAX_CHARS = 2000  # paragraphs longer than this are windowed
MIN_CODE_CHARS = 200  # smaller fenced blocks are inline snippets: part of the text
WINDOW = 1000
OVERLAP = 150

KINDS = ("text", "table", "figure", "code")

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
    r"^\*Figure, as read by (?P<model>.+?):\* ?(?P<text>.*)$", re.MULTILINE
)
_TABLE_CAPTION = re.compile(r"^\**(Table|TABLE)\s*\d+")
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

    def locator(self) -> dict[str, Any]:
        loc: dict[str, Any] = {"char_start": self.char_start, "char_end": self.char_end}
        if self.page is not None:
            loc["page"] = self.page
        return loc


@dataclass
class _Element:
    kind: str  # heading | table | code | figure | para | page | blank
    start: int
    end: int
    text: str
    level: int = 0
    page: int | None = None


# ---------------------------------------------------------------- parsing


def _lines_with_offsets(text: str) -> list[tuple[int, int, str]]:
    out = []
    pos = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        out.append((pos, pos + len(body), body))
        pos += len(line)
    return out


def _elements(text: str) -> list[_Element]:
    lines = _lines_with_offsets(text)
    els: list[_Element] = []
    i = 0
    n = len(lines)
    while i < n:
        start, end, body = lines[i]
        stripped = body.strip()
        if not stripped:
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
            ):
                break
            if (
                s.startswith("|")
                and j + 1 < n
                and _TABLE_SEP.match(lines[j + 1][2].strip())
            ):
                break
            j += 1
        pend = lines[j - 1][1]
        ptext = text[start:pend]
        kind = "figure" if _FIGURE.match(stripped) else "para"
        els.append(_Element(kind, start, pend, ptext))
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
        body = text[start:end]
        if len(body) > MAX_CHARS and len(pending) == 1:
            for ws, we in windows(body):
                chunks.append(
                    Chunk("text", body[ws:we], start + ws, start + we, page, path())
                )
        else:
            chunks.append(Chunk("text", body, start, end, page, path()))
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
                )
                chunks.pop()

    els = _elements(text)
    for idx, el in enumerate(els):
        if el.kind == "page":
            # a substantial chunk ends with its page; a small one (a running
            # header, a sentence cut by the break) carries on into the next
            if pending and pending[-1].end - pending[0].start >= MIN_CHARS:
                flush()
                maybe_merge_small_tail()
            continue
        if el.kind == "code" and len(el.text) < MIN_CODE_CHARS:
            el = _Element("para", el.start, el.end, el.text, page=el.page)
        if el.kind == "heading":
            flush()
            maybe_merge_small_tail()
            while heading and heading[-1][0] >= el.level:
                heading.pop()
            heading.append((el.level, el.text))
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
        if el.kind in ("figure", "code"):
            flush()
            chunks.append(
                Chunk(
                    el.kind,
                    text[el.start : el.end],
                    el.start,
                    el.end,
                    el.page,
                    path(),
                    parse_figure(el.text) if el.kind == "figure" else None,
                )
            )
            continue
        # blank (consumed caption): nothing
    flush()
    maybe_merge_small_tail()
    for c in chunks:
        assert c.text == text[c.char_start : c.char_end]
    return chunks


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
