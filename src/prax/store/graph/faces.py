"""The faces as people (plan item AT steps 3 to 5): the groups of faces
that are probably one person, what a person said of each face, the
matching that offers an open face as someone, a picture's faces as its
page lists them, and a faces reading's faces with what was said of them
carried over.

The vector side (the face index, the crops, the nearest faces) is
``retrieval.faces``; what a person says of a face is ``naming``, which
owns ``face_names`` and ``face_refusals``. This part reads both, after
them in the graph's ``ORDER``, so a face's live answer is one join
(``naming.LIVE_ANSWER``) and an event's pictures one relation
(``events.RECORDED_AT``).

Faces are biometric. Every read here goes through the wall: a face of a
picture the viewer may not see is not there, and no named token's route
reaches these reads.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from prax.ml import matching

from ..base import (
    _get_vectors,
    _reading,
    _serialized,
    document_hidden,
    hidden_documents,
    now,
)
from ..documents import _checked_face, _put_meta, get_meta
from ..retrieval import FACES_PAGE, face_index_path, face_model, match_settings
from .events import RECORDED_AT
from .naming import LIVE_ANSWER, _carry_answers

# how much a new face's box must cover an old one's to be the same face
# read again: past it, what a person said of the old face moves to it
SAME_FACE_IOU = 0.4


def _iou(a: list[float], b: list[float]) -> float:
    """The overlap of two ``x, y, w, h`` boxes over their union."""
    w = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = w * h
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _successors(
    old: dict[int, list[float]], new: dict[int, list[float]]
) -> dict[int, int]:
    """Each old face the new reading found again, with its new face: the
    pairs of most overlap first, each face in one pair at most."""
    pairs = sorted(
        ((_iou(ob, nb), o, n) for o, ob in old.items() for n, nb in new.items()),
        reverse=True,
    )
    out: dict[int, int] = {}
    taken: set[int] = set()
    for iou, o, n in pairs:
        if iou < SAME_FACE_IOU:
            break
        if o not in out and n not in taken:
            out[o] = n
            taken.add(n)
    return out


@_serialized
def apply_faces(
    con: sqlite3.Connection,
    doc_id: int,
    *,
    faces: list[dict[str, Any]],
    version: int,
    by: str,
    width: int,
    height: int,
    source: str = "preview",
) -> str:
    """A picture's faces reading: the faces an earlier reading found ended
    (``gone_at``), the new ones written, and ``meta.faces`` saying which
    reading, of which copy at what size, and how many. What a person said
    of an old face moves to the new face whose box covers it
    (``SAME_FACE_IOU``); an answer whose face was not found again stays on
    the ended face, its ``depicts`` fact with it, and its action's undo
    still takes it back. A reading older than the one the picture holds
    changes nothing (a worker's post after its lease ran out). Returns
    ``found``, ``none`` or ``stale``."""
    checked = [_checked_face(f) for f in faces]
    meta = get_meta(con, doc_id)
    if int((meta.get("faces") or {}).get("v") or 0) > version:
        return "stale"
    at = now()
    old = {
        int(r[0]): json.loads(r[1])
        for r in con.execute(
            "SELECT id, box FROM faces WHERE doc_id = ? AND gone_at IS NULL",
            (doc_id,),
        )
    }
    con.execute(
        "UPDATE faces SET gone_at = ? WHERE doc_id = ? AND gone_at IS NULL",
        (at, doc_id),
    )
    new: dict[int, list[float]] = {}
    for box, marks, score, px in checked:
        cur = con.execute(
            "INSERT INTO faces (doc_id, box, landmarks, score, px, detector, found_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc_id, box, marks, score, px, by, at),
        )
        new[int(cur.lastrowid or 0)] = json.loads(box)
    if old and new:
        _carry_answers(con, _successors(old, new), at)
    meta["faces"] = {
        "v": version,
        "by": by,
        "at": at,
        "n": len(checked),
        "from": source,
        "width": int(width),
        "height": int(height),
    }
    _put_meta(con, doc_id, meta)
    con.commit()
    return "found" if checked else "none"


# the groups (plan item AT step 3)
GROUPS_PAGE = 60  # groups a page of the Review page shows at most
GROUP_STRIP = 12  # faces of a group the list shows, the firmest first
GROUP_CARRY = 0.5  # a new group takes the id of an old one this close (Jaccard)


@_serialized
def replace_face_groups(
    con: sqlite3.Connection,
    faces: list[int],
    labels: list[int],
    strength: list[float],
    persistence: list[float],
    *,
    model: str,
    run: str,
) -> dict[str, int]:
    """Replace the face groups with a new grouping (``prax.ml.clusters``:
    ``labels[i]`` is face ``faces[i]``'s group or -1). A group that overlaps
    an old one by ``GROUP_CARRY`` or more takes its id. Returns the counts."""
    old: dict[int, set[int]] = {}
    for r in con.execute("SELECT face_id, group_id FROM face_group_members"):
        old.setdefault(int(r[1]), set()).add(int(r[0]))
    was = {face: g for g, members in old.items() for face in members}
    members: dict[int, list[int]] = {}
    for i, g in enumerate(labels):
        if g >= 0:
            members.setdefault(g, []).append(i)
    docs = {
        int(r[0]): int(r[1])
        for r in con.execute(
            "SELECT id, doc_id FROM faces WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(faces),),
        )
    }
    taken: set[int] = set()
    # an id no group ever had: a page names a group by its id, and a group
    # that dissolved must not lend its id to another (id_marks, 59)
    mark = con.execute(
        "SELECT last FROM id_marks WHERE name = 'face_groups'"
    ).fetchone()
    next_id = max(int(mark[0]) if mark else 0, max(old, default=0)) + 1
    stamp = now()
    groups, rows = [], []
    report = {"groups": len(members), "carried": 0, "new": 0, "doubled": 0}
    # the largest first, so it is the one that keeps a contested id
    for g, at in sorted(members.items(), key=lambda kv: -len(kv[1])):
        mine = {faces[i] for i in at}
        best, best_j = None, 0.0
        for gid in sorted({was[f] for f in mine if f in was} - taken):
            j = len(mine & old[gid]) / len(mine | old[gid])
            if j > best_j:
                best, best_j = gid, j
        if best is not None and best_j >= GROUP_CARRY:
            gid = best
            report["carried"] += 1
        else:
            gid = next_id
            next_id += 1
            report["new"] += 1
        taken.add(gid)
        per_doc: dict[int, int] = {}
        for f in mine:
            per_doc[docs[f]] = per_doc.get(docs[f], 0) + 1
        doubled = sum(1 for n in per_doc.values() if n > 1)
        report["doubled"] += doubled > 0
        groups.append(
            (gid, len(mine), len(per_doc), doubled, persistence[g], model, run, stamp)
        )
        rows += [(faces[i], gid, round(strength[i], 4)) for i in at]
    con.execute(
        "INSERT INTO id_marks (name, last) VALUES ('face_groups', ?)"
        " ON CONFLICT (name) DO UPDATE SET last = max(last, excluded.last)",
        (next_id - 1,),
    )
    con.execute("DELETE FROM face_group_members")
    con.execute("DELETE FROM face_groups")
    con.executemany(
        "INSERT INTO face_groups (id, size, pictures, doubled, persistence, model,"
        " run, built_at) VALUES (?,?,?,?,?,?,?,?)",
        groups,
    )
    con.executemany(
        "INSERT INTO face_group_members (face_id, group_id, strength) VALUES (?,?,?)",
        rows,
    )
    con.commit()
    return report


def _group_faces(
    con: sqlite3.Connection,
    group_ids: list[int],
    hidden: frozenset[int],
    docs: frozenset[int] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """The live, visible faces of these groups (of ``docs`` only, when
    given), the firmest first, each with its live answer (step 4's
    ``face_names``): ``verdict`` None for a face nobody has answered for."""
    out: dict[int, list[dict[str, Any]]] = {g: [] for g in group_ids}
    for r in con.execute(
        "SELECT m.group_id, m.strength, f.id, f.doc_id, f.score, f.px,"
        " f.crop IS NOT NULL AS crop, n.verdict, e.id AS entity_id, e.name"
        " FROM face_group_members m JOIN faces f ON f.id = m.face_id"
        + LIVE_ANSWER
        + " WHERE m.group_id IN (SELECT value FROM json_each(?))"
        " AND f.gone_at IS NULL ORDER BY m.group_id, m.strength DESC, f.id",
        (json.dumps(group_ids),),
    ):
        doc = int(r["doc_id"])
        if doc in hidden or (docs is not None and doc not in docs):
            continue
        out[int(r["group_id"])].append(_face_item(r))
    return out


def _face_item(r: sqlite3.Row) -> dict[str, Any]:
    keys = r.keys()
    return {
        "id": int(r["id"]),
        "doc_id": int(r["doc_id"]),
        "score": float(r["score"]),
        "px": int(r["px"]),
        "strength": float(r["strength"]) if "strength" in keys else None,
        "crop": bool(r["crop"]),
        "verdict": r["verdict"],
        "entity_id": r["entity_id"],
        "name": r["name"],
    }


def _group_line(gid: int | None, faces: list[dict[str, Any]]) -> dict[str, Any]:
    per_doc: dict[int, int] = {}
    named: dict[int, dict[str, Any]] = {}
    for f in faces:
        per_doc[f["doc_id"]] = per_doc.get(f["doc_id"], 0) + 1
        if f["verdict"] == "person":
            who = named.setdefault(
                f["entity_id"],
                {"entity_id": f["entity_id"], "name": f["name"], "faces": 0},
            )
            who["faces"] += 1
    return {
        "id": gid,
        "size": len(faces),
        "open": sum(1 for f in faces if f["verdict"] is None),
        "pictures": len(per_doc),
        # two faces of one picture: surely more than one person
        "doubled": sorted(d for d, n in per_doc.items() if n > 1),
        "named": sorted(named.values(), key=lambda w: (-w["faces"], w["entity_id"])),
        "judged": sum(1 for f in faces if f["verdict"] in ("not_face", "nobody")),
    }


def _event_docs(
    con: sqlite3.Connection, event_id: int, hidden: frozenset[int]
) -> tuple[str, frozenset[int]]:
    """An event's name and the visible pictures recorded at it (its alias
    group's live ``recorded_at`` edges); KeyError for none."""
    row = con.execute(
        "SELECT c.id, c.name FROM entities e"
        " JOIN entities c ON c.id = COALESCE(e.canonical_id, e.id)"
        " WHERE e.id = ? AND c.type = 'event'",
        (event_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"no such event: {event_id}")
    docs = frozenset(
        int(r[0])
        for r in con.execute(
            "SELECT x.source_doc FROM edges x JOIN entities t ON t.id = x.dst"
            " WHERE x.rel = ? AND x.valid_to IS NULL"
            " AND COALESCE(t.canonical_id, t.id) = ? AND x.source_doc IS NOT NULL",
            (RECORDED_AT, int(row["id"])),
        )
        if int(r[0]) not in hidden
    )
    return str(row["name"]), docs


def _events_with_faces(
    con: sqlite3.Connection, hidden: frozenset[int]
) -> list[dict[str, Any]]:
    """The events whose pictures hold faces nobody has answered for, the
    most such faces first: what the tab's choice of one event lists."""
    count: dict[int, int] = {}
    names: dict[int, str] = {}
    for r in con.execute(
        "SELECT COALESCE(t.canonical_id, t.id) AS ev, f.doc_id, count(*) AS n"
        " FROM faces f JOIN edges x ON x.source_doc = f.doc_id"
        " JOIN entities t ON t.id = x.dst"
        " WHERE x.rel = ? AND x.valid_to IS NULL AND f.gone_at IS NULL"
        " AND f.id NOT IN (SELECT face_id FROM face_names WHERE ended_at IS NULL)"
        " GROUP BY ev, f.doc_id",
        (RECORDED_AT,),
    ):
        if int(r["doc_id"]) not in hidden:
            count[int(r["ev"])] = count.get(int(r["ev"]), 0) + int(r["n"])
    if count:
        names = {
            int(r[0]): str(r[1])
            for r in con.execute(
                "SELECT id, name FROM entities WHERE id IN"
                " (SELECT value FROM json_each(?))",
                (json.dumps(sorted(count)),),
            )
        }
    return [
        {"id": ev, "name": names.get(ev, ""), "faces": n}
        for ev, n in sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


@_reading
def face_groups(
    con: sqlite3.Connection,
    *,
    offset: int = 0,
    limit: int = 30,
    event: int | None = None,
) -> dict[str, Any]:
    """The face groups with faces nobody has answered for, the most such
    first, each with its firmest open faces (``GROUP_STRIP``), its size,
    pictures and the pictures that hold two of its faces, and who its
    faces were named so far (``named``). ``done`` counts the groups every
    face of which is answered for. With ``event`` (an event's entity id)
    only that event's pictures count, and ``loose`` holds their open faces
    in no group. ``events`` lists the events with open faces. A face of a
    hidden picture is left out, and a group of none is absent."""
    hidden = hidden_documents(con)
    docs = None
    out: dict[str, Any] = {}
    if event is not None:
        title, docs = _event_docs(con, event, hidden)
        out["event"] = {"id": event, "name": title}
    ids = [
        int(r[0])
        for r in con.execute("SELECT id FROM face_groups ORDER BY size DESC, id")
    ]
    faces = _group_faces(con, ids, hidden, docs)
    lines = []
    done = 0
    for g in ids:
        if not faces[g]:
            continue
        line = _group_line(g, faces[g])
        if not line["open"]:
            done += 1
            continue
        line["faces"] = [f for f in faces[g] if f["verdict"] is None][:GROUP_STRIP]
        lines.append(line)
    lines.sort(key=lambda line: (-line["open"], line["id"]))
    if docs is not None:
        loose = [
            _face_item(r)
            for r in con.execute(
                "SELECT f.id, f.doc_id, f.score, f.px, f.crop IS NOT NULL AS crop,"
                " NULL AS verdict, NULL AS entity_id, NULL AS name FROM faces f"
                " WHERE f.gone_at IS NULL"
                " AND f.doc_id IN (SELECT value FROM json_each(?))"
                " AND f.id NOT IN (SELECT face_id FROM face_group_members)"
                " AND f.id NOT IN"
                " (SELECT face_id FROM face_names WHERE ended_at IS NULL)"
                " ORDER BY f.score DESC, f.id LIMIT ?",
                (json.dumps(sorted(docs)), FACES_PAGE),
            )
        ]
        out["loose"] = {**_group_line(None, loose), "faces": loose}
    built = con.execute("SELECT max(built_at) FROM face_groups").fetchone()
    limit = max(1, min(limit, GROUPS_PAGE))
    return {
        "total": len(lines),
        "grouped": sum(line["open"] for line in lines),
        "done": done,
        "built_at": built[0],
        "items": lines[max(0, offset) : max(0, offset) + limit],
        "events": _events_with_faces(con, hidden),
        **out,
    }


@_reading
def face_group(con: sqlite3.Connection, group_id: int) -> dict[str, Any]:
    """One group with all its visible faces: the open ones, the firmest
    first, then those answered for. KeyError when there is no such group
    or the viewer sees none of its faces."""
    faces = _group_faces(con, [group_id], hidden_documents(con))[group_id]
    if not faces:
        raise KeyError(f"no such face group: {group_id}")
    faces.sort(key=lambda f: f["verdict"] is not None)
    return {**_group_line(group_id, faces), "faces": faces}


# AT step 5: whose face an open face probably is, against every named face
MATCH_STRIP = 24  # suggested faces a person's row shows, the surest first
# the named side of the matching (their faces, people and unit vectors)
# and the last matching of every open face, each with the state it was
# made from: made again once an answer, a refusal, a merge or a vector is
# new. A picture's page matches its own few faces against the named side
# and leaves the whole matching to the Review page's tab
_NAMED: list[Any] = [None, None]
_MATCHED: list[Any] = [None, None]


def _named_state(
    con: sqlite3.Connection, hidden: frozenset[int], model: str
) -> tuple[Any, ...]:
    names = con.execute(
        "SELECT max(id), count(*) FILTER (WHERE ended_at IS NULL) FROM face_names"
    ).fetchone()
    faces = con.execute(
        "SELECT count(*), max(vector_at) FROM faces"
        " WHERE gone_at IS NULL AND vector_model = ?",
        (model,),
    ).fetchone()
    # who the named and refused entities are now: a merge or its undo
    # changes a canonical id and nothing above (the review of 2026-10-28)
    folded = con.execute(
        "SELECT count(canonical_id), total(canonical_id) FROM entities WHERE id IN"
        " (SELECT entity_id FROM face_names WHERE ended_at IS NULL"
        "  AND entity_id IS NOT NULL"
        "  UNION SELECT entity_id FROM face_refusals WHERE ended_at IS NULL)"
    ).fetchone()
    return (*names, *faces, *folded, hash(hidden), model)


def _named_side(
    con: sqlite3.Connection, hidden: frozenset[int], model: str
) -> tuple[list[tuple[int, int, int]], Any]:
    """The named faces with a vector, as ``(face, picture, person)`` with
    the canonical person, and their unit vectors as rows; None for no
    rows. Hidden pictures' faces are not matched against."""
    import numpy as np

    state = _named_state(con, hidden, model)
    if _NAMED[0] == state:
        return _NAMED[1]  # type: ignore[no-any-return]
    named = [
        (int(r[0]), int(r[1]), int(r[2]))
        for r in con.execute(
            "SELECT f.id, f.doc_id, COALESCE(e.canonical_id, e.id) FROM face_names n"
            " JOIN faces f ON f.id = n.face_id JOIN entities e ON e.id = n.entity_id"
            " WHERE n.ended_at IS NULL AND n.verdict = 'person'"
            " AND f.gone_at IS NULL AND f.vector_model = ? ORDER BY f.id",
            (model,),
        )
        if int(r[1]) not in hidden
    ]
    vecs = _get_vectors(face_index_path(model), [f for f, _, _ in named])
    named = [n for n in named if n[0] in vecs]
    rows = matching.unit(np.stack([vecs[f] for f, _, _ in named])) if named else None
    _NAMED[:] = [state, (named, rows)]
    return named, rows


def _match_faces(
    con: sqlite3.Connection,
    asked: list[tuple[int, int]],
    hidden: frozenset[int],
    k: int,
    model: str,
) -> list[tuple[int, int, matching.Match, int]]:
    """The match of each face of ``asked`` (``(face, picture)``) against
    the named side, as ``(face, picture, match, like)``, ``like`` the
    named face nearest of that person; a face with no vector or no person
    left is absent."""
    import numpy as np

    named, rows = _named_side(con, hidden, model)
    if not named or not asked:
        return []
    qvecs = _get_vectors(face_index_path(model), [f for f, _ in asked])
    asked = [a for a in asked if a[0] in qvecs]
    if not asked:
        return []
    refused: dict[int, set[int]] = {}
    for r in con.execute(
        "SELECT x.face_id, COALESCE(e.canonical_id, e.id) FROM face_refusals x"
        " JOIN entities e ON e.id = x.entity_id WHERE x.ended_at IS NULL"
        " AND x.face_id IN (SELECT value FROM json_each(?))",
        (json.dumps([f for f, _ in asked]),),
    ):
        refused.setdefault(int(r[0]), set()).add(int(r[1]))
    # a person named in a picture is not a second face of it
    in_picture: dict[int, set[int]] = {}
    for _, doc, person in named:
        in_picture.setdefault(doc, set()).add(person)
    found = matching.nearest_people(
        rows,
        [p for _, _, p in named],
        matching.unit(np.stack([qvecs[f] for f, _ in asked])),
        k=k,
        excluded={
            i: refused.get(face, set()) | in_picture.get(doc, set())
            for i, (face, doc) in enumerate(asked)
        },
    )
    return [
        (face, doc, m, named[m.like][0])
        for (face, doc), m in zip(asked, found, strict=True)
        if m is not None
    ]


def _open_faces(
    con: sqlite3.Connection,
    hidden: frozenset[int],
    model: str,
    doc_id: int | None = None,
) -> list[tuple[int, int]]:
    """The open faces with a vector, of one picture or of all."""
    return [
        (int(r[0]), int(r[1]))
        for r in con.execute(
            "SELECT id, doc_id FROM faces WHERE gone_at IS NULL AND vector_model = ?"
            " AND (? IS NULL OR doc_id = ?)"
            " AND id NOT IN (SELECT face_id FROM face_names WHERE ended_at IS NULL)"
            " ORDER BY id",
            (model, doc_id, doc_id),
        )
        if int(r[1]) not in hidden
    ]


def _matched(
    con: sqlite3.Connection, hidden: frozenset[int], k: int, model: str
) -> list[tuple[int, int, matching.Match, int]]:
    """Every open face with a vector and its match (``_match_faces``).
    Hidden pictures' faces are neither matched nor matched against."""
    refusals = con.execute(
        "SELECT max(id), count(*) FILTER (WHERE ended_at IS NULL) FROM face_refusals"
    ).fetchone()
    state = (*_named_state(con, hidden, model), *refusals, k)
    if _MATCHED[0] == state:
        return _MATCHED[1]  # type: ignore[no-any-return]
    out = _match_faces(con, _open_faces(con, hidden, model), hidden, k, model)
    _MATCHED[:] = [state, out]
    return out


@_reading
def face_matches(
    con: sqlite3.Connection,
    *,
    offset: int = 0,
    limit: int = 30,
    person: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """The open faces that are probably a person someone has named (AT
    step 5), by person, the person whose answer settles the most pictures
    first. A face is offered when its match (``prax.ml.matching``) is at
    least ``faces.match.line`` alike and ``faces.match.margin`` nearer
    than anyone else, both in prax.yaml (with the ``neighbours`` that
    vote). Each person's row holds their surest faces (``MATCH_STRIP``,
    or with ``person`` a page of their faces: ``limit`` of them, at most
    ``FACES_PAGE``, from ``offset``), each with its ``similarity``
    and the named face it is most like (``like``), and the person's
    ``like`` face to show beside them. ``open`` counts the open faces
    with a vector, ``offered`` those offered. Nothing is written: an
    answer is ``name_faces`` or ``refuse_faces``."""
    model = model or face_model()
    k, line, margin = match_settings()
    hidden = hidden_documents(con)
    rows: dict[int, list[tuple[int, int, matching.Match, int]]] = {}
    for item in _matched(con, hidden, k, model):
        m = item[2]
        if m.similarity >= line and m.margin >= margin:
            rows.setdefault(m.person, []).append(item)
    if person is not None:
        canonical = con.execute(
            "SELECT COALESCE(canonical_id, id) FROM entities WHERE id = ?", (person,)
        ).fetchone()
        if canonical is None:
            raise KeyError(f"no such entity: {person}")
        rows = {int(canonical[0]): rows.get(int(canonical[0]), [])}

    def pictures(p: int) -> int:
        return len({doc for _, doc, _, _ in rows[p]})

    order = sorted(rows, key=lambda p: (-pictures(p), p))
    limit = max(1, min(limit, GROUPS_PAGE))
    page = order if person is not None else order[max(0, offset) : offset + limit]
    ranked = {
        p: sorted(rows[p], key=lambda it: (-it[2].similarity, it[0])) for p in page
    }
    start = max(0, offset) if person is not None else 0
    strip = max(1, min(limit, FACES_PAGE)) if person is not None else MATCH_STRIP
    shown = {p: ranked[p][start : start + strip] for p in page}
    ids = {f for items in shown.values() for f, _, _, _ in items}
    ids |= {items[0][3] for items in ranked.values() if items}
    tiles = {
        int(r["id"]): r
        for r in con.execute(
            "SELECT f.id, f.doc_id, f.score, f.px, f.crop IS NOT NULL AS crop,"
            " NULL AS verdict, NULL AS entity_id, NULL AS name FROM faces f"
            " WHERE f.id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(ids)),),
        )
    }
    names = {
        int(r[0]): str(r[1])
        for r in con.execute(
            "SELECT id, name FROM entities"
            " WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(page),),
        )
    }
    items = []
    for p in page:
        items.append(
            {
                "entity_id": p,
                "name": names.get(p, ""),
                "open": len(rows[p]),
                "pictures": pictures(p),
                "like": _face_item(tiles[ranked[p][0][3]]) if ranked[p] else None,
                "faces": [
                    {
                        **_face_item(tiles[f]),
                        "similarity": round(m.similarity, 4),
                        "margin": round(m.margin, 4),
                        "like": like,
                    }
                    for f, _, m, like in shown[p]
                ],
            }
        )
    return {
        "total": len(order),
        "offered": sum(len(v) for v in rows.values()),
        "open": con.execute(
            "SELECT count(*) FROM faces WHERE gone_at IS NULL AND vector_model = ?"
            " AND id NOT IN (SELECT face_id FROM face_names WHERE ended_at IS NULL)",
            (model,),
        ).fetchone()[0],
        "line": line,
        "margin": margin,
        "items": items,
    }


@_reading
def picture_faces(
    con: sqlite3.Connection, doc_id: int, *, model: str | None = None
) -> list[dict[str, Any]]:
    """The faces of one picture as its page lists them under it, the most
    certain first: each its ``id``, ``box`` (fractions of the upright
    picture), ``score``, ``px``, ``crop`` (whether it has one), its live
    ``answer`` (``verdict``, ``entity_id``, ``name``, ``run``) or None,
    and for an open face the matching offers (as ``face_matches`` does,
    at the same line and margin) the ``offer``: ``entity_id``, ``name``,
    ``similarity``. Nothing for a picture the viewer may not see."""
    model = model or face_model()
    if document_hidden(con, doc_id):
        return []
    rows = con.execute(
        "SELECT f.id, f.box, f.score, f.px, f.crop IS NOT NULL AS crop,"
        " n.verdict, e.id AS entity_id, n.run, e.name FROM faces f"
        + LIVE_ANSWER
        + " WHERE f.doc_id = ? AND f.gone_at IS NULL ORDER BY f.score DESC, f.id",
        (doc_id,),
    ).fetchall()
    offers: dict[int, matching.Match] = {}
    if any(r["verdict"] is None for r in rows):
        k, line, margin = match_settings()
        hidden = hidden_documents(con)
        offers = {
            face: m
            for face, _, m, _ in _match_faces(
                con, _open_faces(con, hidden, model, doc_id), hidden, k, model
            )
            if m.similarity >= line and m.margin >= margin
        }
    names = {
        int(r[0]): str(r[1])
        for r in con.execute(
            "SELECT id, name FROM entities"
            " WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted({m.person for m in offers.values()})),),
        )
    }
    out = []
    for r in rows:
        face = int(r["id"])
        m = offers.get(face)
        out.append(
            {
                "id": face,
                "box": json.loads(r["box"]),
                "score": float(r["score"]),
                "px": int(r["px"]),
                "crop": bool(r["crop"]),
                "answer": None
                if r["verdict"] is None
                else {
                    "verdict": r["verdict"],
                    "entity_id": r["entity_id"],
                    "name": r["name"],
                    "run": r["run"],
                },
                "offer": None
                if m is None
                else {
                    "entity_id": m.person,
                    "name": names.get(m.person, ""),
                    "similarity": round(m.similarity, 4),
                },
            }
        )
    return out
