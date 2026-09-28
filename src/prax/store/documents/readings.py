"""The readings queue: what a model is asked to read of a document, which
documents want one, their pages and figures, and what finished."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..base import (
    _NOW,
    _archive_path,
    _like_prefix,
    _reading,
    _serialized,
    now,
)
from .meta import DOCTYPES, get_meta, set_meta

# A reading request: a person (or an agent) asks for a named extractor on
# one document — the vision model over its scanned pages, a second reading
# of an image, OCR in another script, Docling — and a worker does it
# through the work protocol, which hands requests out before the pending
# captures. The request lives in ``meta.reading`` until the result comes
# back, then records the outcome, so the document page can say what
# happened and the Jobs page what is waiting.

READINGS = (
    "vision-pages",
    "vision",
    "figures",
    "figure-refs",
    "figure-crops",
    "formulas",
    "polish",
    "pymupdf4llm-ocr",
    "djvu",  # the next window of a long scan (stage Y)
    "docling",
    "marker",
    "trafilatura",
    "pymupdf4llm",
)
# the setting a request may choose for one run, per extractor
MODES: dict[str, tuple[str, ...] | None] = {
    "vision-pages": ("scans", "all"),  # the pages without a text layer, or every page
    "marker": ("fast", "balanced"),  # the layout by rules, or by the vision model too
    # the figures a caption claims or every image; "-again" reads the ones
    # this model has read before too, replacing its earlier reading
    "figures": ("captioned", "all", "again", "all-again"),
    "formulas": ("new", "again"),  # the unread ones, or every one read again
    "pymupdf4llm-ocr": None,  # the recognizer's script: ch, en, latin, arabic…
}
_MODE_WORD = re.compile(r"[a-z][a-z0-9_]*")


def check_mode(extractor: str, mode: str | None) -> None:
    """``mode`` is one the extractor takes (``MODES``): a fixed choice, or
    for OCR any language word the recognizer knows."""
    if extractor not in READINGS:
        raise ValueError(f"extractor must be one of {READINGS}")
    if mode is None:
        return
    if extractor not in MODES:
        raise ValueError(f"{extractor} takes no mode")
    allowed = MODES[extractor]
    if allowed is None:
        if not _MODE_WORD.fullmatch(mode):
            raise ValueError(f"a language word, not {mode!r}")
    elif mode not in allowed:
        raise ValueError(f"mode must be one of {allowed} for {extractor}")


@_serialized
def request_reading(
    con: sqlite3.Connection,
    doc_id: int,
    extractor: str,
    *,
    mode: str | None = None,
    by: str = "human",
) -> dict[str, Any]:
    """Ask for ``extractor`` (one of ``READINGS``) on a document; ``mode``
    is the extractor's setting for this run (``MODES``: ``vision-pages``
    reads the scans or every page, ``figures`` the captioned ones or every
    image).

    Requests queue: a document may wait for several readings at once, and
    asking again for one it is already waiting for changes nothing. Until
    migration 21 a request was one field on the document and a new one
    replaced whatever was waiting there.
    """
    check_mode(extractor, mode)
    row = con.execute("SELECT id FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such document: {doc_id}")
    con.execute(
        "INSERT OR IGNORE INTO readings (doc_id, extractor, mode, asked_by, at)"
        f" VALUES (?, ?, ?, ?, {_NOW})",
        (doc_id, extractor, mode, by),
    )
    con.commit()
    waiting = con.execute(
        "SELECT extractor, mode, asked_by AS by, at, state FROM readings"
        " WHERE doc_id = ? AND extractor = ? AND state = 'requested'"
        " ORDER BY id DESC LIMIT 1",
        (doc_id, extractor),
    ).fetchone()
    return dict(waiting) if waiting else {}


def unreadable_documents(
    con: sqlite3.Connection, *, limit: int | None = None
) -> list[int]:
    """Documents without text whose every extractor has tried and found
    none (kept, empty or an error under its current stamp): scans without
    a text layer, mostly. The door does not hand them out again; a
    reading asked for — OCR, the vision model — is the way on."""
    from prax import parsers
    from prax.parsers import queue

    out: list[int] = []
    rows = con.execute(
        "SELECT id, mime, meta FROM documents WHERE text_hash IS NULL"
        "   AND json_extract(meta, '$.retired') IS NULL"
        "   AND json_extract(meta, '$.parse_history') IS NOT NULL"
        " ORDER BY id"
    )
    for r in rows:
        exts = parsers.candidates((r["mime"] or "").strip())
        meta = json.loads(r["meta"] or "{}")
        if exts and any(queue._seen(meta, e.stamp) for e in exts):
            out.append(r["id"])
            if limit and len(out) >= limit:
                break
    return out


THIN_BYTES_PER_PAGE = 100  # under this much text a page, the layer is the cover's
THIN_MIN_PAGES = 5  # a leaflet is not a scanned book


def document_has_figure(con: sqlite3.Connection, doc_id: int, ref: str) -> bool:
    """Whether the document's text references the figure ``ref`` (a figure
    chunk carries it in ``data.ref``): what the figure route checks, so an
    archive artifact is served only under a document that shows it."""
    row = con.execute(
        "SELECT 1 FROM chunks WHERE doc_id = ? AND kind = 'figure'"
        " AND json_extract(data, '$.ref') = ? LIMIT 1",
        (doc_id, ref),
    ).fetchone()
    return row is not None


def figure_blob(ref: str) -> tuple[bytes, str] | None:
    """A filed picture by its reference, with its media type; None when
    the archive has no such artifact."""
    if not re.fullmatch(r"[0-9a-f]{16,64}", ref or ""):
        return None
    path = _archive_path(ref)
    if not path.exists():
        return None
    from prax.parsers import figures

    data = path.read_bytes()
    return data, figures.media_of(data)


def page_counts(
    con: sqlite3.Connection, ids: list[int], *, open_files: bool = True
) -> dict[int, int]:
    """How many pages each of those PDFs has: ``meta.pages`` where a parse
    recorded it, else counted from the original when pymupdf is here (the
    worker's library; a door without it knows what the parses told it and
    leaves the rest out). ``open_files=False`` is what the parses told it
    only: the health check over ten thousand PDFs opened every one that
    predates the count (200 s); ``uncounted_pages`` names those and
    ``count_pages`` writes the count once."""
    out: dict[int, int] = {}
    todo: list[tuple[int, str]] = []
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            "SELECT id, hash, json_extract(meta, '$.pages') AS pages FROM documents"
            f" WHERE id IN ({marks})",
            tuple(part),
        ):
            if r["pages"]:
                out[r["id"]] = int(r["pages"])
            else:
                todo.append((r["id"], r["hash"]))
    if not todo or not open_files:
        return out
    try:
        import pymupdf
    except ImportError:
        return out
    for doc_id, digest in todo:
        try:
            with pymupdf.open(_archive_path(digest)) as doc:
                out[doc_id] = int(doc.page_count)
        except Exception:  # noqa: BLE001, S112 — not a PDF after all: left out
            continue
    return out


def uncounted_pages(con: sqlite3.Connection, *, limit: int | None = None) -> list[int]:
    """PDFs with text whose page count no parse recorded (``meta.pages``):
    the thin-texts check cannot weigh them until ``count_pages`` has."""
    sql = (
        "SELECT id FROM documents WHERE mime = 'application/pdf'"
        "   AND text_hash IS NOT NULL"
        "   AND json_extract(meta, '$.pages') IS NULL"
        "   AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["id"] for r in con.execute(sql)]


def count_pages(con: sqlite3.Connection, ids: list[int]) -> int:
    """Open each of those PDFs once and keep its page count in
    ``meta.pages``: what a parse records for every document since, done
    for the ones before. Needs pymupdf; a PDF it cannot open is left as
    it is. A PDF that opens with no pages at all records zero, which is
    the truth about a truncated original and stops the check asking
    again. Returns how many were counted."""
    counted = page_counts(con, ids, open_files=True)
    done = 0
    for doc_id in ids:
        n = counted.get(doc_id)
        if n is None:
            continue
        meta = get_meta(con, doc_id)
        if meta.get("pages") is not None:
            continue
        meta["pages"] = n
        set_meta(con, doc_id, meta)
        done += 1
    return done


def thin_documents(
    con: sqlite3.Connection,
    *,
    per_page: int = THIN_BYTES_PER_PAGE,
    min_pages: int = THIN_MIN_PAGES,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """PDFs of ``min_pages`` pages or more whose text comes to under
    ``per_page`` bytes a page: a scan whose text layer is the cover's or
    the front matter's, taken for the book — what ``unreadable_documents``
    cannot see, since something was read. Rows of ``id``, ``pages``,
    ``bytes`` and ``per_page``, the longest first; OCR is the way on."""
    rows = con.execute(
        "SELECT id, text_hash FROM documents WHERE mime = 'application/pdf'"
        "   AND text_hash IS NOT NULL"
        "   AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
    ).fetchall()
    # what the parses recorded: a PDF without a count is not weighed
    # (``uncounted_pages``), rather than every one opened on every check
    pages = page_counts(con, [r["id"] for r in rows], open_files=False)
    out = []
    for r in rows:
        n = pages.get(r["id"])
        if not n or n < min_pages:
            continue
        try:
            size = _archive_path(r["text_hash"]).stat().st_size
        except OSError:
            continue
        if size / n < per_page:
            out.append(
                {"id": r["id"], "pages": n, "bytes": size, "per_page": size // n}
            )
    out.sort(key=lambda o: -o["pages"])
    return out[:limit] if limit else out


def select_for_reading(
    con: sqlite3.Connection,
    *,
    ids: list[int] | None = None,
    mime: str | None = None,
    text_source: str | None = None,
    title: str | None = None,
    unreadable: bool = False,
    thin: int | None = None,
    doctype: str | None = None,
    unpolished: bool = False,
    read_figures: bool = False,
    unread_figures: bool = False,
    bare_captions: bool = False,
    read_formulas: bool = False,
    unread_formulas: bool = False,
    maths: float | None = None,
    limit: int | None = None,
) -> list[int]:
    """The documents a reading is asked for at once: the ``ids`` given,
    narrowed by a MIME type or prefix (``application/pdf``, ``image/``),
    by the prefix of the text-source stamp (``pymupdf4llm/1.28.2``: what
    an old extractor read), by words the title contains, to the
    unreadable ones, to the thin ones (``thin``: PDFs with under that
    many bytes of text a page, ``thin_documents``), to the ones holding
    a figure a model has read
    (``read_figures``: what a better prompt or a better model goes over
    again) and to the ones holding a figure nobody has read
    (``unread_figures``), the documents holding a caption with no
    picture behind it (``bare_captions``: what `figure-crops` renders),
    the same for formulas (``read_formulas``,
    ``unread_formulas``), and to the mathematical ones (``maths``: at
    least that many references to numbered equations per 10,000
    characters of prose, and at least :data:`MATHS_MIN_REFS` of them —
    what a parser that reads the mathematics is for); every filter
    given must hold. Retired documents are never selected."""
    sql = "SELECT id FROM documents WHERE json_extract(meta, '$.retired') IS NULL"
    args: list[Any] = []
    if ids:
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        args.extend(int(i) for i in ids)
    if mime:
        sql += " AND coalesce(mime, '') LIKE ? ESCAPE '!'"
        args.append(_like_prefix(mime))
    if title:
        sql += " AND lower(coalesce(title, '')) LIKE ? ESCAPE '!'"
        args.append("%" + _like_prefix(title.lower())[:-1] + "%")
    if text_source:
        sql += (
            " AND coalesce(json_extract(meta, '$.text_source'), '') LIKE ? ESCAPE '!'"
        )
        args.append(_like_prefix(text_source))
    if doctype:
        if doctype not in DOCTYPES:
            raise ValueError(f"doctype must be one of {sorted(DOCTYPES)}")
        sql += " AND " + DOCTYPES[doctype].replace("d.", "")
    if unpolished:
        # a video with an automatic transcript whose text the polish has
        # not written yet
        sql += (
            " AND json_extract(meta, '$.video.captions') = 'asr'"
            " AND coalesce(json_extract(meta, '$.text_source'), '') NOT LIKE 'polish/%'"
        )
    sql += " ORDER BY id"
    chosen = [r[0] for r in con.execute(sql, args)]
    if unreadable:
        keep = set(unreadable_documents(con))
        chosen = [i for i in chosen if i in keep]
    if thin is not None:
        keep = {o["id"] for o in thin_documents(con, per_page=thin)}
        chosen = [i for i in chosen if i in keep]
    if bare_captions:
        # a caption with no picture behind it: what the crop pass renders,
        # and asking for it anywhere else is a fetch and a parse for
        # nothing (the figures queue spent a day doing exactly that)
        with_bare = {
            r[0]
            for r in con.execute(
                "SELECT DISTINCT doc_id FROM chunks WHERE kind = 'figure'"
                " AND json_extract(data, '$.ref') IS NULL"
            )
        }
        chosen = [i for i in chosen if i in with_bare]
    for wanted, kind, read in (
        (read_figures, "figure", True),
        (unread_figures, "figure", False),
        (read_formulas, "formula", True),
        (unread_formulas, "formula", False),
    ):
        if not wanted:
            continue
        rows = con.execute(
            # the kind first, then the readings: an OR left loose here once
            # selected every document with an unread figure as one with an
            # unread formula (3,210 requests, withdrawn)
            f"SELECT DISTINCT doc_id FROM chunks WHERE kind = '{kind}' AND"
            " coalesce(json_array_length(json_extract(data, '$.readings')), 0)"
            + (" > 0" if read else " = 0")
        )
        keep = {r[0] for r in rows}
        chosen = [i for i in chosen if i in keep]
    if maths is not None:
        dense = equation_density(con, chosen)
        chosen = [i for i in chosen if dense.get(i, 0.0) >= maths]
    return chosen[:limit] if limit else chosen


MATHS_MIN_REFS = 15  # fewer references to numbered equations is not a maths paper
_EQ_REF = re.compile(r"(?<![\w.])\((\d{1,3})\)(?![\w])")


def equation_density(
    con: sqlite3.Connection, ids: list[int] | None = None
) -> dict[int, float]:
    """How mathematical a document's prose is: references to numbered
    equations — "(4)" between words — per 10,000 characters of its text
    chunks; 0 under :data:`MATHS_MIN_REFS` references. The 21 papers read
    with marker on 2026-09-17 scored 14 to 39; a user guide with numbered
    steps scores too, which the reference floor mostly keeps out. A scan
    of every chunk of the documents asked about: seconds for a library,
    which a bulk request may spend."""
    if ids is not None and not ids:
        return {}
    sql = "SELECT doc_id, text FROM chunks WHERE kind = 'text'"
    args: list[Any] = []
    if ids is not None:
        sql += f" AND doc_id IN ({','.join('?' * len(ids))})"
        args = [int(i) for i in ids]
    refs: dict[int, int] = {}
    chars: dict[int, int] = {}
    for r in con.execute(sql, args):
        d = r["doc_id"]
        refs[d] = refs.get(d, 0) + len(_EQ_REF.findall(r["text"]))
        chars[d] = chars.get(d, 0) + len(r["text"])
    return {
        d: (
            refs[d] / chars[d] * 10000
            if refs[d] >= MATHS_MIN_REFS and chars[d]
            else 0.0
        )
        for d in chars
    }


def has_unread_formulas(con: sqlite3.Connection, doc_id: int) -> bool:
    """Does the document hold a display equation nobody has read?"""
    row = con.execute(
        "SELECT 1 FROM chunks WHERE doc_id = ? AND kind = 'formula'"
        " AND coalesce(json_array_length(json_extract(data, '$.readings')), 0) = 0"
        " LIMIT 1",
        (doc_id,),
    ).fetchone()
    return row is not None


def request_readings(
    con: sqlite3.Connection,
    ids: list[int],
    extractor: str,
    *,
    mode: str | None = None,
    by: str = "human",
    dry_run: bool = False,
) -> dict[str, int]:
    """A reading request on each of ``ids`` (``request_reading``); a
    document the extractor does not read is skipped and counted. A
    ``dry_run`` counts the same way and asks for nothing: its count once
    took the RTF files an OCR would skip (271 where 92 PDFs were meant)."""
    from prax import parsers

    check_mode(extractor, mode)
    requested = skipped = 0
    for doc_id in ids:
        row = con.execute(
            "SELECT mime FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        # the type only: whether the extractor's server is up is the
        # worker's business when it runs the reading (a busy marker server
        # answered a probe late once, and four papers were "skipped")
        if row is None or not parsers.by_name(extractor).accepts(row["mime"] or ""):
            skipped += 1
            continue
        if not dry_run:
            request_reading(con, doc_id, extractor, mode=mode, by=by)
        requested += 1
    return {"selected": len(ids), "requested": requested, "skipped": skipped}


@_serialized
def cancel_reading(
    con: sqlite3.Connection, doc_id: int, *, extractor: str | None = None
) -> bool:
    """Withdraw what a document is waiting for: one reading by name, or
    every one of them. Returns whether anything was waiting."""
    args: tuple[Any, ...] = (doc_id,)
    sql = "DELETE FROM readings WHERE doc_id = ? AND state = 'requested'"
    if extractor:
        sql += " AND extractor = ?"
        args += (extractor,)
    cur = con.execute(sql, args)
    dropped = int(cur.rowcount or 0)
    if not extractor:  # the old single slot, for a document read before m21
        meta = get_meta(con, doc_id)
        if meta.pop("reading", None):
            con.execute(
                "UPDATE documents SET meta = ? WHERE id = ?",
                (json.dumps(meta), doc_id),
            )
            dropped += 1
    con.commit()
    return dropped > 0


@_reading
def pending_readings(con: sqlite3.Connection, doc_id: int) -> list[dict[str, Any]]:
    """What this document is waiting to be read by, oldest asked first."""
    return [
        dict(r)
        for r in con.execute(
            "SELECT id, extractor, mode, asked_by AS by, at, state FROM readings"
            " WHERE doc_id = ? AND state = 'requested' ORDER BY id",
            (doc_id,),
        )
    ]


@_serialized
def finish_reading(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    outcome: str,
    stamp: str,
    error: str | None = None,
    extractor: str | None = None,
) -> None:
    """The worker's result for a requested reading: ``outcome`` is the
    parse action (``upgraded``, ``created``, ``kept``, ``empty``) or
    ``error`` with its message.

    ``extractor`` names which of the document's waiting readings this
    was; without it the oldest is taken, which is what a worker that
    reported no request means. The row is marked done and the document's
    ``meta.reading`` keeps it as its last reading, which is what the page
    shows.
    """
    args: tuple[Any, ...] = (doc_id,)
    sql = (
        "SELECT id, extractor, mode, asked_by, at FROM readings"
        " WHERE doc_id = ? AND state = 'requested'"
    )
    if extractor:
        sql += " AND extractor = ?"
        args += (extractor,)
    row = con.execute(sql + " ORDER BY id LIMIT 1", args).fetchone()
    finished = now()
    state = "error" if error else "done"
    if row is not None:
        con.execute(
            "UPDATE readings SET state = ?, outcome = ?, stamp = ?, error = ?,"
            " finished_at = ? WHERE id = ?",
            (state, outcome, stamp, error, finished, row["id"]),
        )
    meta = get_meta(con, doc_id)
    was = meta.get("reading") or {}
    meta["reading"] = {
        "extractor": row["extractor"] if row is not None else was.get("extractor"),
        "mode": row["mode"] if row is not None else was.get("mode"),
        "by": row["asked_by"] if row is not None else was.get("by"),
        "at": row["at"] if row is not None else was.get("at"),
        "state": state,
        "outcome": outcome,
        "stamp": stamp,
        "error": error,
        "finished_at": finished,
    }
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()


def reading_requests(
    con: sqlite3.Connection,
    *,
    state: str | None = "requested",
    limit: int | None = 50,
    oldest_first: bool = False,
) -> list[dict[str, Any]]:
    """Reading requests: the waiting ones (``state`` ``requested``), or
    every state (None), newest first for the status view; the hand-out
    asks for the whole queue oldest first (``limit`` None), so a burst of
    newer requests never hides the older ones.

    A document may wait for several readings, so a document can appear
    more than once here — one row per request, which is what the queue
    is (migration 21).
    """
    params: list[Any] = [state] if state else []
    sql = (
        "SELECT r.id, r.doc_id, r.extractor, r.mode, r.asked_by AS by, r.at,"
        " r.state, r.outcome, r.stamp, r.error, r.finished_at,"
        " d.title, d.mime FROM readings r JOIN documents d ON d.id = r.doc_id"
        + (" WHERE r.state = ?" if state else "")
        + (" ORDER BY r.id ASC" if oldest_first else " ORDER BY r.id DESC")
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(r) for r in con.execute(sql, params)]


@_reading
def count_reading_requests(
    con: sqlite3.Connection, *, state: str | None = "requested"
) -> int:
    """How many reading requests are in that state — the list is a page
    (``reading_requests``), and a bulk re-read places thousands."""
    sql = "SELECT count(*) FROM readings"
    args: tuple[Any, ...] = ()
    if state:
        sql += " WHERE state = ?"
        args = (state,)
    return int(con.execute(sql, args).fetchone()[0])


def finished_readings(
    con: sqlite3.Connection, *, limit: int = 50
) -> list[dict[str, Any]]:
    """The reading requests lately finished, newest finish first — its own
    query, because a bulk request places hundreds at one time and the
    newest-requested window then holds nothing but waiting ones."""
    return [
        dict(r)
        for r in con.execute(
            "SELECT r.doc_id, r.extractor, r.mode, r.asked_by AS by, r.at, r.state,"
            " r.outcome, r.stamp, r.error, r.finished_at, d.title, d.mime"
            " FROM readings r JOIN documents d ON d.id = r.doc_id"
            " WHERE r.state IN ('done', 'error')"
            " ORDER BY r.finished_at DESC, r.id DESC LIMIT ?",
            (limit,),
        )
    ]


def figures_to_read(
    con: sqlite3.Connection, doc_id: int, *, model: str, every: bool = False
) -> int:
    """How many of a document's figures this model has not read yet.

    The same test the reading itself makes (``parsers.figures.annotate``).
    A figure is wanted when it holds a picture at all, when the model is
    absent from its readings, and, unless ``every``, when a caption
    claims it — the captioned pass leaves "Figure on page N" alone.

    The picture is the part that was missed: a `figure` chunk without a
    ``ref`` is a caption whose image no extractor could pull out of the
    PDF, and no reading will ever change it. More than half the store's
    figure chunks are those.
    """
    from prax.parsers.figures import UNCAPTIONED

    n = 0
    for row in con.execute(
        "SELECT data FROM chunks WHERE doc_id = ? AND kind = 'figure'", (doc_id,)
    ):
        data = json.loads(row[0] or "{}")
        if not data.get("ref"):  # a caption with no picture behind it
            continue
        if not every and str(data.get("caption") or "").startswith(UNCAPTIONED):
            continue
        read_by = {r.get("model") for r in (data.get("readings") or [])}
        if model not in read_by:
            n += 1
    return n


def readings_done_since(con: sqlite3.Connection, since: str) -> dict[str, int]:
    """How many readings finished since a moment, per extractor: the rate
    a waiting queue is moving at, which is what says whether it is stuck
    or merely long."""
    rows = con.execute(
        "SELECT extractor, count(*) FROM readings WHERE state = 'done'"
        " AND finished_at >= ? GROUP BY 1",
        (since,),
    ).fetchall()
    return {str(name or "?"): int(n) for name, n in rows}


def waiting_readings(con: sqlite3.Connection) -> dict[str, int]:
    """How many requests wait per extractor: what a script that swaps
    the card to marker and back watches (``prax readings --wait``)."""
    rows = con.execute(
        "SELECT extractor, count(*) FROM readings WHERE state = 'requested'"
        " GROUP BY 1 ORDER BY 2 DESC, 1"
    ).fetchall()
    return {str(name or "?"): int(n) for name, n in rows}


@_serialized
def note_ocr_progress(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    extractor: str,
    pages: int,
    left: int,
) -> dict[str, Any]:
    """How far the OCR of a long scan has come (stage Y), on the document
    as ``meta.ocr``: the extractor, the pages, how many still wait, and
    when the last window ended. While pages wait, the next window is asked
    for as a reading of the same extractor; the last window leaves the
    record with nothing left, which is what the document page shows."""
    meta = get_meta(con, doc_id)
    meta["ocr"] = {"extractor": extractor, "pages": pages, "left": left, "at": now()}
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    if left > 0:
        request_reading(con, doc_id, extractor, by="ocr-window")
    return meta["ocr"]
