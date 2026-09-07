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
    con: sqlite3.Connection, doc_id: int, entry: dict[str, Any]
) -> dict[str, Any]:
    meta = store.get_meta(con, doc_id)
    history = list(meta.get("parse_history", []))
    history.append({"at": _now(), **entry})
    meta["parse_history"] = history
    store.set_meta(con, doc_id, meta)
    return meta


def _seen(meta: dict[str, Any], stamp: str) -> bool:
    """True if this extractor version already tried the document and the
    result was kept, empty or an error: re-running would repeat that. An
    ``upgraded``/``created`` entry changes ``text_source``, so such documents
    leave the selection by themselves."""
    return any(
        h.get("extractor") == stamp
        and (h.get("outcome") in ("kept", "empty") or "error" in h)
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
    exts = parsers.candidates(doc["mime"] or "", extractor)
    if not exts:
        return "skipped"
    if not force and _seen(doc["meta"], exts[0].stamp):
        return "seen"
    data = store.get_original(con, doc_id)
    old_len = doc["text_len"]
    last_error: Exception | None = None
    for ext in exts:
        t0 = time.monotonic()
        try:
            text = ext(data).strip()
        except Exception as exc:  # noqa: BLE001 - recorded; the next candidate is tried
            last_error = exc
            _record(
                con,
                doc_id,
                {"extractor": ext.stamp, "error": f"{type(exc).__name__}: {exc}"},
            )
            continue
        seconds = round(time.monotonic() - t0, 2)
        entry = {"extractor": ext.stamp, "chars": len(text), "seconds": seconds}
        if not force and _too_short(len(text), old_len):
            action = "kept" if old_len else "empty"
            _record(con, doc_id, {**entry, "outcome": action})
            return action
        action = "upgraded" if old_len else "created"
        _record(con, doc_id, {**entry, "outcome": action})
        store.index_text(con, doc_id, text, text_source=ext.stamp)
        return action
    assert last_error is not None
    raise last_error


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
