"""What a document is: its genres (stage Z, ``ontology/genres.yaml``).

A person's genres are the gold sample the genres step is measured and
calibrated against. They are written on the Review page's "genre" tab and
kept in ``meta.genres`` as ``[{"genre", "p"}]`` with ``meta.genres_by``
naming who wrote them. A person's are ``by: human``, ``p`` 1.0, and no
model overwrites them. A document a person could not place is
``meta.genres_skip`` and leaves the sample.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from prax.graph import ontology

from ..base import (
    _read_archive,
    _reading,
    _serialized,
    document_hidden,
    now,
)
from .meta import get_meta

GENRE_OPENING = 1500  # characters of the text a labeller reads beside the summary

# Where a document came from, as the sample draws it: each source in turn,
# so the NAS dump does not fill the gold sample and the extension's pages
# are in it. ``source`` on each item says which, so an estimate for the
# whole library can weigh them back.
_GENRE_SOURCE = (
    "CASE"
    " WHEN json_extract(meta, '$.origin.path') IS NOT NULL THEN 'nas'"
    " WHEN json_extract(meta, '$.source') = 'zotero' THEN 'zotero'"
    " WHEN json_extract(meta, '$.capture.by') = 'extension' THEN 'extension'"
    " ELSE coalesce(json_extract(meta, '$.source'), 'other') END"
)
# the open documents: text to read, not retired, not a wiki page, and no
# person has labelled or skipped them
_GENRE_OPEN = (
    " FROM documents WHERE text_hash IS NOT NULL"
    " AND json_extract(meta, '$.retired') IS NULL"
    " AND coalesce(json_extract(meta, '$.source'), '') != 'wiki'"
    " AND coalesce(json_extract(meta, '$.genres_by'), '') != 'human'"
    " AND json_extract(meta, '$.genres_skip') IS NULL"
)
_GENRE_LABELLED = " FROM documents WHERE json_extract(meta, '$.genres_by') = 'human'"


@_serialized
def set_genres(
    con: sqlite3.Connection,
    doc_id: int,
    genres: list[str] | None,
    *,
    by: str = "human",
    skip: bool = False,
) -> dict[str, Any]:
    """A person's genres for a document (``by: human``), checked against
    ``ontology.genres()``. ``skip``: the person could not place it, and it
    leaves the sample. ``genres=None`` without ``skip`` takes both back."""
    if document_hidden(con, doc_id):
        raise KeyError(f"no such document: {doc_id}")
    meta = get_meta(con, doc_id)
    for key in ("genres", "genres_by", "genres_at", "genres_skip"):
        meta.pop(key, None)
    if skip:
        meta["genres_skip"] = now()
    elif genres is not None:
        labels = ontology.genres().check(genres)
        if not labels:
            raise ValueError("no genre given: skip the document instead")
        meta["genres"] = [{"genre": g, "p": 1.0} for g in labels]
        meta["genres_by"] = by
        meta["genres_at"] = now()
    con.execute(
        "UPDATE documents SET meta = ? WHERE id = ?", (json.dumps(meta), doc_id)
    )
    con.commit()
    return {
        "doc_id": doc_id,
        "genres": [g["genre"] for g in meta.get("genres") or []],
        "skipped": bool(meta.get("genres_skip")),
    }


def _genre_item(row: sqlite3.Row, opening: int) -> dict[str, Any]:
    meta = json.loads(row["meta"] or "{}")
    text = ""
    if opening and row["text_hash"]:
        try:
            text = _read_archive(row["text_hash"]).decode("utf-8")[:opening]
        except (OSError, KeyError):
            text = ""
    origin = meta.get("origin") or {}
    return {
        "id": row["id"],
        "title": row["title"],
        "mime": row["mime"],
        "source": row["source"],
        "where": origin.get("path") or row["source_url"] or row["original_path"],
        "lang": meta.get("lang"),
        "summary": meta.get("summary"),
        "opening": text,
        "genres": [g.get("genre") for g in meta.get("genres") or []],
        "genres_by": meta.get("genres_by"),
        "genres_at": meta.get("genres_at"),
    }


@_reading
def genre_sample(
    con: sqlite3.Connection,
    *,
    state: str = "open",
    offset: int = 0,
    limit: int = 10,
    opening: int = GENRE_OPENING,
) -> dict[str, Any]:
    """The documents to label on the Review page's "genre" tab.

    ``open``: the ones no person has labelled or skipped, one source after
    another (the NAS, Zotero, the extension, the other captures) and, within
    a source, in an order fixed by the id's hash, so the page reads the same
    on every visit and the sample spreads over each source. ``labelled``:
    the person's labels, the last first, where one is corrected. Each item
    carries its summary and the opening of its text. ``labelled`` and
    ``skipped`` count what the person has done so far."""
    if state not in ("open", "labelled"):
        raise ValueError("state is open or labelled")
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    cols = (
        "id, title, mime, meta, text_hash, source_url, original_path,"
        f" {_GENRE_SOURCE} AS source"
    )
    if state == "open":
        where = _GENRE_OPEN
        total = con.execute("SELECT count(*)" + where).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM (SELECT {cols}, row_number() OVER ("
            f" PARTITION BY {_GENRE_SOURCE}"
            " ORDER BY (id * 2654435761) % 4294967296) AS turn"
            + where
            + ") ORDER BY turn, source LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    else:
        where = _GENRE_LABELLED
        total = con.execute("SELECT count(*)" + where).fetchone()[0]
        rows = con.execute(
            f"SELECT {cols}"
            + where
            + " ORDER BY json_extract(meta, '$.genres_at') DESC, id DESC"
            " LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    done = con.execute(
        "SELECT sum(json_extract(meta, '$.genres_by') = 'human'),"
        " sum(json_extract(meta, '$.genres_skip') IS NOT NULL) FROM documents"
    ).fetchone()
    return {
        "total": int(total),
        "labelled": int(done[0] or 0),
        "skipped": int(done[1] or 0),
        "items": [_genre_item(r, opening) for r in rows],
    }
