"""The photos and recordings prax references and does not hold, as a library
of their own (plan item AQ, "A media view"; ``docs/media-by-reference.md``):
grouped by the folder they lie in, by when they were taken, or by the
event a person grouped them into, with a group released or marked personal
in one decision.

A referenced original is suspected personal from its registration
(``private.referenced``), so the whole share is kept from every restricted
token until a person says otherwise. ``decide_media`` is that person's
answer for a group: it changes only the members no person has decided
about, so a photo marked personal by hand stays personal when its folder
is released. Each member keeps what it was (``meta.sensitivity.before``)
under the decision's run, which ``undo_media`` puts back.

A member is a live referenced document (``held`` 0, not retired). Its
folder is that of its first place, by root and path: a file with copies in
two folders is in the first of them.
"""

from __future__ import annotations

import json
import posixpath
import sqlite3
from collections import defaultdict
from typing import Any

from ..base import _reading, _serialized, now, run_name

MEDIA_BY = ("folder", "month", "event")
MEDIA_STATES = ("suspected", "open", "personal")
MEDIA_PAGE = 40  # pictures of a group a page
MEDIA_PAGE_MAX = 100
UNDATED = "undated"  # the month key of a member without a taken date

_MEMBERS = """
    SELECT d.id, d.mime, d.sensitivity, json_extract(d.meta, '$.taken.date') AS taken,
           (SELECT p.root || ':' || p.path FROM places p
            WHERE p.sha256 = d.hash AND p.gone IS NULL
            ORDER BY p.root, p.path LIMIT 1) AS place
    FROM documents d
    WHERE d.held = 0 AND json_extract(d.meta, '$.retired') IS NULL
"""


def _members(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(_MEMBERS).fetchall()


def _folder(place: str | None) -> str:
    """``root:dir/sub`` of a place ``root:dir/sub/file``; ``""`` for none."""
    if not place:
        return ""
    root, _, path = place.partition(":")
    folder = posixpath.dirname(path.replace("\\", "/"))
    return f"{root}:{folder}" if folder else f"{root}:"


def _month(taken: str | None) -> str:
    return taken[:7] if taken and len(taken) >= 7 else UNDATED


def _state(sensitivity: str | None) -> str:
    return sensitivity if sensitivity in ("suspected", "personal") else "open"


def _kind(mime: str | None) -> str:
    return (mime or "").split("/", 1)[0] or "other"


def _events(
    con: sqlite3.Connection, rows: list[sqlite3.Row]
) -> dict[int, tuple[int, str]]:
    """Each member's event: the canonical entity it is ``recorded_at`` and
    that entity's name. Read by the members' documents, which an index
    serves; no index serves a relation, and a scan of the edges for one was
    a second on the live store (2026-10-29)."""
    out: dict[int, tuple[int, str]] = {}
    for r in con.execute(
        "SELECT x.source_doc, c.id, c.name FROM json_each(?) j"
        " JOIN edges x ON x.source_doc = j.value"
        " JOIN entities e ON e.id = x.dst"
        " JOIN entities c ON c.id = COALESCE(e.canonical_id, e.id)"
        " WHERE x.rel = 'recorded_at' AND x.valid_to IS NULL ORDER BY x.id",
        (json.dumps([int(r["id"]) for r in rows]),),
    ):
        out.setdefault(int(r[0]), (int(r[1]), str(r[2])))
    return out


def _key(
    by: str, row: sqlite3.Row, under: str, events: dict[int, tuple[int, str]]
) -> tuple[str, str] | None:
    """The group a member is in one level below ``under`` (its key and its
    label), or None when it is not under it."""
    if by == "event":
        got = events.get(int(row["id"]))
        return (str(got[0]), got[1]) if got else None
    if by == "month":
        month = _month(row["taken"])
        if not under:
            year = UNDATED if month == UNDATED else month[:4]
            return year, year
        if month == under or month.startswith(under + "-"):
            return month, month
        return None
    folder = _folder(row["place"])
    if not under:
        root = folder.partition(":")[0]
        return f"{root}:", root
    if folder == under:
        return under, "(here)"
    prefix = under if under.endswith(":") else under + "/"
    if not folder.startswith(prefix):
        return None
    step = folder[len(prefix) :].split("/", 1)[0]
    return prefix + step, step


def _descend(folders: list[str], under: str) -> str:
    """``under``, or the folder below it every member under it lies in: a
    share whose files are all in one folder some levels down opens there,
    not on a chain of folders of one folder each."""
    while True:
        if not under:
            roots = {f.partition(":")[0] + ":" for f in folders if f}
            if len(roots) != 1:
                return under
            under = roots.pop()
            continue
        prefix = under if under.endswith(":") else under + "/"
        inside = [f for f in folders if f == under or f.startswith(prefix)]
        steps = {f[len(prefix) :].split("/", 1)[0] for f in inside if f != under}
        if len(steps) != 1 or under in inside:
            return under
        under = prefix + steps.pop()


@_reading
def media_groups(
    con: sqlite3.Connection, *, by: str = "folder", under: str = ""
) -> dict[str, Any]:
    """The groups one level below ``under``: a folder's folders (``root:``
    and a path; ``""`` for the roots), a year's months (``""`` for the
    years), or every event. Each with how many members it holds by state
    and by kind; a folder's own files are the group ``(here)``. In key
    order, an event's by name."""
    if by not in MEDIA_BY:
        raise ValueError(f"by is one of {MEDIA_BY}")
    rows = _members(con)
    events = _events(con, rows) if by == "event" else {}
    if by == "folder":
        under = _descend([_folder(r["place"]) for r in rows], under)
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        got = _key(by, row, under, events)
        if got is None:
            continue
        key, label = got
        g = groups.setdefault(
            key,
            {"key": key, "label": label, "count": 0, **dict.fromkeys(MEDIA_STATES, 0)},
        )
        g["count"] += 1
        g[_state(row["sensitivity"])] += 1
        kinds = g.setdefault("kinds", {})
        kinds[_kind(row["mime"])] = kinds.get(_kind(row["mime"]), 0) + 1
    order = sorted(
        groups.values(),
        key=lambda g: (g["label"].lower(), g["key"]) if by == "event" else g["key"],
    )
    return {"by": by, "under": under, "groups": order}


def _in_group(
    by: str, key: str, row: sqlite3.Row, events: dict[int, tuple[int, str]]
) -> bool:
    if by == "event":
        got = events.get(int(row["id"]))
        return got is not None and str(got[0]) == key
    if by == "month":
        month = _month(row["taken"])
        return month == key or (key != UNDATED and month.startswith(key + "-"))
    folder = _folder(row["place"])
    if key.endswith(":"):
        return folder.startswith(key)
    return folder == key or folder.startswith(key + "/")


@_reading
def media_members(con: sqlite3.Connection, by: str, key: str) -> list[sqlite3.Row]:
    """The members of a group and every group under it, taken first and the
    undated last: a folder with its folders, a year with its months."""
    if by not in MEDIA_BY:
        raise ValueError(f"by is one of {MEDIA_BY}")
    if not key:
        raise ValueError("a group's key")
    members = _members(con)
    events = _events(con, members) if by == "event" else {}
    rows = [r for r in members if _in_group(by, key, r, events)]
    return sorted(rows, key=lambda r: (r["taken"] is None, r["taken"] or "", r["id"]))


@_reading
def media_pictures(
    con: sqlite3.Connection,
    by: str,
    key: str,
    *,
    offset: int = 0,
    limit: int = MEDIA_PAGE,
) -> dict[str, Any]:
    """One page of a group for the gallery: ``{total, offset, limit,
    items}``, each item ``{doc_id, taken, title, kind, state, thumb}``:
    ``thumb`` false when the photos step made no copy to show (a video, a
    picture not read yet)."""
    rows = media_members(con, by, key)
    limit = max(1, min(limit, MEDIA_PAGE_MAX))
    offset = max(0, offset)
    page = rows[offset : offset + limit]
    shown = {
        int(r[0]): (r[1], bool(r[2]))
        for r in con.execute(
            "SELECT id, title, json_extract(meta, '$.photo.thumb') FROM documents"
            " WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps([int(r["id"]) for r in page]),),
        )
    }
    return {
        "by": by,
        "key": key,
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "doc_id": int(r["id"]),
                "taken": r["taken"],
                "title": shown.get(int(r["id"]), (None, False))[0],
                "thumb": shown.get(int(r["id"]), (None, False))[1],
                "kind": _kind(r["mime"]),
                "state": _state(r["sensitivity"]),
            }
            for r in page
        ],
    }


@_serialized
def decide_media(
    con: sqlite3.Connection, by: str, key: str, state: str, *, who: str = "human"
) -> dict[str, Any]:
    """A person's answer for a group: ``open`` releases it, ``personal``
    keeps it from every restricted token for good. Only the members still
    suspected and decided by no person change; each keeps what it was
    under the decision's run (``undo_media``). Returns the run and how many
    changed and were left."""
    if state not in ("open", "personal"):
        raise ValueError("state is open or personal")
    rows = media_members(con, by, key)
    run = run_name(f"media-{state}", unique=True)
    at = now()
    changed = 0
    for r in rows:
        meta_row = con.execute(
            "SELECT sensitivity, meta FROM documents WHERE id = ?", (r["id"],)
        ).fetchone()
        meta = json.loads(meta_row["meta"] or "{}")
        before = meta.get("sensitivity")
        if (
            meta_row["sensitivity"] != "suspected"
            or (before or {}).get("by") == "human"
        ):
            continue
        meta["sensitivity"] = {
            "state": None if state == "open" else state,
            "by": who,
            "at": at,
            "run": run,
            "before": {"column": meta_row["sensitivity"], "meta": before},
        }
        con.execute(
            "UPDATE documents SET sensitivity = ?, meta = ? WHERE id = ?",
            (None if state == "open" else state, json.dumps(meta), r["id"]),
        )
        changed += 1
    con.commit()
    return {"run": run, "state": state, "changed": changed, "left": len(rows) - changed}


@_serialized
def undo_media(con: sqlite3.Connection, run: str) -> dict[str, Any]:
    """A group's decision taken back: each member it changed has what it had
    before, unless a later decision changed it again."""
    rows = con.execute(
        "SELECT id, meta FROM documents"
        " WHERE json_extract(meta, '$.sensitivity.run') = ?",
        (run,),
    ).fetchall()
    for r in rows:
        meta = json.loads(r["meta"] or "{}")
        before = (meta.get("sensitivity") or {}).get("before") or {}
        if before.get("meta") is None:
            meta.pop("sensitivity", None)
        else:
            meta["sensitivity"] = before["meta"]
        con.execute(
            "UPDATE documents SET sensitivity = ?, meta = ? WHERE id = ?",
            (before.get("column"), json.dumps(meta), r["id"]),
        )
    con.commit()
    return {"run": run, "restored": len(rows)}


@_reading
def media_counts(con: sqlite3.Connection) -> dict[str, int]:
    """How many members there are, by state."""
    out: dict[str, int] = defaultdict(int)
    for r in _members(con):
        out[_state(r["sensitivity"])] += 1
    return {s: out.get(s, 0) for s in MEDIA_STATES}
