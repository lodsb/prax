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

from prax.chunking import format_time
from prax.parsers import figures

META_NAME = "prax-video"
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
    meta_el = root.find('.//meta[@name="prax-video"]')
    try:
        meta = json.loads(meta_el.get("content") or "{}") if meta_el is not None else {}
    except json.JSONDecodeError:
        meta = {}
    h1 = root.find(".//h1")
    title_el = root.find(".//title")
    title = (
        _text(h1)
        if h1 is not None
        else (_text(title_el) if title_el is not None else "")
    )
    out: list[str] = []
    if title:
        out.append(f"# {title}")
        out.append("")
    byline = [str(meta[k]) for k in ("channel", "published") if meta.get(k)]
    if meta.get("duration"):
        byline.append(format_time(int(meta["duration"])))
    if meta.get("url"):
        byline.append(str(meta["url"]))
    if byline:
        out.append(" · ".join(byline))
        out.append("")
    desc = root.find('.//section[@class="description"]')
    if desc is not None:
        paras = [_text(p) for p in desc.iter("p")]
        paras = [p for p in paras if p]
        if paras:
            out.append("## Description")
            out.append("")
            for p in paras:
                out.append(p)
                out.append("")
    transcript = root.find('.//section[@class="transcript"]')
    if transcript is None:
        raise NotATranscript("a video capture without a transcript section")
    out.append("## Transcript")
    out.append("")
    chapters = sorted(
        (
            (int(c.get("t", 0)), str(c.get("title") or "").strip())
            for c in (meta.get("chapters") or [])
            if isinstance(c, dict) and str(c.get("title") or "").strip()
        ),
        key=lambda c: c[0],
    )
    next_chapter = 0
    seen: set[str] = set()
    for el in transcript:
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag not in ("p", "figure"):
            continue
        try:
            t = int(float(el.get("data-t") or 0))
        except ValueError:
            t = 0
        while next_chapter < len(chapters) and chapters[next_chapter][0] <= t:
            out.append(f"### {chapters[next_chapter][1]}")
            out.append("")
            next_chapter += 1
        mark = format_time(t)
        if tag == "p":
            words = _text(el)
            # the time link's own text, when the extension wrote one first
            if words.startswith(mark):
                words = words[len(mark) :].strip()
            if words:
                out.append(f"[{mark}] {words}")
                out.append("")
            continue
        img = el.find(".//img")
        found = figures._data_url(img.get("src") or "") if img is not None else None
        if found is None:
            continue
        blob, media = found
        ref = figures.sha(blob)
        if ref in seen:
            continue
        seen.add(ref)
        cap = el.find(".//figcaption")
        caption = _text(cap) if cap is not None else ""
        if caption.startswith(mark):
            caption = caption[len(mark) :].lstrip(" —–-")
        alt = f"{mark} — {caption}" if caption else f"{mark} — frame"
        out.append(figures.line_for(figures.Figure(ref, blob, media, alt)))
        out.append("")
    return "\n".join(out).rstrip() + "\n"
