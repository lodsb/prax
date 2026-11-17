"""A recording's text: what the transcribe step heard in it (plan item AQ
stage 4, ``prax.ml.speech``, ``prax.steps.recordings``).

A recording is a document whose original is sound or video, most often
referenced on a share (``documents.held`` 0). The step's worker reads it
through the door and posts the transcript, which is indexed as the
document's text like any parser's (``index_text``), with its time marks,
so a passage is found at its moment. What the reading found besides the
words (how long it is, how much of it is speech, singing or music, how
many lines the model was unsure of) is ``meta.transcript``.

A recording with a text from elsewhere (a captured video's transcript is
an HTML page, not a recording; a sound file sent with its words) is not
read: the step writes only the text it wrote before.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..base import LIVE, _reading, _serialized, now
from .meta import _due, _put_meta, _refresh_document_field, get_meta
from .photos import reachable_due
from .text import _index_text

# the documents the step reads: sound and video, a text of its own or none
_KINDS = (
    "(mime LIKE 'audio/%' OR mime LIKE 'video/%')"
    " AND (text_hash IS NULL OR json_extract(meta, '$.transcript') IS NOT NULL)"
)


def is_recording(mime: str) -> bool:
    return mime.startswith(("audio/", "video/"))


# what the step writes into ``meta.transcript``
TRANSCRIPT_KEYS = frozenset(
    {"v", "by", "at", "duration", "speech_s", "sung_s", "music_s"}
    | {"segments", "doubtful", "error"}
)


@_reading
def transcripts_due(
    con: sqlite3.Connection,
    version: int,
    *,
    limit: int,
    skip: frozenset[int] = frozenset(),
) -> list[int]:
    """Recordings with no transcript of ``version`` (or a later one) whose
    original this door reaches now, oldest first: a referenced one whose
    share is not mounted waits for it."""
    return reachable_due(
        con,
        "transcribe",
        version,
        kinds=_KINDS,
        keep=is_recording,
        limit=limit,
        skip=skip,
    )


@_serialized
def apply_transcript(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    text: str,
    facts: dict[str, Any],
    version: int,
    by: str,
) -> str:
    """A recording's reading: its transcript indexed as its text (none
    when nothing was heard), what was found in ``meta.transcript``, and
    the document field rewritten. Returns ``read``, or ``silent`` when no
    speech, singing or music was heard."""
    meta = get_meta(con, doc_id)
    meta["transcript"] = {
        **{k: v for k, v in facts.items() if k in TRANSCRIPT_KEYS - {"v", "by", "at"}},
        "v": version,
        "by": by,
        "at": now(),
    }
    _put_meta(con, doc_id, meta)
    if text.strip():
        _index_text(con, doc_id, text, text_source=f"transcribe/{by}")
    _refresh_document_field(con, doc_id)
    con.commit()
    return "read" if text.strip() else "silent"


@_serialized
def transcript_failed(
    con: sqlite3.Connection, doc_id: int, *, error: str, version: int, by: str
) -> str:
    """A recording ffmpeg could not decode: kept as ``meta.transcript.error``
    at this version, so it is not handed out again until a later one."""
    meta = get_meta(con, doc_id)
    meta["transcript"] = {"v": version, "by": by, "at": now(), "error": error[:300]}
    _put_meta(con, doc_id, meta)
    con.commit()
    return "failed"


@_reading
def transcript_counts(con: sqlite3.Connection, version: int) -> dict[str, int]:
    """How many recordings are read at ``version``, failed at it, and wait
    (reachable or not)."""
    done = f"NOT {_due('transcribe')}"
    row = con.execute(
        "SELECT"
        f" sum({done} AND json_extract(meta, '$.transcript.error') IS NULL),"
        f" sum({done} AND json_extract(meta, '$.transcript.error') IS NOT NULL),"
        f" sum({_due('transcribe')})"
        f" FROM documents WHERE {_KINDS} AND {LIVE}",
        (version, version, version),
    ).fetchone()
    return {"read": row[0] or 0, "failed": row[1] or 0, "waiting": row[2] or 0}
