"""Which documents were opened, and when (migration 66, plan item "Reading
activity as a signal"): recorded, and read back as counts. Nothing ranks
by it yet; a vote in the fusion waits for months of it and a measurement.

The door calls ``note_open`` when a document is read whole (``GET
/get/{id}``). A document, a viewer and a way in are written at most once
in ``OPEN_EVERY`` seconds, held in memory like a token's last use: the web
page draws a document again on every change of the store, and an agent
reads a long one in windows."""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

from ..base import VIEWER, _reading, _scrubbed, _serialized

OPEN_EVERY = 1800.0  # seconds: one opening a document, viewer and way in
OPEN_VIAS = ("web", "agent", "api")
ADMIN_VIEWER = "admin"  # the administrator's token, which has no name
OPENED_KEPT = 10_000  # remembered openings before the expired ones go
_opened: dict[tuple[int, str, str], float] = {}
_opened_lock = threading.Lock()  # the door's request threads share it


def note_open(con: sqlite3.Connection, doc_id: int, via: str) -> bool:
    """Record that the current viewer opened ``doc_id`` through ``via``,
    unless it did within ``OPEN_EVERY``. Returns whether a row was
    written."""
    if via not in OPEN_VIAS:
        raise ValueError(f"via must be one of {OPEN_VIAS}")
    viewer = VIEWER.get()
    who = viewer.name if viewer is not None else ADMIN_VIEWER
    key = (doc_id, who, via)
    now_m = time.monotonic()
    with _opened_lock:
        if now_m - _opened.get(key, float("-inf")) < OPEN_EVERY:
            return False
        if len(_opened) > OPENED_KEPT:  # what has passed its time is no use
            for old in [k for k, t in _opened.items() if now_m - t >= OPEN_EVERY]:
                del _opened[old]
        _opened[key] = now_m
    _write_open(con, doc_id, who, via)
    return True


@_serialized
def _write_open(con: sqlite3.Connection, doc_id: int, who: str, via: str) -> None:
    con.execute(
        "INSERT INTO document_opens (doc_id, viewer, via) VALUES (?, ?, ?)",
        (doc_id, who, via),
    )
    con.commit()


@_scrubbed
@_reading
def document_opens(
    con: sqlite3.Connection, doc_ids: list[int], *, since: str | None = None
) -> dict[int, dict[str, Any]]:
    """How often each of these documents was opened (since ``since``, a
    timestamp, or ever), by way in, and when last: ``{doc_id: {count,
    web, agent, api, last}}``; a document never opened is left out."""
    out: dict[int, dict[str, Any]] = {}
    for start in range(0, len(doc_ids), 500):
        part = doc_ids[start : start + 500]
        marks = ",".join("?" * len(part))
        for r in con.execute(
            f"SELECT doc_id, via, count(*) AS n, max(opened_at) AS last"
            f" FROM document_opens WHERE doc_id IN ({marks})"
            f" AND opened_at >= ? GROUP BY doc_id, via",
            (*part, since or ""),
        ):
            seen = out.setdefault(
                int(r["doc_id"]),
                {"count": 0, **dict.fromkeys(OPEN_VIAS, 0), "last": ""},
            )
            seen[r["via"]] = int(r["n"])
            seen["count"] += int(r["n"])
            seen["last"] = max(seen["last"], str(r["last"]))
    return out
