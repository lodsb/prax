"""A set of pictures, a page at a time: what the gallery of the web page
shows (``GET /pictures``).

A set is named by one thing: the person they ``depict``, the event they
were ``recorded_at``, an event candidate's photos, or the place they were
``taken_in`` with every place ``located_in`` it. A merged alias's set is
its survivor's. A person's and a place's pictures come newest first, an
event's in the order they were taken. Every set is read through the wall:
a picture the viewer may not see is in neither the page nor the count.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..base import _reading, hidden_documents
from .events import RECORDED_AT
from .people import DEPICTS

PICTURE_SETS = ("depicts", "event", "candidate", "place")
PICTURES_PAGE = 20
PICTURES_PAGE_MAX = 100
PLACE_DEPTH = 4  # a town in its region in its country, and one more
_TAKEN = "json_extract(d.meta, '$.taken.date')"


def _aliases(con: sqlite3.Connection, entity_id: int) -> tuple[int, str, list[int]]:
    """The canonical entity, its name and its alias group, or KeyError."""
    row = con.execute(
        "SELECT COALESCE(canonical_id, id) FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such entity: {entity_id}")
    canon = int(row[0])
    name = con.execute("SELECT name FROM entities WHERE id = ?", (canon,)).fetchone()
    ids = [
        int(r[0])
        for r in con.execute(
            "SELECT id FROM entities WHERE id = ? OR canonical_id = ?", (canon, canon)
        )
    ]
    return canon, str(name[0]), ids


def _with_aliases(con: sqlite3.Connection, canon: set[int]) -> set[int]:
    """The canonical entities and every row folded into them: an edge names
    the row it was written to."""
    marks = json.dumps(sorted(canon))
    return canon | {
        int(r[0])
        for r in con.execute(
            "SELECT id FROM entities WHERE canonical_id IN"
            " (SELECT value FROM json_each(?))",
            (marks,),
        )
    }


def _within(con: sqlite3.Connection, canon: int) -> list[int]:
    """The place and every place located in it, to ``PLACE_DEPTH``, with
    their aliases. One level at a time: the edges are a picture's each, so
    a country's few regions are stated thousands of times."""
    seen = {canon}
    frontier = {canon}
    for _ in range(PLACE_DEPTH):
        rows = con.execute(
            "SELECT DISTINCT COALESCE(x.canonical_id, x.id) FROM edges e"
            " JOIN entities x ON x.id = e.src"
            " WHERE e.rel = 'located_in' AND e.valid_to IS NULL"
            " AND e.dst IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(_with_aliases(con, frontier))),),
        )
        frontier = {int(r[0]) for r in rows} - seen
        if not frontier:
            break
        seen |= frontier
    return sorted(_with_aliases(con, seen))


def _by_edge(rel: str, ends: list[int]) -> tuple[str, list[Any]]:
    sql = (
        "SELECT DISTINCT e.source_doc AS doc FROM edges e"
        " WHERE e.rel = ? AND e.valid_to IS NULL AND e.source_doc IS NOT NULL"
        " AND e.dst IN (SELECT value FROM json_each(?))"
    )
    return sql, [rel, json.dumps(ends)]


@_reading
def picture_set(
    con: sqlite3.Connection,
    *,
    depicts: int | None = None,
    event: int | None = None,
    candidate: int | None = None,
    place: int | None = None,
    offset: int = 0,
    limit: int = PICTURES_PAGE,
) -> dict[str, Any]:
    """One page of a set of pictures: ``{set, id, name, total, offset,
    limit, items}``, each item ``{doc_id, taken, title}``. Exactly one of
    the four names the set. Raises ValueError for none or several, and
    KeyError for an entity or a candidate that is not there."""
    named = {
        k: v
        for k, v in (
            ("depicts", depicts),
            ("event", event),
            ("candidate", candidate),
            ("place", place),
        )
        if v is not None
    }
    if len(named) != 1:
        raise ValueError(f"one of {', '.join(PICTURE_SETS)}")
    ((kind, key),) = named.items()
    limit = max(1, min(int(limit), PICTURES_PAGE_MAX))
    offset = max(0, int(offset))
    if kind == "candidate":
        row = con.execute(
            "SELECT id, COALESCE(name, suggestion) FROM event_candidates WHERE id = ?",
            (key,),
        ).fetchone()
        if row is None:
            raise KeyError(f"no such event candidate: {key}")
        ident, name = int(row[0]), str(row[1] or "")
        members = (
            "SELECT doc_id AS doc FROM event_candidate_photos WHERE candidate_id = ?"
        )
        args: list[Any] = [ident]
    else:
        ident, name, ids = _aliases(con, int(key))
        if kind == "place":
            ids = _within(con, ident)
        rel = {"depicts": DEPICTS, "event": RECORDED_AT, "place": "taken_in"}[kind]
        members, args = _by_edge(rel, ids)
    newest = kind in ("depicts", "place")
    order = "DESC" if newest else "ASC"
    hidden = json.dumps(sorted(hidden_documents(con)))
    base = (
        f"FROM ({members}) m JOIN documents d ON d.id = m.doc"
        " WHERE d.id NOT IN (SELECT value FROM json_each(?))"
        " AND json_extract(d.meta, '$.retired') IS NULL"
    )
    # the whole set in its order once, and only the page's titles: a count
    # and a page each read every member's meta again (2026-10-28, 37 ms of
    # 83 for a country). An undated picture goes last either way.
    ranked = con.execute(
        f"SELECT d.id, {_TAKEN} AS taken {base}"
        f" ORDER BY taken IS NULL, taken {order}, d.id {order}",
        (*args, hidden),
    ).fetchall()
    total = len(ranked)
    page = ranked[offset : offset + limit]
    titles = dict(
        con.execute(
            "SELECT id, title FROM documents WHERE id IN"
            " (SELECT value FROM json_each(?))",
            (json.dumps([int(r[0]) for r in page]),),
        ).fetchall()
    )
    return {
        "set": kind,
        "id": ident,
        "name": name,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {"doc_id": int(r[0]), "taken": r[1], "title": titles.get(r[0]) or ""}
            for r in page
        ],
    }
