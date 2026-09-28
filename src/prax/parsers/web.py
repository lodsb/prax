"""A captured web page: trafilatura's article Markdown with its comment
section, and the extension's video capture.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util

from prax import config
from prax.parsers import figures
from prax.text import markup

from .base import ExtractionError


def _comments_wanted() -> bool:
    """``parse.comments`` [``PRAX_COMMENTS``]: a page's comment section
    kept under its own heading (on by default: a thread under an article
    is where the corrections and the jokes are)."""

    value = str(config.setting("parse.comments", "PRAX_COMMENTS", "true")).lower()
    return value not in ("0", "false", "no", "off")


def _video(data: bytes) -> str:
    from prax.parsers import video

    try:
        return video.parse(data)
    except video.NotATranscript as exc:
        raise ExtractionError(str(exc)) from exc


def _trafilatura_variant() -> str:
    return "" if _comments_wanted() else "nocomments"


COMMENTS_HEADING = markup.COMMENTS_HEADING


def _trafilatura(data: bytes) -> str:
    trafilatura = importlib.import_module("trafilatura")
    text = trafilatura.extract(
        data,
        output_format="markdown",  # headings, lists and fenced code blocks
        include_tables=True,
        include_links=False,
        include_comments=False,
    )
    if not text:
        raise ExtractionError("trafilatura found no main content")
    meta = trafilatura.extract_metadata(data)
    title = getattr(meta, "title", None) if meta else None
    text = f"# {title}\n\n{text}" if title and title not in text[:200] else text
    if _comments_wanted():
        # the comment section, when the snapshot has one: its own heading,
        # so every comment is a chunk under "Comments" and the article's
        # own text stays what a search hit or an extraction reads first
        try:
            bare = trafilatura.bare_extraction(data, include_comments=True)
        except Exception:  # noqa: BLE001 - the article is what matters
            bare = None
        comments = (getattr(bare, "comments", None) or "").strip() if bare else ""
        first, _, rest = comments.partition("\n")
        if first.strip().lower() in ("comments", "comment", "responses", "replies"):
            comments = rest.strip()  # the section's own heading: ours says it
        if comments:
            text = f"{text.rstrip()}\n\n{COMMENTS_HEADING}\n\n{comments}\n"
    return figures.place(text, figures.html_figures(data))
