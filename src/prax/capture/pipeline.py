"""What the steps ask of a capture's life after it arrives: which
documents want a title or a summary in the library's language
(``titles_needed``, ``summaries_needed``), and which reading the door asks
for next once a parse has landed (``follow_ups``), only from a model that
costs nothing and is free.

The in-process runner that once took a capture through parse, title,
extract and embed on the door (``process_captures`` and its passes) is
gone (2026-09-30): the worker's steps (``prax.steps``) do that work,
and nothing called it but its own test.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from typing import Any

from prax import config, models, store
from prax.parsers import figures, quality
from prax.text import language
from prax.writing import summaries, titles

log = logging.getLogger("prax.pipeline")

# ---------------------------------------------------------------- titles


def file_name(doc: Mapping[str, Any]) -> str | None:
    z = (doc["meta"] or {}).get("zotero") or {}
    got: str | None = z.get("filename") or doc["title"]
    return got


def summaries_needed(
    con: sqlite3.Connection,
    *,
    ids: list[int] | None = None,
    untried_only: bool = False,
) -> list[tuple[int, str]]:
    """``(doc_id, lang)`` for the documents whose summary is not in the
    language the document field is written in.

    The language is read from ``meta.summary_lang``, which the extraction
    writes and the ``languages`` pass of ``prax maintain`` fills in for
    the summaries written before it existed. A summary nobody has
    detected yet is not handed out: detecting it is free and belongs to
    that pass, not to a model call.

    A translation already made but not good enough to keep is asked for
    again, from the original ``meta.summaries`` held on to. That is what
    makes the check worth tightening: a model that handed the prompt's
    labels back with the answer (2026-09-24) is corrected by improving
    ``summaries.acceptable`` and running the pass, not by repairing rows.
    It cannot loop — a second refusal is the worker's, which writes
    ``meta.summary_tried``.

    With ``untried_only`` the documents a translation already failed on
    are left out, as a guess costs a model call; a pass that wants them
    again clears ``meta.summary_tried``.
    """
    lang = language.canonical()
    # two narrow questions rather than one pass over every summary: this
    # runs on every hand-out, and a worker asks every twenty seconds
    out = store.summaries_in_other_languages(
        con, lang, untried_only=untried_only, ids=ids
    )
    # and the ones a translation has already been made for, which are only
    # the documents this pass has touched
    for doc_id, summary, held in store.translated_summaries(
        con, lang, untried_only=untried_only, ids=ids
    ):
        native = summaries.native(held, lang)
        if native is not None and summaries.acceptable(summary, native[1]):
            out.append((doc_id, native[0]))  # translated, but not usably
    return sorted(out)


def titles_needed(
    con: sqlite3.Connection,
    *,
    reasons: tuple[str, ...] = (
        "empty",
        "filename",
        "identifier",
        "zotero-auto",
        "caps",
    ),
    ids: list[int] | None = None,
    untried_only: bool = False,
) -> list[tuple[int, str]]:
    """``(doc_id, why)`` for the documents whose title is not one. With
    ``untried_only`` (the pipeline) documents a guess already failed on,
    and documents without text, are left out: a guess costs a model call."""
    out = []
    # a title many documents carry is the reason only where it was asked
    # for: the watched titles step asks, and names it to the model
    shared = store.shared_titles(con) if "shared" in reasons else set()
    for r in store.title_rows(con, ids):
        meta = r["meta"]
        if untried_only and (meta.get("titles_tried") or not r["text_hash"]):
            continue  # a guess that failed before, or nothing to guess from
        why = titles.needs_title(r["title"], meta)
        if (
            why is None
            and r["title"] in shared
            and meta.get("title_source") in (None, "zotero")
        ):
            why = "shared"
        if why in reasons:
            out.append((r["id"], why))
    return out


def mark_tried(con: sqlite3.Connection, doc_id: int, run: str, why: str) -> None:
    """Remember that a guess was made and not applied, so the pipeline does
    not ask the model again every pass (a later titles pass over the document
    or ``--apply-low`` still can)."""
    meta = store.get_meta(con, doc_id)
    meta["titles_tried"] = {"run": run, "why": why}
    store.set_meta(con, doc_id, meta)


# ------------------------------------------------- the readings the door asks for
# The edges of the process graph: after a parse lands, what the door asks
# the worker to read next, on its own — only from a model that costs
# nothing (a local server), never over a request still waiting, one at a
# time (a document has one reading slot; the next is placed when this one
# lands). Both roads into the library call this: a capture parsed inline
# at ingest, and a parse posted by a worker.


def _free(step: str) -> bool:
    """Whether a reading by that step's model costs nothing (a local
    server): the readings the door asks for on its own are only those."""
    try:
        spec = models.resolve(step)
    except Exception:  # noqa: BLE001 - a broken prax.yaml is not this step's problem
        return False
    return spec is not None and spec.kind == "openai"


def vision_is_free() -> bool:
    return _free("vision")


def formulas_are_free() -> bool:
    return _free("formulas")


def polish_is_free() -> bool:
    return _free("polish")


# -------------------------------------------------------- the PDF's reader
# A PDF's first text is its text layer (pymupdf4llm), which loses the
# mathematics and, for a scan, is an old OCR's. What reads it better
# depends on what it is, and the door asks for that by itself instead of a
# person asking per document (2026-10-17: thirty-four arXiv papers and
# fourteen scans put right by hand in one night):
#
# - a paper on arXiv: its LaTeX source (``latex-source``), exact and
#   without the card; marker if the source fails;
# - a scan, with an OCR layer or not: marker reading every page itself
#   (``fast-ocr``), its maths as LaTeX;
# - a born-digital PDF that sets formulas in a maths font: marker as it is.
#
# Only for what a person added (``CAPTURE_SOURCES``): an import of a
# thousand papers is a decision about the card, not a follow-up.

SCANNED = 0.5  # the share of profiled pages that makes a PDF a scan


def choose_reader_on() -> bool:
    """``parse.choose_reader`` [``PRAX_CHOOSE_READER``], on by default."""
    value = config.setting("parse.choose_reader", "PRAX_CHOOSE_READER", True)
    return str(value).lower() not in ("0", "false", "no", "off")


def _tried(meta: Mapping[str, Any]) -> set[str]:
    """The extractors that have read the document already, by name."""
    return {
        str(h.get("extractor") or "").split("/", 1)[0]
        for h in meta.get("parse_history") or []
    }


def _marker_mode(scan: bool) -> str | None:
    """The marker reading's mode: the host's layout (``parse.marker_mode``),
    with ``-ocr`` for a scan."""
    base = str(config.setting("parse.marker_mode", "PRAX_MARKER_MODE", "fast"))
    base = base.removesuffix("-ocr") if base else "fast"
    if base not in ("fast", "balanced"):
        base = "fast"
    if scan:
        return f"{base}-ocr"
    return None if base == "fast" else base


def assess(
    con: sqlite3.Connection, doc_id: int
) -> tuple[list[quality.Finding], dict[str, Any] | None]:
    """The checks of the document's current text under the stamp of what
    wrote it (``parsers.quality``, stage AO), kept in ``meta.quality``
    with the text's hash; the findings and the original's profile (None
    but for a PDF)."""
    doc = store.get_document(con, doc_id)
    if doc is None or not doc.get("text"):
        return [], None
    stamp = str((doc.get("meta") or {}).get("text_source") or "")
    prof = None
    if doc.get("mime") == "application/pdf":
        info = store.original_info(con, doc_id)
        try:
            data = info["path"].read_bytes() if info else None
        except OSError:
            data = None
        prof = quality.profile_of(
            data, mime=doc["mime"], arxiv=store.get_meta(con, doc_id).get("arxiv")
        )
    since = quality.since_text((doc.get("meta") or {}).get("parse_history"), stamp)
    found = quality.check(
        doc["text"], stamp=stamp, mime=doc.get("mime"), profile=prof, since=since
    )
    store.set_quality(
        con,
        doc_id,
        [f.as_dict() for f in found],
        version=quality.VERSION,
        text_hash=str(doc["text_hash"]),
    )
    return found, prof


def choose_reader(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    stamp: str,
    action: str,
    pending: set[str],
    found: list[quality.Finding],
    profile: dict[str, Any] | None,
) -> str | None:
    """The reading a captured PDF should have next for its text (the
    section's comment), asked for and named; None when it has what suits
    it, is not a captured PDF, or the host turned this off. What the
    checks found decides (``found``): a scan read from its OCR layer, or
    maths left as loose symbols, gets marker; a paper on arXiv its source
    first, whatever was found."""
    if not choose_reader_on() or profile is None:
        return None
    meta = store.get_meta(con, doc_id)
    if meta.get("source") not in store.CAPTURE_SOURCES:
        return None
    tried = _tried(meta)
    if {"latex-source", "marker"} & pending:
        return None  # one is on its way
    source_failed = stamp.startswith("latex-source/") and action in (
        "error",
        "kept",
        "empty",
    )
    if source_failed and str(meta.get("text_source") or "").startswith("latex-source/"):
        # a re-read of the source that failed leaves the source's own text:
        # no marker over it (the review of 2026-10-18)
        return None
    first_text = stamp.startswith("pymupdf4llm/") and action in ("created", "upgraded")
    if not (source_failed or first_text):
        return None
    if first_text and "latex-source" not in tried and profile.get("arxiv"):
        try:
            store.request_reading(
                con, doc_id, "latex-source", mode=str(profile["arxiv"]), by="door"
            )
            return "latex-source"
        except ValueError:  # an id the mode refuses costs this reading only
            log.warning("doc %d: no latex-source for %r", doc_id, profile["arxiv"])
    if "marker" in tried:
        return None
    named = quality.fixes(found)
    # a scan with no text layer at all is a scan as much as one with an OCR's
    scan = "marker-ocr" in named or (
        max(profile["scanned"], profile["ocr_layer"]) >= SCANNED
        and not profile.get("layer")
    )
    if scan or "marker" in named or "latex-source" in named or source_failed:
        store.request_reading(con, doc_id, "marker", mode=_marker_mode(scan), by="door")
        return "marker"
    return None


def follow_ups(
    con: sqlite3.Connection, doc_id: int, *, stamp: str, action: str
) -> str | None:
    """The reading the door places after a text landed under ``stamp``
    with ``action`` (``created``/``upgraded``; nothing after ``same`` or an
    error, but for a source that failed), or None. A captured PDF gets the
    reader that suits it first (``choose_reader``); a video capture with
    an automatic transcript gets the polish first and its frames read once
    that has landed; a marker read or a text from the LaTeX source with
    display equations gets the formula readings; a capture with figures
    nobody has read gets the vision model."""
    # what the document is already waiting for: since migration 21 a
    # reading queues beside the others instead of replacing them, so the
    # door only declines to ask for the same one twice
    pending = {r["extractor"] for r in store.pending_readings(con, doc_id)}
    landed = action in ("created", "upgraded")
    source_failed = stamp.startswith("latex-source/") and action in (
        "error",
        "kept",
        "empty",
    )
    if action == "same" and stamp.startswith(("figure-crops/", "formulas/")):
        # an annotating reading that found nothing to add has still run on
        # this text: checked again, its finding stops naming it (the
        # review of 2026-10-18)
        assess(con, doc_id)
        return None
    if not (landed or source_failed):
        return None
    # what the checks find in the text the document now holds: kept, and
    # what decides the reader and the crops below
    found, prof = assess(con, doc_id)
    chosen = choose_reader(
        con,
        doc_id,
        stamp=stamp,
        action=action,
        pending=pending,
        found=found,
        profile=prof,
    )
    if chosen is not None:
        return chosen
    if not landed:
        return None
    meta = store.get_meta(con, doc_id)
    video = meta.get("video") or {}
    if (
        video.get("captions") == "asr"
        and not stamp.startswith("polish/")
        and polish_is_free()
    ):
        if "polish" not in pending:
            store.request_reading(con, doc_id, "polish", by="door")
        return "polish"
    # a PDF whose captions have no picture: the crop pass renders them off
    # the page and runs no model at all, so there is nothing to be free.
    # Never after itself. Asked beside the formulas, not after them: the
    # door asks for one follow-up a text, and the formulas' own stamp never
    # led to the crops (2026-10-17: 438 of 465 figures of a week's papers
    # read from their source or by marker were a caption alone)
    crops = (
        stamp.startswith(("pymupdf4llm/", "marker/", "latex-source/", "formulas/"))
        and "figure-crops" not in pending
        and "figure-crops" in quality.fixes(found)
    )
    if crops:
        store.request_reading(con, doc_id, "figure-crops", by="door")
    if (
        stamp.startswith(("marker/", "latex-source/"))
        and formulas_are_free()
        and store.has_unread_formulas(con, doc_id)
    ):
        if "formulas" not in pending:
            store.request_reading(con, doc_id, "formulas", by="door")
        return "formulas"
    if crops:
        return "figure-crops"
    # never the edge whose reading just landed: a model that read none of
    # the figures would be asked for them without end
    if (
        meta.get("source") in store.CAPTURE_SOURCES
        and not stamp.startswith("figures/")
        and vision_is_free()
    ):
        doc = store.get_document(con, doc_id)
        if doc and any(not r["described_by"] for r in figures.refs(doc["text"])):
            if "figures" not in pending:
                store.request_reading(con, doc_id, "figures", by="door")
            return "figures"
    return None
