"""The parse queue: extract text for documents and hand it to ``index_text``.

Two selections:

* **pending**: documents with ``parsed_at IS NULL`` (registered, never
  indexed); the extractor's text becomes their first text artifact.
* **upgrade**: documents whose ``meta.text_source`` matches a given prefix
  (``"zotero-ft-cache"``, ``"pymupdf/"``…); a better extractor replaces the
  text and rebuilds the chunks. The old text is kept when the new one is
  suspiciously short (a login wall, a scanned PDF with no OCR), so an
  upgrade can never make a document less searchable.

Every attempt is appended to ``meta.parse_history`` with the extractor
stamp, character count, outcome and timestamp, which is what the extractor
comparison reads. Errors are recorded there too and never stop the run.

Heavy extractors run here, on the batch host; nothing in the serving path
imports this module (invariant 7).
"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from prax import parsers, store

MIN_CHARS = 200  # below this an extraction is "empty"
MIN_RATIO = 0.2  # an upgrade must keep at least this share of the old length


@dataclass
class Report:
    actions: Counter[str] = field(default_factory=Counter)
    seconds: float = 0.0
    errors: list[tuple[int, str]] = field(default_factory=list)

    def __str__(self) -> str:
        parts = ", ".join(f"{k}={n}" for k, n in sorted(self.actions.items()))
        return (
            f"parsed: {parts or 'nothing'}; {self.seconds:.0f} s;"
            f" errors: {len(self.errors)}"
        )


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(
    con: sqlite3.Connection,
    doc_id: int,
    entry: dict[str, Any],
    *,
    pages: int | None = None,
) -> dict[str, Any]:
    meta = store.get_meta(con, doc_id)
    history = list(meta.get("parse_history", []))
    history.append({"at": _now(), **entry})
    meta["parse_history"] = history
    if pages:  # a fact of the original the worker counted on the way
        meta["pages"] = int(pages)
    store.set_meta(con, doc_id, meta)
    return meta


def _seen(meta: dict[str, Any], stamp: str) -> bool:
    """True if this extractor version already tried the document and the
    result was kept, empty or an error: re-running would repeat that. An
    ``upgraded``/``created`` entry changes ``text_source``, so such documents
    leave the selection by themselves. A refusal for the OCR page budget is
    not an attempt — nothing was read — so a run with a bigger budget gets
    another go; nor is a worker that could not fetch the original."""
    return any(
        h.get("extractor") == stamp
        and (
            h.get("outcome") in ("kept", "empty")
            or (
                "error" in h
                and "OCR budget" not in str(h.get("error") or "")
                and not str(h.get("error") or "").startswith("fetch:")
            )
        )
        for h in meta.get("parse_history", [])
    )


def _too_short(new_len: int, old_len: int) -> bool:
    return new_len < MIN_CHARS or (old_len > 0 and new_len < MIN_RATIO * old_len)


def parse_one(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    extractor: str | None = None,
    force: bool = False,
) -> str:
    """Extract and index one document; return the action taken.

    Without an explicit ``extractor`` the registry's candidates for the MIME
    type are tried in order until one succeeds (MuPDF's Markdown path fails
    on some PDFs where its plain path does not). Every attempt, failed or
    not, is recorded in ``meta.parse_history``.

    ``created``: first text for the document. ``upgraded``: replaced the old
    text. ``kept``: new text too short, old text left in place (``force``
    overrides). ``empty``: too short and there was no old text. ``skipped``:
    no extractor for the MIME type. ``seen``: this extractor version already
    tried and kept/emptied/failed (``force`` overrides). Raises when every
    candidate failed.
    """
    doc = store.get_document(con, doc_id, max_chars=0)
    if doc is None:
        raise KeyError(f"no such document: {doc_id}")
    # a document may name its parser (a video capture: meta.parser), and
    # then only that one is tried: a wrong parse is worse than none
    extractor = extractor or doc["meta"].get("parser")
    exts = parsers.candidates(doc["mime"] or "", extractor)
    if not exts:
        return "skipped"
    if not force and _seen(doc["meta"], exts[0].stamp):
        return "seen"
    data = store.get_original(con, doc_id)
    path = doc.get("original_path")
    filename = path.replace("\\", "/").rsplit("/", 1)[-1] if path else None
    previous: str | None = None
    if doc["text_len"] and any(e.previous for e in exts):
        previous = store.get_document(con, doc_id)["text"]
    last_error: Exception | None = None
    for ext in exts:
        t0 = time.monotonic()
        try:
            text = ext(data, filename=filename, previous=previous).strip()
        except Exception as exc:  # noqa: BLE001 - recorded; the next candidate is tried
            last_error = exc
            apply_parse(
                con, doc_id, stamp=ext.stamp, error=f"{type(exc).__name__}: {exc}"
            )
            continue
        return apply_parse(
            con,
            doc_id,
            stamp=ext.stamp,
            text=text,
            seconds=round(time.monotonic() - t0, 2),
            force=force,
            keep_source=ext.annotates,
        )
    assert last_error is not None
    raise last_error


def covered_by_history(meta: dict[str, Any]) -> str | None:
    """The stamp the text is at, when an annotation in ``parse_history``
    amounts to a later revision of its extractor than ``text_source``
    says (the figure-refs pass over the library, before annotations moved
    the stamp): None when nothing in the history covers it."""
    current = meta.get("text_source") or ""
    own = parsers.stamp_parts(current)
    if own is None:
        return None
    moved = None
    for entry in meta.get("parse_history", []):
        if entry.get("outcome") not in ("created", "upgraded", "same"):
            continue
        parts = parsers.stamp_parts(str(entry.get("extractor", "")))
        if parts is None or parts[0] == own[0]:
            continue
        try:
            by = parsers.by_name(parts[0])
        except KeyError:
            continue
        moved = parsers.covered(moved or current, by) or moved
    return moved


def stale(con: sqlite3.Connection, *, limit: int | None = None) -> list[int]:
    """Documents whose text came from an extractor prax has revised since
    (``parsers.behind``): a re-read would produce something new, or say
    ``same`` and cost only the parse. Oldest first, retired ones left out."""
    out: list[int] = []
    rows = con.execute(
        "SELECT id, json_extract(meta, '$.text_source') AS src FROM documents"
        " WHERE text_hash IS NOT NULL AND json_extract(meta, '$.retired') IS NULL"
        " ORDER BY id"
    )
    for r in rows:
        if parsers.behind(r["src"] or "") is not None:
            out.append(r["id"])
            if limit and len(out) >= limit:
                break
    return out


def apply_parse(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    stamp: str,
    text: str | None = None,
    error: str | None = None,
    seconds: float = 0.0,
    force: bool = False,
    keep_source: bool = False,
    pages: int | None = None,
) -> str:
    """Take in what an extractor produced for a document, here or on a
    worker: record the attempt in ``meta.parse_history`` (and the page
    count of a PDF as ``meta.pages``, when the worker counted it) and index the
    text unless it is suspiciously short next to the old one (``force``
    overrides) or the same as the current text (``same``: the stamp moves,
    nothing is rebuilt). Returns the action: ``created``, ``upgraded``,
    ``same``, ``kept``, ``empty`` or ``error``. ``keep_source``: the
    extractor added to the text (figures, their readings) — the attempt
    is recorded under its stamp, ``meta.text_source`` stays the parser's."""
    doc = store.get_document(con, doc_id, max_chars=0)
    if doc is None:
        raise KeyError(f"no such document: {doc_id}")
    if error or text is None:
        _record(
            con, doc_id, {"extractor": stamp, "error": error or "no text"}, pages=pages
        )
        return "error"
    text = text.strip()
    old_len = doc["text_len"]
    entry = {"extractor": stamp, "chars": len(text), "seconds": seconds}
    if old_len and store.text_unchanged(con, doc_id, text):
        # a re-read that found nothing new (an upgrade pass over the
        # library): the stamp moves on, chunks and vectors stay
        _record(con, doc_id, {**entry, "outcome": "same"}, pages=pages)
        _restamp(con, doc_id, stamp, keep_source)
        return "same"
    if not force and _too_short(len(text), old_len):
        action = "kept" if old_len else "empty"
        _record(con, doc_id, {**entry, "outcome": action}, pages=pages)
        return action
    action = "upgraded" if old_len else "created"
    source = None if keep_source else stamp
    result = store.index_text(con, doc_id, text, text_source=source)
    # the artifact's hash in the record: every earlier text stays in the
    # archive, content-addressed, and this is how it is found again
    _record(
        con,
        doc_id,
        {**entry, "outcome": action, "text_hash": result["text_hash"]},
        pages=pages,
    )
    if keep_source:
        _restamp(con, doc_id, stamp, keep_source)
    elif action == "upgraded":
        # a replacing read (marker over a pymupdf4llm text, OCR over a
        # scan) leaves an extraction stamped on a text that no longer
        # exists: the extract step selects the document again. An
        # annotating read adds to the text and leaves the stamp: every
        # figure pass would re-extract the library otherwise
        store.unstamp_extraction(con, doc_id, stamp)
    return action


def _restamp(
    con: sqlite3.Connection, doc_id: int, stamp: str, keep_source: bool
) -> None:
    """After a read or an annotation, where ``meta.text_source`` goes: the
    reader's own stamp; or, for an annotation that is what a revision of
    the parser added (``Extractor.covers``), the parser's stamp at that
    revision, so the document is not read again for it."""
    if not keep_source:
        store.set_text_source(con, doc_id, stamp)
        return
    parts = parsers.stamp_parts(stamp)
    if parts is None:
        return
    current = store.get_meta(con, doc_id).get("text_source") or ""
    moved = parsers.covered(current, parsers.by_name(parts[0]))
    if moved:
        store.set_text_source(con, doc_id, moved)


def run(
    con: sqlite3.Connection,
    doc_ids: list[int],
    *,
    extractor: str | None = None,
    force: bool = False,
    limit: int | None = None,
    log: Callable[[int, int, str], None] | None = None,
) -> Report:
    """Parse ``doc_ids`` in order; ``limit`` caps the documents actually
    worked on (``seen`` and ``skipped`` ones do not count), so a batch loop
    over a stable selection always makes progress."""
    report = Report()
    t0 = time.monotonic()
    worked = 0
    for n, doc_id in enumerate(doc_ids):
        if limit is not None and worked >= limit:
            break
        try:
            action = parse_one(con, doc_id, extractor=extractor, force=force)
        except Exception as exc:  # noqa: BLE001 - keep going; the report lists failures
            report.errors.append((doc_id, f"{type(exc).__name__}: {exc}"))
            action = "error"
        report.actions[action] += 1
        if action not in ("seen", "skipped"):
            worked += 1
        if log is not None:
            log(n, doc_id, action)
    report.seconds = time.monotonic() - t0
    return report
