"""A person's page: one person's life from every source the library holds
(``docs/biography.md`` §7, plan item AT step 0).

The genealogy and the photos fill the same page. ``person_page`` gives a
person entity's facts (the dated ones in the order they hold in the
world, the undated ones by relation), the documents that say something
about them, and, from the pictures that depict them, the events those
pictures were recorded at, the places they were taken in and the people
photographed with them. A picture says who it depicts once AT names its
faces (``depicts``, picture -> person); until then that half is empty.

Every part is read through the wall: a fact whose document the viewer
may not see is left out, as ``traverse`` leaves it out, and a person no
visible fact speaks of answers as absent. A merged alias's page is the
survivor's.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any

from prax.graph import ontology

from ..base import _reading, hidden_documents
from .edges import hidden_by_premise

PERSON_TYPE = "person"
DEPICTS = "depicts"  # picture -> person: AT's faces write it
PERSON_FACTS_PER_RELATION = 12  # undated facts shown per relation
PERSON_DOCUMENTS = 30
PERSON_PHOTOS = 24  # thumbnails on the page
PERSON_TOP = 12  # events, places and people photographed with them
PEOPLE_LIMIT = 100


def person_types() -> list[str]:
    """``person`` and every type under it (an author is a person)."""
    onto = ontology.current()
    return sorted(t for t in onto.types if onto.is_a(t, PERSON_TYPE))


def _group(con: sqlite3.Connection, entity_id: int) -> tuple[int, list[int]]:
    """The canonical entity and its alias group, or KeyError."""
    row = con.execute(
        "SELECT COALESCE(canonical_id, id) FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no such entity: {entity_id}")
    canon = int(row[0])
    ids = [
        int(r[0])
        for r in con.execute(
            "SELECT id FROM entities WHERE id = ? OR canonical_id = ? ORDER BY id",
            (canon, canon),
        )
    ]
    return canon, ids


def _own_facts(
    con: sqlite3.Connection, ids: list[int], hidden: frozenset[int]
) -> list[dict[str, Any]]:
    """The live edges with the person at one end, as the page shows them:
    the other end (canonical) and which way, through the wall."""
    group = json.dumps(ids)
    rows = []
    for end, other in (("src", "dst"), ("dst", "src")):
        rows += [
            {**dict(r), "dir": "out" if end == "src" else "in"}
            for r in con.execute(
                f"SELECT e.id AS edge_id, e.rel, o.id AS other_id, o.name AS other,"
                f" o.type AS other_type, e.world_from, e.world_to, e.confidence,"
                f" e.producer, e.source_doc AS doc"
                f" FROM edges e JOIN entities x ON x.id = e.{other}"
                f" JOIN entities o ON o.id = COALESCE(x.canonical_id, x.id)"
                f" WHERE e.{end} IN (SELECT value FROM json_each(?))"
                f" AND e.valid_to IS NULL",
                (group,),
            )
        ]
    seen = [r for r in rows if r["doc"] is None or int(r["doc"]) not in hidden]
    derived = [
        int(r["edge_id"])
        for r in seen
        if str(r.get("producer") or "").startswith("rule:")
    ]
    veiled = hidden_by_premise(con, derived, hidden) if derived else set()
    return [r for r in seen if int(r["edge_id"]) not in veiled]


def _photos_part(
    con: sqlite3.Connection, ids: list[int], hidden: frozenset[int]
) -> dict[str, Any]:
    """What the pictures that depict the person say: the pictures, their
    events and places, and who else they depict."""
    marks = ",".join("?" * len(ids))
    pictures: dict[int, str] = {}
    for r in con.execute(
        f"SELECT e.source_doc, json_extract(d.meta, '$.taken.date') AS taken"
        f" FROM edges e JOIN documents d ON d.id = e.source_doc"
        f" WHERE e.rel = ? AND e.dst IN ({marks}) AND e.valid_to IS NULL",
        (DEPICTS, *ids),
    ):
        doc = int(r["source_doc"])
        if doc not in hidden:
            pictures[doc] = str(r["taken"] or "")
    out: dict[str, Any] = {
        "photos": len(pictures),
        "sample": [],
        "events": [],
        "places": [],
        "with": [],
    }
    if not pictures:
        return out
    newest = sorted(pictures, key=lambda d: (pictures[d], d), reverse=True)
    out["sample"] = newest[:PERSON_PHOTOS]
    docs = json.dumps(sorted(pictures))
    group = set(ids)
    events: dict[int, dict[str, Any]] = {}
    places: Counter[str] = Counter()
    others: Counter[tuple[int, str]] = Counter()
    for r in con.execute(
        "SELECT e.source_doc, e.rel, e.world_from, t.id AS tid, t.name AS tname,"
        " COALESCE(t.canonical_id, t.id) AS cid"
        " FROM edges e JOIN entities t ON t.id = e.dst"
        " WHERE e.source_doc IN (SELECT value FROM json_each(?))"
        " AND e.rel IN ('recorded_at', 'taken_in', ?) AND e.valid_to IS NULL",
        (docs, DEPICTS),
    ):
        if r["rel"] == "recorded_at":
            ev = events.setdefault(
                int(r["cid"]),
                {"id": int(r["cid"]), "name": r["tname"], "photos": 0, "days": set()},
            )
            ev["photos"] += 1
            if r["world_from"]:
                ev["days"].add(str(r["world_from"])[:10])
        elif r["rel"] == "taken_in":
            places[str(r["tname"])] += 1
        elif int(r["tid"]) not in group:
            others[(int(r["cid"]), str(r["tname"]))] += 1
    ranked = sorted(events.values(), key=lambda e: (-e["photos"], e["name"]))
    for ev in ranked[:PERSON_TOP]:
        days = sorted(ev.pop("days"))
        ev["first"], ev["last"] = (days[0], days[-1]) if days else (None, None)
    out["events"] = ranked[:PERSON_TOP]
    top = PERSON_TOP
    out["places"] = [{"name": n, "photos": c} for n, c in places.most_common(top)]
    out["with"] = [
        {"id": i, "name": n, "photos": c} for (i, n), c in others.most_common(top)
    ]
    return out


@_reading
def person_page(con: sqlite3.Connection, entity_id: int) -> dict[str, Any]:
    """Everything the page of a person shows (the module's rule).

    Raises KeyError for an entity that is not there or that the viewer
    may not see (no visible fact speaks of it), and ValueError for one
    that is not a person."""
    canon, ids = _group(con, entity_id)
    row = con.execute(
        "SELECT id, name, type FROM entities WHERE id = ?", (canon,)
    ).fetchone()
    if not ontology.current().is_a(str(row["type"]), PERSON_TYPE):
        raise ValueError(f"entity {canon} is a {row['type']}, not a person")
    hidden = hidden_documents(con)
    name = str(row["name"])
    edges = _own_facts(con, ids, hidden)
    if not edges:
        raise KeyError(f"no such entity: {entity_id}")
    facts = [e for e in edges if e["rel"] != DEPICTS]
    persons = set(person_types())
    for f in facts:  # the page links another person to their page
        f["person"] = f["other_type"] in persons
    dated = sorted(
        (f for f in facts if f["world_from"] or f["world_to"]),
        key=lambda f: (str(f["world_from"] or f["world_to"]), f["edge_id"]),
    )
    undated: dict[str, list[dict[str, Any]]] = {}
    for f in facts:
        if not (f["world_from"] or f["world_to"]):
            undated.setdefault(f"{f['rel']} {f['dir']}", []).append(f)
    relations = [
        {
            "rel": rows[0]["rel"],
            "dir": rows[0]["dir"],
            "count": len(rows),
            "facts": rows[:PERSON_FACTS_PER_RELATION],
        }
        for rows in sorted(undated.values(), key=lambda rows: -len(rows))
    ]
    said = Counter(int(f["doc"]) for f in facts if f["doc"] is not None)
    titles = {
        int(r["id"]): r
        for r in con.execute(
            "SELECT id, title, json_extract(meta, '$.published.date') AS published"
            " FROM documents WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(said)),),
        )
    }
    documents = [
        {
            "id": d,
            "title": titles[d]["title"] if d in titles else None,
            "published": titles[d]["published"] if d in titles else None,
            "facts": n,
        }
        for d, n in said.most_common(PERSON_DOCUMENTS)
    ]
    # a name only a hidden document gave is as hidden as the document
    labels: dict[tuple[str, str], dict[str, Any]] = {}
    for r in con.execute(
        "SELECT label, lang, source_doc FROM entity_labels"
        " WHERE entity_id IN (SELECT value FROM json_each(?)) AND label != ?"
        " ORDER BY label",
        (json.dumps(ids), name),
    ):
        if r["source_doc"] is None or int(r["source_doc"]) not in hidden:
            labels.setdefault(
                (r["label"], r["lang"] or ""), {"label": r["label"], "lang": r["lang"]}
            )
    return {
        "id": canon,
        "name": name,
        "type": row["type"],
        "labels": list(labels.values()),
        "timeline": dated,
        "relations": relations,
        "facts": len(facts),
        "documents": documents,
        "documents_total": len(said),
        **_photos_part(con, ids, hidden),
    }


@_reading
def people(
    con: sqlite3.Connection, *, q: str = "", limit: int = 50
) -> list[dict[str, Any]]:
    """Persons, most connected first: those whose name or a label holds
    ``q``, or with none the most connected of all, each with its number of
    visible facts and of pictures that depict it."""
    limit = max(1, min(limit, PEOPLE_LIMIT))
    types = person_types()
    hidden = hidden_documents(con)
    marks = ",".join("?" * len(types))
    where, args = "", []
    if q.strip():
        where = (
            " AND (n.name LIKE ? OR n.id IN (SELECT entity_id FROM entity_labels"
            " WHERE label LIKE ?))"
        )
        pattern = f"%{q.strip()}%"
        args = [pattern, pattern]
    counts: Counter[int] = Counter()
    photos: Counter[int] = Counter()
    for end in ("src", "dst"):
        for r in con.execute(
            # a person merged into a thing of another type is that thing
            f"SELECT c.id AS cid, e.rel, e.source_doc FROM entities n"
            f" JOIN entities c ON c.id = COALESCE(n.canonical_id, n.id)"
            f" JOIN edges e ON e.{end} = n.id AND e.valid_to IS NULL"
            f" WHERE c.type IN ({marks}){where}",
            (*types, *args),
        ):
            if r["source_doc"] is not None and int(r["source_doc"]) in hidden:
                continue
            if r["rel"] != DEPICTS:
                counts[int(r["cid"])] += 1
            elif end == "dst":
                photos[int(r["cid"])] += 1
    seen = set(counts) | set(photos)
    ranked = sorted(seen, key=lambda i: (-photos[i], -counts[i], i))[:limit]
    names = {
        int(r["id"]): r
        for r in con.execute(
            "SELECT id, name, type FROM entities"
            " WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(ranked),),
        )
    }
    return [
        {
            "id": i,
            "name": names[i]["name"],
            "type": names[i]["type"],
            "facts": counts[i],
            "photos": photos[i],
        }
        for i in ranked
        if i in names
    ]
