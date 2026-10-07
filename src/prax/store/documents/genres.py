"""What a document is and what it is about: its genres
(``ontology/genres.yaml``) and its subjects (``ontology/subjects.yaml``),
stage Z.

A person's labels are the gold sample the genres step is measured and
calibrated against. They are written on the Review page's "genre" tab, in
one act for both facets, and kept in ``meta.genres`` as ``[{"genre",
"p"}]`` and ``meta.subjects`` as ``[{"subject", "p"}]``, with
``meta.genres_by`` naming who labelled the document. A person's are
``by: human``, ``p`` 1.0, and no model overwrites them. A document a
person could not place is ``meta.genres_skip`` and leaves the sample.

A model may label the sample first (``by: claude``, each label with its
``p`` and a line of ``genres_note``), so the person checks rather than
labels: the "to check" list shows the least sure first. A model never
writes over a person's labels. When the person saves over a model's, the
model's are kept as ``meta.genres_model``, which is what measures how
often the model and the person agree.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal

from prax.graph import ontology

from ..base import (
    _read_archive,
    _reading,
    _serialized,
    document_hidden,
    now,
)
from .meta import _put_meta, get_meta

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
# the open documents: text to read, not retired, not a wiki page, and
# nobody, person or model, has labelled or skipped them
_GENRE_OPEN = (
    " FROM documents WHERE text_hash IS NOT NULL"
    " AND json_extract(meta, '$.retired') IS NULL"
    " AND coalesce(json_extract(meta, '$.source'), '') != 'wiki'"
    " AND json_extract(meta, '$.genres_by') IS NULL"
    " AND json_extract(meta, '$.genres_skip') IS NULL"
)
_GENRE_LABELLED = " FROM documents WHERE json_extract(meta, '$.genres_by') = 'human'"
# labelled by a model and not yet by a person: the "to check" list
_GENRE_CHECK = (
    " FROM documents WHERE json_extract(meta, '$.genres_by') IS NOT NULL"
    " AND json_extract(meta, '$.genres_by') != 'human'"
    " AND json_extract(meta, '$.retired') IS NULL"
)
# of those, the ones a domain rule placed by their labels: a rule set the
# domains, and not the Zotero one, which goes by the source alone. A wrong
# label there put the document in the wrong modules
_GENRE_RULED = (
    _GENRE_CHECK + " AND json_extract(meta, '$.domains_by') = 'rule'"
    " AND coalesce(json_extract(meta, '$.source'), '') != 'zotero'"
)
# how sure the model was of a document: its least sure label, genre or
# subject; the list shows the least sure first
_GENRE_SURE = (
    "min(coalesce((SELECT min(json_extract(value, '$.p'))"
    " FROM json_each(json_extract(meta, '$.genres'))), 1),"
    " coalesce((SELECT min(json_extract(value, '$.p'))"
    " FROM json_each(json_extract(meta, '$.subjects'))), 1))"
)


# what a labelling replaces: every key of the last one
GENRE_KEYS: tuple[
    Literal[
        "genres",
        "subjects",
        "genres_by",
        "genres_at",
        "genres_skip",
        "genres_note",
        "genres_run",
        "genres_tried",
    ],
    ...,
] = (
    "genres",
    "subjects",
    "genres_by",
    "genres_at",
    "genres_skip",
    "genres_note",
    "genres_run",
    "genres_tried",
)


@_serialized
def _sure(labels: list[dict[str, Any]] | None, key: str) -> list[str]:
    """The labels given with ``p`` of 0.5 or more."""
    return [str(x[key]) for x in labels or [] if float(x.get("p", 1.0)) >= 0.5]


@_serialized
def set_genres(
    con: sqlite3.Connection,
    doc_id: int,
    genres: list[str] | None,
    *,
    subjects: list[str] | None = None,
    by: str = "human",
    skip: bool = False,
    p: dict[str, float] | None = None,
    note: str | None = None,
    run: str | None = None,
) -> dict[str, Any]:
    """A document's genres and subjects, checked against
    ``ontology.genres()`` and ``ontology.subjects()``. A genre is
    required; subjects may be none (an invoice is about nothing in
    particular). ``skip``: the person could not place it, and it leaves
    the sample. ``genres=None`` without ``skip`` takes all of it back.

    A label under a level brings its level with it (``Facet.implied``):
    "paper" is "informational" too, "politics" is "society".

    ``by`` is ``human`` for a person, else the model that labelled it,
    whose ``p`` gives each label's probability (1.0 where it names none)
    and ``note`` its reason in a line. A model's labels never replace a
    person's (ValueError). A person's labels over a model's keep the
    model's as ``meta.genres_model``."""
    if document_hidden(con, doc_id):
        raise KeyError(f"no such document: {doc_id}")
    meta = get_meta(con, doc_id)
    human = by == "human"
    if not human and meta.get("genres_by") == "human":
        raise ValueError("a person labelled this document: a model does not relabel it")
    before = meta.get("genres_by")
    if human and before and before != "human" and meta.get("genres"):
        meta["genres_model"] = {
            "by": before,
            "at": meta.get("genres_at"),
            "genres": meta.get("genres"),
            "subjects": meta.get("subjects") or [],
            "note": meta.get("genres_note"),
        }
    for key in GENRE_KEYS:
        meta.pop(key, None)
    sure = {k: max(0.0, min(1.0, float(v))) for k, v in (p or {}).items()}
    if skip:
        meta["genres_skip"] = now()
    elif genres is not None:
        gv, sv = ontology.genres(), ontology.subjects()
        if not gv.check(genres):
            raise ValueError("no genre given: skip the document instead")
        # a genre is its level too, a subject its group: added here, with
        # the probability of the surest label under it when a model gave
        # none of its own
        labels = gv.implied(genres)
        about = sv.implied(subjects or [])
        for facet, chosen in ((gv, labels), (sv, about)):
            for level in chosen:
                if level in sure or facet.level_of(level) != level:
                    continue
                under = [
                    sure.get(x, 1.0)
                    for x in chosen
                    if x != level and facet.level_of(x) == level
                ]
                if under:
                    sure[level] = max(under)
        meta["genres"] = [
            {"genre": g, "p": 1.0 if human else sure.get(g, 1.0)} for g in labels
        ]
        if about:
            meta["subjects"] = [
                {"subject": x, "p": 1.0 if human else sure.get(x, 1.0)} for x in about
            ]
        meta["genres_by"] = by
        meta["genres_at"] = now()
        if note and not human:
            meta["genres_note"] = " ".join(note.split())[:300]
        if run and not human:
            meta["genres_run"] = run
    _put_meta(con, doc_id, meta)
    con.commit()
    return {
        "doc_id": doc_id,
        "genres": [g["genre"] for g in meta.get("genres") or []],
        "subjects": [x["subject"] for x in meta.get("subjects") or []],
        "skipped": bool(meta.get("genres_skip")),
    }


@_reading
def genre_training(
    con: sqlite3.Connection, *, opening: int = GENRE_OPENING + 700
) -> list[dict[str, Any]]:
    """Every labelled document for training the small model
    (`scripts/train_labeller.py`): its title, meta, the opening of its
    text, who labelled it (``by``), whether it is a person's blind label
    (``blind``: labelled with nothing ticked in front of them, the measure
    a model is never trained on), and its labels at 0.5 or more."""
    out = []
    for r in con.execute(
        "SELECT id, title, meta, text_hash, source_url, original_path FROM documents"
        " WHERE json_extract(meta, '$.genres_by') IS NOT NULL"
        " AND json_extract(meta, '$.retired') IS NULL ORDER BY id"
    ).fetchall():
        meta = json.loads(r["meta"] or "{}")
        text = ""
        if r["text_hash"]:
            try:
                text = _read_archive(r["text_hash"]).decode("utf-8", "replace")[
                    :opening
                ]
            except (OSError, KeyError):
                text = ""
        by = meta.get("genres_by")
        out.append(
            {
                "id": r["id"],
                "title": r["title"],
                "meta": meta,
                "text": text,
                "where": (meta.get("origin") or {}).get("path")
                or r["source_url"]
                or r["original_path"],
                "by": by,
                "blind": by == "human" and not meta.get("genres_model"),
                "g": _sure(meta.get("genres"), "genre"),
                "s": _sure(meta.get("subjects"), "subject"),
            }
        )
    return out


@_reading
def genres_needed(
    con: sqlite3.Connection, *, limit: int = 50, current: str | None = None
) -> list[int]:
    """The documents the genres step labels next: the open ones
    (``_GENRE_OPEN``) a pass has not tried without result, the newest
    first, so a capture is labelled on the night it arrives. With
    ``current`` (the labeller run in use, ``labeller:<run>``), then the
    documents an older labeller run labelled: a new run makes its
    predecessor's labels stale. A person's labels and another model's
    are never taken."""
    limit = max(1, limit)
    rows = con.execute(
        "SELECT id" + _GENRE_OPEN + " AND json_extract(meta, '$.genres_tried') IS NULL"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = [int(r[0]) for r in rows]
    if current and len(out) < limit:
        stale = con.execute(
            "SELECT id FROM documents"
            " WHERE json_extract(meta, '$.genres_by') LIKE 'labeller:%'"
            " AND json_extract(meta, '$.genres_by') != ?"
            " AND json_extract(meta, '$.retired') IS NULL"
            # tried by the new run and nothing kept: the old labels stay,
            # and it is not handed out again (set_genres clears the mark)
            " AND json_extract(meta, '$.genres_tried') IS NULL"
            " ORDER BY id DESC LIMIT ?",
            (current, limit - len(out)),
        ).fetchall()
        out += [int(r[0]) for r in stale]
    return out


@_serialized
def genres_tried(con: sqlite3.Connection, doc_id: int, run: str, why: str) -> None:
    """A pass asked and kept no genre: the document is not handed out
    again until its labels are taken back (``set_genres`` with None)."""
    meta = get_meta(con, doc_id)
    meta["genres_tried"] = {"run": run, "why": why[:200], "at": now()}
    _put_meta(con, doc_id, meta)
    con.commit()


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
        "subjects": [x.get("subject") for x in meta.get("subjects") or []],
        "genres_by": meta.get("genres_by"),
        "genres_at": meta.get("genres_at"),
        "p": {
            **{g["genre"]: g.get("p") for g in meta.get("genres") or []},
            **{x["subject"]: x.get("p") for x in meta.get("subjects") or []},
        },
        "note": meta.get("genres_note"),
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
    the person's labels, the last first, where one is corrected.
    ``check``: a model's labels no person has looked at, the least sure
    first; ``ruled``: the same for the documents a domain rule placed by
    those labels, where a wrong one costs the most. Each item carries its
    summary and the opening of its text.
    ``labelled``, ``to_check`` and ``skipped`` count what is done so far."""
    if state not in ("open", "labelled", "check", "ruled"):
        raise ValueError("state is open, check, ruled or labelled")
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
    elif state in ("check", "ruled"):
        where = _GENRE_CHECK if state == "check" else _GENRE_RULED
        total = con.execute("SELECT count(*)" + where).fetchone()[0]
        rows = con.execute(
            f"SELECT {cols}" + where + f" ORDER BY {_GENRE_SURE}, id LIMIT ? OFFSET ?",
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
        " sum(json_extract(meta, '$.genres_skip') IS NOT NULL),"
        " sum(json_extract(meta, '$.genres_by') != 'human') FROM documents"
    ).fetchone()
    return {
        "total": int(total),
        "labelled": int(done[0] or 0),
        "skipped": int(done[1] or 0),
        "to_check": int(done[2] or 0),
        "items": [_genre_item(r, opening) for r in rows],
    }
