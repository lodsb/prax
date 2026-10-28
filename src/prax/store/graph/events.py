"""Events from photos: groups of pictures taken close together, found
without a model and named by a person (``docs/biography.md`` §7, core 6).

A group is a run of dated pictures with no gap of ``EVENT_GAP_HOURS`` or
more between two of them, split where the country changes between two
placed ones (``meta.place``), and kept when it holds ``EVENT_MIN_PHOTOS``.
Measured on 2026-10-26 over 12,983 dated photos: a 24-hour gap gives 165
groups of eight or more, holding 11,184 photos, most of them one day
long and the longest twenty days. A gap of 12 hours split a week away
into its days; one of 36 joined weekends at home to the week after.

A group is a candidate (``event_candidates``) until a person decides on
the Review page. "An event", with a name, writes each photo
``recorded_at`` the event and the event ``located_in`` the place most of
its photos were taken in, producer ``human`` under a run of the decision,
each with its photo as the source, so the wall hides them with it. "Not
one" is kept, so the group is not asked again. A name another event
already has joins that event: two weekends in one place named alike are
one event. ``undo_event`` ends the run and opens the candidate again.

What a group is called to start with is a suggestion: the name of the
folder most of its photos lie in, where that name has a letter in it
(icloudpd's ``2019/05/03`` says nothing a date does not), else its
place and its months, or its days where two groups would share the
months (25 of the 165, 2026-10-26).

A group the rule got wrong (a trip and the weeks at home after it: 2,352
photos in twenty days) is split by a person. ``split_event`` keeps a cut
at a moment (``event_cuts``, migration 52), and every pass starts a group
again there. The page offers a cut before each burst, a run of photos
with no gap of ``EVENT_BURST_HOURS`` (36 in that group, 3 in a day of
1,020 photos). ``join_event`` takes a cut back.
"""

from __future__ import annotations

import json
import re
import sqlite3
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from prax.text import mimes

from ..base import _reading, _serialized, now
from .edges import Edge, document_node, link, retire_run
from .labels import PERSON

EVENT_GAP_HOURS = 24
EVENT_MIN_PHOTOS = 8
# a place names the event when this share of its placed photos is there
EVENT_PLACE_SHARE = 0.6
EVENT_SAMPLE = 8  # the thumbnails a candidate shows
EVENT_NAME_CHARS = 120
EVENT_STATES = ("open", "event", "not")
# where the page offers a cut: before a run of photos with no gap this long
EVENT_BURST_HOURS = 3
EVENT_BURST_SAMPLE = 6
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_LETTER = re.compile(r"[^\W\d_]")


def _when(taken: str) -> datetime | None:
    try:
        return datetime.fromisoformat(taken[:19])
    except ValueError:
        return None


def _months(first: datetime, last: datetime) -> str:
    """``May 2018``, ``May – June 2018``, ``December 2019 – January 2020``."""
    a, b = _MONTH_NAMES[first.month - 1], _MONTH_NAMES[last.month - 1]
    if (first.year, first.month) == (last.year, last.month):
        return f"{a} {first.year}"
    if first.year == last.year:
        return f"{a} – {b} {first.year}"
    return f"{a} {first.year} – {b} {last.year}"


def _days(first: datetime, last: datetime) -> str:
    """``3 May 2018``, ``3 – 5 May 2018``, ``30 May – 2 June 2018``."""
    a, b = _MONTH_NAMES[first.month - 1], _MONTH_NAMES[last.month - 1]
    if first.date() == last.date():
        return f"{first.day} {a} {first.year}"
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day} – {last.day} {a} {first.year}"
    if first.year == last.year:
        return f"{first.day} {a} – {last.day} {b} {first.year}"
    return f"{first.day} {a} {first.year} – {last.day} {b} {last.year}"


def _folder_name(path: str) -> str:
    """The last folder of a path as a name, when it has a letter in it."""
    parts = path.replace("\\", "/").split("/")
    last = parts[-2] if len(parts) > 1 else ""
    if not _LETTER.search(last):
        return ""
    return " ".join(re.sub(r"[_+]+", " ", last).split())


def _dominant(values: list[str], share: float) -> str:
    if not values:
        return ""
    name, n = Counter(values).most_common(1)[0]
    return name if n >= share * len(values) else ""


@_reading
def photo_groups(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """The groups of dated pictures (the module's rule), each with its
    ``docs`` in the order taken, ``first`` and ``last`` (ISO), the
    ``place`` most of them were taken in (a town, else a country, else
    ``""``), its ``folder`` cue and the ``suggestion``. Reads only."""
    photos: dict[int, dict[str, Any]] = {}
    for r in con.execute(
        "SELECT d.id, d.mime, json_extract(d.meta, '$.taken.date') AS taken,"
        " json_extract(d.meta, '$.place.name') AS town,"
        " json_extract(d.meta, '$.place.country') AS country, p.path"
        " FROM documents d LEFT JOIN places p ON p.sha256 = d.hash AND p.gone IS NULL"
        " WHERE json_extract(d.meta, '$.taken.date') IS NOT NULL"
        " AND json_extract(d.meta, '$.retired') IS NULL"
    ):
        if not mimes.is_picture(r["mime"]):
            continue
        when = _when(str(r["taken"]))
        if when is None:
            continue
        got = photos.setdefault(
            int(r["id"]),
            {
                "id": int(r["id"]),
                "when": when,
                "town": r["town"] or "",
                "country": r["country"] or "",
                "folders": set(),
            },
        )
        if r["path"]:
            got["folders"].add(_folder_name(str(r["path"])))
    ordered = sorted(photos.values(), key=lambda p: (p["when"], p["id"]))
    cuts = sorted(
        (when, str(r[0]))
        for r in con.execute("SELECT at FROM event_cuts")
        if (when := _when(str(r[0]))) is not None
    )
    moments = [c[0] for c in cuts]
    runs: list[list[dict[str, Any]]] = []
    starts: list[str] = []  # the cut a run starts at, or ""
    for p in ordered:
        last = runs[-1][-1] if runs else None
        cut = ""
        if last is not None:
            i = bisect_right(moments, last["when"])
            if i < len(cuts) and cuts[i][0] <= p["when"]:
                cut = cuts[i][1]
        if (
            last is None
            or cut
            or (p["when"] - last["when"]).total_seconds() >= EVENT_GAP_HOURS * 3600
            or (p["country"] and last["country"] and p["country"] != last["country"])
        ):
            runs.append([p])
            starts.append(cut)
        else:
            runs[-1].append(p)
    out = []
    for run, cut in zip(runs, starts, strict=True):
        if len(run) < EVENT_MIN_PHOTOS:
            continue
        first, last = run[0]["when"], run[-1]["when"]
        placed = [p for p in run if p["town"]]
        place = _dominant([p["town"] for p in placed], EVENT_PLACE_SHARE)
        country = _dominant([p["country"] for p in placed], EVENT_PLACE_SHARE)
        named = [f for p in run for f in p["folders"] if f]
        folder = _dominant(named, 0.5) if len(named) >= len(run) / 2 else ""
        where = f"{place}, {country}" if place and country != place else place
        where = where or country
        months = _months(first, last)
        suggestion = folder or (f"{where}, {months}" if where else months)
        out.append(
            {
                "docs": [p["id"] for p in run],
                "first": first.isoformat(),
                "last": last.isoformat(),
                "place": place or country,
                "where": where,
                "folder": folder,
                "placed": len(placed),
                "suggestion": suggestion,
                "days": _days(first, last),
                "cut": cut,
            }
        )
    # a month two groups share is not one event, as a folder two share
    # may be: those name their days, so taking the suggestion joins nothing
    shared = Counter(g["suggestion"] for g in out if not g["folder"])
    for g in out:
        if not g["folder"] and shared[g["suggestion"]] > 1:
            g["suggestion"] = f"{g['where']}, {g['days']}" if g["where"] else g["days"]
    return out


@_serialized
def replace_event_candidates(
    con: sqlite3.Connection, groups: list[dict[str, Any]]
) -> dict[str, int]:
    """The open candidates replaced by ``groups``. A group half of whose
    photos are in a decided candidate is that candidate's, and is left
    out: what a person decided is asked again only when they undo it. A
    group of the same photos as an open candidate keeps its id, and a new
    one takes an id no candidate ever had, so a page shown before the
    groups were found again decides the photos it showed or none (the
    review of 2026-10-28)."""
    decided = {
        int(r[0])
        for r in con.execute(
            "SELECT p.doc_id FROM event_candidate_photos p JOIN event_candidates c"
            " ON c.id = p.candidate_id WHERE c.state != 'open'"
        )
    }
    held: dict[frozenset[int], int] = {}
    for r in con.execute(
        "SELECT c.id, json_group_array(p.doc_id) FROM event_candidates c"
        " JOIN event_candidate_photos p ON p.candidate_id = c.id"
        " WHERE c.state = 'open' GROUP BY c.id"
    ):
        held[frozenset(json.loads(r[1]))] = int(r[0])
    mark = con.execute(
        "SELECT last FROM id_marks WHERE name = 'event_candidates'"
    ).fetchone()
    top = con.execute("SELECT coalesce(max(id), 0) FROM event_candidates").fetchone()
    fresh = max(int(mark[0]) if mark else 0, int(top[0])) + 1
    kept: set[int] = set()
    at = now()
    found = skipped = 0
    rows: list[tuple[int | None, dict[str, Any]]] = []
    for g in groups:
        if sum(d in decided for d in g["docs"]) * 2 >= len(g["docs"]):
            skipped += 1
            continue
        same = held.get(frozenset(g["docs"]))
        if same is not None and same not in kept:
            kept.add(same)
            rows.append((same, g))
        else:
            rows.append((None, g))
    con.execute(
        "DELETE FROM event_candidates WHERE state = 'open'"
        " AND id NOT IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(kept)),),
    )
    for same, g in rows:
        cues = {
            "place": g["place"],
            "where": g["where"],
            "folder": g["folder"],
            "placed": g["placed"],
            "cut": g.get("cut", ""),
        }
        if same is not None:
            con.execute(
                "UPDATE event_candidates SET suggestion = ?, cues = ?, found_at = ?"
                " WHERE id = ?",
                (g["suggestion"], json.dumps(cues), at, same),
            )
            found += 1
            continue
        con.execute(
            "INSERT INTO event_candidates (id, first_taken, last_taken, photos,"
            " suggestion, cues, found_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                fresh,
                g["first"],
                g["last"],
                len(g["docs"]),
                g["suggestion"],
                json.dumps(cues),
                at,
            ),
        )
        con.executemany(
            "INSERT INTO event_candidate_photos (candidate_id, doc_id) VALUES (?, ?)",
            [(fresh, d) for d in g["docs"]],
        )
        fresh += 1
        found += 1
    con.execute(
        "INSERT INTO id_marks (name, last) VALUES ('event_candidates', ?)"
        " ON CONFLICT (name) DO UPDATE SET last = max(last, excluded.last)",
        (fresh - 1,),
    )
    con.commit()
    return {"candidates": found, "decided_before": skipped}


def find_event_candidates(con: sqlite3.Connection) -> dict[str, int]:
    """The groups found again, read holding nothing and written in one
    short write (``photo_groups``, ``replace_event_candidates``)."""
    return replace_event_candidates(con, photo_groups(con))


def _open_candidate(con: sqlite3.Connection, candidate_id: int) -> sqlite3.Row:
    row: sqlite3.Row | None = con.execute(
        "SELECT * FROM event_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such candidate: {candidate_id}")
    if row["state"] != "open":
        raise ValueError(f"candidate {candidate_id} is decided already: undo it first")
    return row


@_reading
def event_timeline(con: sqlite3.Connection, candidate_id: int) -> dict[str, Any]:
    """A candidate's photos as its bursts, in the order taken: each burst's
    ``first`` and ``last`` moment, its count and a sample to show. A cut
    may go before any burst but the first (``split_event``)."""
    row = con.execute(
        "SELECT id, state FROM event_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such candidate: {candidate_id}")
    bursts: list[dict[str, Any]] = []
    last: datetime | None = None
    for r in con.execute(
        "SELECT p.doc_id, json_extract(d.meta, '$.taken.date') AS taken"
        " FROM event_candidate_photos p JOIN documents d ON d.id = p.doc_id"
        " WHERE p.candidate_id = ? ORDER BY taken, p.doc_id",
        (candidate_id,),
    ):
        when = _when(str(r["taken"] or ""))
        if when is None:
            continue
        if last is None or (when - last).total_seconds() >= EVENT_BURST_HOURS * 3600:
            bursts.append({"first": when.isoformat(), "docs": []})
        bursts[-1]["last"] = when.isoformat()
        bursts[-1]["docs"].append(int(r["doc_id"]))
        last = when
    for b in bursts:
        docs = b.pop("docs")
        step = max(1, len(docs) // EVENT_BURST_SAMPLE)
        b["photos"] = len(docs)
        b["sample"] = docs[::step][:EVENT_BURST_SAMPLE]
    return {"id": int(row["id"]), "state": row["state"], "bursts": bursts}


@_serialized
def _add_cut(con: sqlite3.Connection, candidate_id: int, at: str, by: str) -> str:
    row = _open_candidate(con, candidate_id)
    when = _when(at)
    first, last = _when(row["first_taken"]), _when(row["last_taken"])
    if when is None or first is None or last is None or not first < when <= last:
        raise ValueError("a cut lies inside the group, after its first photo")
    con.execute(
        "INSERT OR IGNORE INTO event_cuts (at, by, made_at) VALUES (?, ?, ?)",
        (when.isoformat(), by, now()),
    )
    con.commit()
    return when.isoformat()


def split_event(
    con: sqlite3.Connection, candidate_id: int, *, at: str, by: str = PERSON
) -> dict[str, Any]:
    """An open candidate split before the photos taken at or after ``at``:
    the cut kept and the groups found again, so each piece is a candidate
    of its own (or none, under ``EVENT_MIN_PHOTOS``)."""
    cut = _add_cut(con, candidate_id, at, by)
    return {"cut": cut, **find_event_candidates(con)}


@_serialized
def _drop_cut(con: sqlite3.Connection, candidate_id: int) -> str:
    row = _open_candidate(con, candidate_id)
    cut = str(json.loads(row["cues"] or "{}").get("cut") or "")
    if not cut:
        raise ValueError(f"candidate {candidate_id} was not split from the one before")
    # a decided group that ends within the gap before the cut would be
    # joined to an open one; one further back is not reached
    start = _when(row["first_taken"])
    reach = (start - timedelta(hours=EVENT_GAP_HOURS)).isoformat() if start else ""
    before = con.execute(
        "SELECT 1 FROM event_candidates WHERE state != 'open'"
        " AND last_taken < ? AND last_taken >= ?",
        (row["first_taken"], reach),
    ).fetchone()
    if before is not None:
        raise ValueError("the group before is decided: undo it first")
    con.execute("DELETE FROM event_cuts WHERE at = ?", (cut,))
    con.commit()
    return cut


def join_event(con: sqlite3.Connection, candidate_id: int) -> dict[str, Any]:
    """A cut taken back: the candidate that starts at it joins the group
    before it again, and the groups are found again."""
    cut = _drop_cut(con, candidate_id)
    return {"joined": cut, **find_event_candidates(con)}


def _candidate(con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    docs = [
        int(r[0])
        for r in con.execute(
            "SELECT p.doc_id FROM event_candidate_photos p JOIN documents d"
            " ON d.id = p.doc_id WHERE p.candidate_id = ?"
            " ORDER BY json_extract(d.meta, '$.taken.date'), p.doc_id",
            (row["id"],),
        )
    ]
    step = max(1, len(docs) // EVENT_SAMPLE)
    return {
        "id": int(row["id"]),
        "first": row["first_taken"],
        "last": row["last_taken"],
        "photos": int(row["photos"]),
        "suggestion": row["suggestion"],
        "cues": json.loads(row["cues"] or "{}"),
        "state": row["state"],
        "name": row["name"],
        "decided_at": row["decided_at"],
        "sample": docs[::step][:EVENT_SAMPLE],
    }


@_reading
def event_candidates_page(
    con: sqlite3.Connection, *, state: str = "open", offset: int = 0, limit: int = 20
) -> dict[str, Any]:
    """One page of candidates in a state, oldest first, each with a sample
    of its photos to show, and the count in every state."""
    if state not in EVENT_STATES:
        raise ValueError(f"state is one of {EVENT_STATES}")
    counts = dict.fromkeys(EVENT_STATES, 0)
    for r in con.execute("SELECT state, count(*) FROM event_candidates GROUP BY state"):
        counts[r[0]] = int(r[1])
    rows = con.execute(
        "SELECT * FROM event_candidates WHERE state = ?"
        " ORDER BY first_taken, id LIMIT ? OFFSET ?",
        (state, limit, offset),
    ).fetchall()
    return {
        "state": state,
        "total": counts[state],
        "counts": counts,
        "items": [_candidate(con, r) for r in rows],
    }


@_serialized
def decide_event(
    con: sqlite3.Connection,
    candidate_id: int,
    *,
    event: bool,
    name: str | None = None,
    by: str = PERSON,
) -> dict[str, Any]:
    """A person's answer for a candidate: an event called ``name`` (its
    facts written, the module's rule) or not one. Returns the candidate's
    state, and for an event its ``name``, ``entity_id``, ``run`` and how
    many ``facts`` were written."""
    row = _open_candidate(con, candidate_id)
    if not event:
        con.execute(
            "UPDATE event_candidates SET state = 'not', decided_at = ? WHERE id = ?",
            (now(), candidate_id),
        )
        con.commit()
        return {"id": candidate_id, "state": "not"}
    title = " ".join((name or "").split())[:EVENT_NAME_CHARS]
    if not title:
        raise ValueError("an event needs a name")
    run = f"event-{now().replace(':', '').replace('-', '')}-{candidate_id}"
    first_day, last_day = row["first_taken"][:10], row["last_taken"][:10]
    said = (
        f"one of {row['photos']} photos taken {first_day} to {last_day},"
        f" named {title!r} by a person"
    )
    members = con.execute(
        "SELECT d.id, json_extract(d.meta, '$.taken.date') AS taken,"
        " json_extract(d.meta, '$.place.name') AS town,"
        " json_extract(d.meta, '$.place.country') AS country"
        " FROM event_candidate_photos p JOIN documents d ON d.id = p.doc_id"
        " WHERE p.candidate_id = ? AND json_extract(d.meta, '$.retired') IS NULL"
        " ORDER BY taken, d.id",
        (candidate_id,),
    ).fetchall()
    facts = 0
    for m in members:
        try:
            node, ntype = document_node(con, int(m["id"]))
        except KeyError:
            continue  # no title, or gone
        day = str(m["taken"] or "")[:10]
        link(
            con,
            Edge(node, ntype, "recorded_at", title, "event"),
            source_doc=int(m["id"]),
            evidence=said,
            producer=by,
            run=run,
            world_from=day if len(day) == 10 else None,
        )
        facts += 1
    place = str(json.loads(row["cues"] or "{}").get("place") or "")
    where = next(
        (m for m in members if place and place in (m["town"], m["country"])), None
    )
    if where is not None:
        link(
            con,
            Edge(title, "event", "located_in", place, "place"),
            source_doc=int(where["id"]),
            evidence=f"most of its photos were taken in {place}",
            producer=by,
            run=run,
            world_from=first_day,
            world_to=last_day,
        )
        facts += 1
    entity = con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = 'event'", (title,)
    ).fetchone()
    con.execute(
        "UPDATE event_candidates SET state = 'event', name = ?, entity_id = ?,"
        " run = ?, decided_at = ? WHERE id = ?",
        (title, entity[0] if entity else None, run, now(), candidate_id),
    )
    con.commit()
    return {
        "id": candidate_id,
        "state": "event",
        "name": title,
        "entity_id": entity[0] if entity else None,
        "run": run,
        "facts": facts,
    }


@_serialized
def undo_event(con: sqlite3.Connection, candidate_id: int) -> dict[str, Any]:
    """A decision taken back: an event's facts ended (its run), and the
    candidate open again."""
    row = con.execute(
        "SELECT state, run FROM event_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such candidate: {candidate_id}")
    ended = retire_run(con, run=row["run"]) if row["run"] else 0
    con.execute(
        "UPDATE event_candidates SET state = 'open', name = NULL, entity_id = NULL,"
        " run = NULL, decided_at = NULL WHERE id = ?",
        (candidate_id,),
    )
    con.commit()
    return {"id": candidate_id, "state": "open", "ended": ended}
