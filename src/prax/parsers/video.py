"""A video as the extension captures it: transcript, frames, and where.

The browser extension turns a watch page into one HTML document of a
shape that is ours — no article to find, so no trafilatura — and the
door parses it exactly:

.. code-block:: html

    <meta name="prax-video" content='{"provider":"youtube","id":"…",
          "url":"…","channel":"…","duration":3600,"published":"2024-01-01",
          "captions":"asr","chapters":[{"t":0,"title":"Intro"}]}'>
    <article class="prax-video">
      <h1>The title</h1>
      <section class="description"><p>…</p></section>
      <section class="transcript">
        <figure data-t="754"><img src="data:image/jpeg;base64,…">
          <figcaption>12:34 — the words spoken there</figcaption></figure>
        <p data-t="754"><a href="…&t=754s">12:34</a> the words spoken there …</p>
      </section>
    </article>

What comes out is Markdown the chunker already understands: a paragraph
that opens ``[12:34]`` and a frame whose caption opens ``12:34 —`` carry
their moment into the chunk's locator (``time``, ``time_end``); the
frames are figures by hash, served out of this original like any
snapshot's images, and the figures pass reads them with the vision
model — a talk's slides become text. Chapters become headings, so a
passage is filed under "Transcript › Chapter". The player itself is not
here: it is the document's ``meta.video``, which the UI renders live;
this original stays self-contained and readable offline.
"""

from __future__ import annotations

import json
import re
from typing import Any

from prax.parsers import figures
from prax.text.chunking import format_time

_META = re.compile(rb'<meta\s+name="prax-video"', re.IGNORECASE)


class NotATranscript(ValueError):
    """The HTML is not one of ours."""


def is_transcript(data: bytes) -> bool:
    """Whether these bytes are a video capture of ours (the meta in the
    head; the first 8 KB are enough)."""
    return _META.search(data[:8192]) is not None


def _text(el: Any) -> str:
    return " ".join(el.text_content().split())


def parse(data: bytes) -> str:
    """The Markdown of a video capture, or :class:`NotATranscript`."""
    if not is_transcript(data):
        raise NotATranscript("no prax-video meta: not a video capture")
    from lxml import html as lxml_html

    root = lxml_html.fromstring(data.decode("utf-8", errors="replace"))
    meta = _meta(root)
    out = _head(root, meta) + _description(root)
    transcript = root.find('.//section[@class="transcript"]')
    if transcript is None:
        raise NotATranscript("a video capture without a transcript section")
    out += ["## Transcript", ""] + _transcript(transcript, _chapters(meta))
    return "\n".join(out).rstrip() + "\n"


def _meta(root: Any) -> dict[str, Any]:
    """The capture's ``prax-video`` meta, ``{}`` when it does not parse."""
    el = root.find('.//meta[@name="prax-video"]')
    try:
        got = json.loads(el.get("content") or "{}") if el is not None else {}
    except json.JSONDecodeError:
        return {}
    return got if isinstance(got, dict) else {}


def _head(root: Any, meta: dict[str, Any]) -> list[str]:
    """The title (the ``h1``, else the ``title``) and the byline: channel,
    date, duration, address."""
    h1 = root.find(".//h1")
    title_el = root.find(".//title")
    title = (
        _text(h1)
        if h1 is not None
        else (_text(title_el) if title_el is not None else "")
    )
    out = [f"# {title}", ""] if title else []
    byline = [str(meta[k]) for k in ("channel", "published") if meta.get(k)]
    if meta.get("duration"):
        byline.append(format_time(int(meta["duration"])))
    if meta.get("url"):
        byline.append(str(meta["url"]))
    if byline:
        out += [" · ".join(byline), ""]
    return out


def _description(root: Any) -> list[str]:
    """The description's paragraphs under a heading of their own."""
    desc = root.find('.//section[@class="description"]')
    paras = (
        [p for p in (_text(p) for p in desc.iter("p")) if p] if desc is not None else []
    )
    if not paras:
        return []
    out = ["## Description", ""]
    for p in paras:
        out += [p, ""]
    return out


def _chapters(meta: dict[str, Any]) -> list[tuple[int, str]]:
    """The chapters the meta names, ``(second, title)`` in order."""
    return sorted(
        (
            (int(c.get("t", 0)), str(c.get("title") or "").strip())
            for c in (meta.get("chapters") or [])
            if isinstance(c, dict) and str(c.get("title") or "").strip()
        ),
        key=lambda c: c[0],
    )


def _transcript(transcript: Any, chapters: list[tuple[int, str]]) -> list[str]:
    """The transcript's paragraphs and frames in order, each opening with
    its moment, and a chapter's heading before the first line at or past
    its start."""
    out: list[str] = []
    next_chapter = 0
    seen: set[str] = set()  # a frame repeated is filed once
    for el in transcript:
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag not in ("p", "figure"):
            continue
        try:
            t = int(float(el.get("data-t") or 0))
        except ValueError:
            t = 0
        while next_chapter < len(chapters) and chapters[next_chapter][0] <= t:
            out += [f"### {chapters[next_chapter][1]}", ""]
            next_chapter += 1
        mark = format_time(t)
        line = _spoken(el, mark) if tag == "p" else _frame(el, mark, seen)
        if line:
            out += [line, ""]
    return out


def _spoken(el: Any, mark: str) -> str | None:
    """A paragraph of the transcript as ``[12:34] words``; None when empty."""
    words = _text(el)
    # the time link's own text, when the extension wrote one first
    if words.startswith(mark):
        words = words[len(mark) :].strip()
    return f"[{mark}] {words}" if words else None


def _frame(el: Any, mark: str, seen: set[str]) -> str | None:
    """A frame as a figure line by hash, its caption opening with the
    moment; None when it holds no picture or one already filed."""
    img = el.find(".//img")
    found = figures._data_url(img.get("src") or "") if img is not None else None
    if found is None:
        return None
    blob, media = found
    ref = figures.sha(blob)
    if ref in seen:
        return None
    seen.add(ref)
    cap = el.find(".//figcaption")
    caption = _text(cap) if cap is not None else ""
    if caption.startswith(mark):
        caption = caption[len(mark) :].lstrip(" —–-")
    alt = f"{mark} — {caption}" if caption else f"{mark} — frame"
    return figures.line_for(figures.Figure(ref, blob, media, alt))
