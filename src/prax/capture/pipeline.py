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

import sqlite3
from typing import Any

from prax import models, store
from prax.parsers import figures
from prax.text import language
from prax.writing import summaries, titles

# ---------------------------------------------------------------- titles


def file_name(doc: dict[str, Any]) -> str | None:
    z = (doc["meta"] or {}).get("zotero") or {}
    return z.get("filename") or doc["title"]


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
        native = summaries.native(held)
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


def _wants_crops(con: sqlite3.Connection, doc_id: int) -> bool:
    """Whether this document holds a caption with no picture behind it —
    a figure drawn with vector paths, which no extractor pulls out."""
    doc = store.get_document(con, doc_id)
    if not doc or doc.get("mime") != "application/pdf":
        return False
    lines = (doc.get("text") or "").split("\n")
    return bool(figures.bare_captions(lines))


def follow_ups(
    con: sqlite3.Connection, doc_id: int, *, stamp: str, action: str
) -> str | None:
    """The reading the door places after a text landed under ``stamp``
    with ``action`` (``created``/``upgraded``; nothing after ``same`` or an
    error), or None. A video capture with an automatic transcript gets the
    polish first and its frames read once that has landed; a marker read
    with display equations gets the formula readings; a capture with
    figures nobody has read gets the vision model."""
    if action not in ("created", "upgraded"):
        return None
    meta = store.get_meta(con, doc_id)
    # what the document is already waiting for: since migration 21 a
    # reading queues beside the others instead of replacing them, so the
    # door only declines to ask for the same one twice
    pending = {r["extractor"] for r in store.pending_readings(con, doc_id)}
    video = meta.get("video") or {}
    if (
        video.get("captions") == "asr"
        and not stamp.startswith("polish/")
        and polish_is_free()
    ):
        if "polish" not in pending:
            store.request_reading(con, doc_id, "polish", by="door")
        return "polish"
    if (
        stamp.startswith("marker/")
        and formulas_are_free()
        and store.has_unread_formulas(con, doc_id)
    ):
        if "formulas" not in pending:
            store.request_reading(con, doc_id, "formulas", by="door")
        return "formulas"
    # a PDF whose captions have no picture: the crop pass renders them off
    # the page and runs no model at all, so there is nothing to be free.
    # Never after itself, and never for a scan (its pages are the picture)
    if (
        stamp.startswith(("pymupdf4llm/", "marker/"))
        and "figure-crops" not in pending
        and _wants_crops(con, doc_id)
    ):
        store.request_reading(con, doc_id, "figure-crops", by="door")
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
