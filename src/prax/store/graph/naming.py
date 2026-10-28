"""Who a face is (plan item AT step 4): what a person says of faces on the
Review page, and the facts that follow.

A person names faces, never a group: the face groups are made again by
every night's pass (migration 55), and a face's id stays. Naming the
faces of a group but the ones a person unticks is a split; naming two
groups as one person is a merge; neither needs a step of its own. A
name is a ``person`` entity, one the graph has or a new one, and each
picture holding a named face ``depicts`` them: an EXTRACTED edge with
producer ``human`` under the action's run, sourced to the picture, so
the wall hides it with the picture. A face may also be ``not_face`` or
``nobody`` (a face with no one to name). One live answer a face
(``face_names``, migration 56); a new answer ends the old one, and the
``depicts`` edge an answer held up is ended when no live answer holds it
up any more. Nothing is deleted (invariant 8).

A picture with a named face is ``suspected`` at least (the plan's wall):
a rule's mark, so a person's own decision about the picture stands.

Step 5 offers open faces as the person they are probably
(``store.face_matches``). Naming those is naming as here, under a run
of kind ``match``, so what was confirmed can be counted later; a face
said not to be that person is a refusal (``face_refusals``, migration
57), and the matching never offers it as them again. An action's
refusals share its run, so its undo takes them back too.

Faces are biometric. Nothing here reaches a model, and no named token's
route reaches these writes.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any

from ..base import _reading, _serialized, document_hidden, now
from .edges import Edge, document_node, end_edge, link
from .labels import PERSON, add_label, display_language, merge_entities
from .people import DEPICTS, person_types

FACE_VERDICTS = ("person", "not_face", "nobody")
FACE_NAME_CHARS = 120
FACES_AT_ONCE = 2000  # faces one action may answer for


def _faces(
    con: sqlite3.Connection, face_ids: list[int], *, gone: bool = False
) -> dict[int, int]:
    """The live faces asked about, each with its picture: ValueError for
    none, too many, or one that is gone (unless ``gone``: an undo may take
    back an answer a later reading left on an ended face), unknown or in a
    picture the viewer may not see (as if absent)."""
    ids = sorted({int(f) for f in face_ids})
    if not ids:
        raise ValueError("no faces given")
    if len(ids) > FACES_AT_ONCE:
        raise ValueError(f"at most {FACES_AT_ONCE} faces at once")
    found = {
        int(r[0]): int(r[1])
        for r in con.execute(
            "SELECT id, doc_id FROM faces WHERE (gone_at IS NULL OR ?)"
            " AND id IN (SELECT value FROM json_each(?))",
            (gone, json.dumps(ids)),
        )
    }
    for face in ids:
        if face not in found or document_hidden(con, found[face]):
            raise ValueError(f"no such face: {face}")
    return found


def _run(kind: str) -> str:
    """An action's run: its second and a few random letters, since a
    person answers for two groups within one second."""
    return f"{kind}-{now().replace(':', '').replace('-', '')}-{secrets.token_hex(3)}"


def _end_answers(con: sqlite3.Connection, face_ids: list[int], run: str) -> int:
    """End the live answers of these faces, and each ``depicts`` edge no
    live answer holds up afterwards, for ``run``. Returns the edges
    ended."""
    rows = con.execute(
        "SELECT id, edge_id FROM face_names WHERE ended_at IS NULL"
        " AND face_id IN (SELECT value FROM json_each(?))",
        (json.dumps(face_ids),),
    ).fetchall()
    if not rows:
        return 0
    con.executemany(
        "UPDATE face_names SET ended_at = ? WHERE id = ?",
        [(now(), int(r["id"])) for r in rows],
    )
    ended = 0
    for edge in {int(r["edge_id"]) for r in rows if r["edge_id"] is not None}:
        held = con.execute(
            "SELECT 1 FROM face_names WHERE edge_id = ? AND ended_at IS NULL",
            (edge,),
        ).fetchone()
        if held is None and end_edge(con, edge, run=run):
            ended += 1
    return ended


def _end_refusals(
    con: sqlite3.Connection, face_ids: list[int], entity_id: int | None = None
) -> int:
    """End the live refusals of these faces (of ``entity_id`` only, when
    given). Returns how many."""
    cur = con.execute(
        "UPDATE face_refusals SET ended_at = ? WHERE ended_at IS NULL"
        " AND face_id IN (SELECT value FROM json_each(?))"
        " AND (? IS NULL OR entity_id = ?)",
        (now(), json.dumps(face_ids), entity_id, entity_id),
    )
    return cur.rowcount


def _refuse(
    con: sqlite3.Connection, face_ids: list[int], entity_id: int, by: str, run: str
) -> int:
    """These faces are not ``entity_id``: a live refusal each, where the
    face has none for them yet. Returns how many were written."""
    written = 0
    for face in face_ids:
        written += con.execute(
            "INSERT INTO face_refusals (face_id, entity_id, by, run, said_at)"
            " SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM face_refusals"
            " WHERE face_id = ? AND entity_id = ? AND ended_at IS NULL)",
            (face, entity_id, by, run, now(), face, entity_id),
        ).rowcount
    return written


def _person(
    con: sqlite3.Connection, entity_id: int | None, name: str | None
) -> tuple[str, str]:
    """The person named, as ``(name, type)``: an entity of a person type
    (its canonical one), or a new person called ``name``."""
    if entity_id is not None:
        row = con.execute(
            "SELECT c.name, c.type FROM entities e"
            " JOIN entities c ON c.id = COALESCE(e.canonical_id, e.id)"
            " WHERE e.id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"no such entity: {entity_id}")
        if row["type"] not in person_types():
            raise ValueError(f"entity {entity_id} is a {row['type']}, not a person")
        return str(row["name"]), str(row["type"])
    title = " ".join((name or "").split())[:FACE_NAME_CHARS]
    if not title:
        raise ValueError("a person needs a name, or an entity_id")
    return title, "person"


def _suspect_picture(con: sqlite3.Connection, doc_id: int) -> bool:
    """A picture with a named face marked ``suspected``, when it is open
    and no person has decided about it. True when it was marked."""
    row = con.execute(
        "SELECT sensitivity, meta FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    meta = json.loads(row["meta"] or "{}")
    if (
        row["sensitivity"] is not None
        or (meta.get("sensitivity") or {}).get("by") == "human"
    ):
        return False
    meta["sensitivity"] = {"state": "suspected", "by": "faces", "at": now()}
    con.execute(
        "UPDATE documents SET sensitivity = 'suspected', meta = ? WHERE id = ?",
        (json.dumps(meta), doc_id),
    )
    return True


@_serialized
def name_faces(
    con: sqlite3.Connection,
    face_ids: list[int],
    *,
    entity_id: int | None = None,
    name: str | None = None,
    by: str = PERSON,
    refused: list[int] | None = None,
    matched: bool = False,
) -> dict[str, Any]:
    """These faces are one person: ``entity_id`` (a person the graph has)
    or a new person called ``name`` (a person of that name already there
    is that person: a name is the identity until stratification step 5).
    Each picture ``depicts`` them, written once a picture; an earlier
    answer for a face is ended, and a refusal of this person for it.
    ``refused`` are faces said not to be them, under the same run (step
    5's "not them"); ``matched`` says the matching offered the faces,
    which names the run's kind. Returns the ``run``, the person's
    ``entity_id`` and ``name``, and how many ``faces``, ``pictures``, new
    ``facts``, newly ``suspected`` pictures and ``refused`` faces."""
    faces = _faces(con, face_ids)
    others = sorted(_faces(con, refused)) if refused else []
    if set(others) & set(faces):
        raise ValueError("a face is both named and refused")
    pname, ptype = _person(con, entity_id, name)
    run = _run("match" if matched else "faces")
    _end_answers(con, sorted(faces), run)
    by_doc: dict[int, list[int]] = {}
    for face, doc in faces.items():
        by_doc.setdefault(doc, []).append(face)
    facts = suspected = 0
    person_id = None
    rows = []
    for doc, mine in sorted(by_doc.items()):
        node, ntype = document_node(con, doc)
        taken = con.execute(
            "SELECT json_extract(meta, '$.taken.date') FROM documents WHERE id = ?",
            (doc,),
        ).fetchone()[0]
        day = str(taken or "")[:10]
        held = con.execute(
            "SELECT n.edge_id FROM face_names n JOIN faces f ON f.id = n.face_id"
            " JOIN edges e ON e.id = n.edge_id JOIN entities t ON t.id = e.dst"
            " WHERE f.doc_id = ? AND n.ended_at IS NULL AND e.valid_to IS NULL"
            " AND t.name = ? AND t.type = ? LIMIT 1",
            (doc, pname, ptype),
        ).fetchone()
        if held is not None:
            edge = int(held[0])
        else:
            edge = link(
                con,
                Edge(node, ntype, DEPICTS, pname, ptype),
                source_doc=doc,
                evidence=f"face {', '.join(map(str, mine))} named {pname!r}"
                " by a person",
                producer=by,
                run=run,
                world_from=day if len(day) == 10 else None,
            )
            facts += 1
        if person_id is None:
            person_id = int(
                con.execute("SELECT dst FROM edges WHERE id = ?", (edge,)).fetchone()[0]
            )
        suspected += _suspect_picture(con, doc)
        rows += [(face, "person", person_id, edge, by, run, now()) for face in mine]
    con.executemany(
        "INSERT INTO face_names (face_id, verdict, entity_id, edge_id, by, run,"
        " said_at) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    assert person_id is not None
    _end_refusals(con, sorted(faces), person_id)
    said_not = _refuse(con, others, person_id, by, run) if others else 0
    con.commit()
    return {
        "run": run,
        "entity_id": person_id,
        "name": pname,
        "faces": len(faces),
        "pictures": len(by_doc),
        "facts": facts,
        "suspected": suspected,
        "refused": said_not,
    }


@_serialized
def refuse_faces(
    con: sqlite3.Connection, face_ids: list[int], *, entity_id: int, by: str = PERSON
) -> dict[str, Any]:
    """These faces are not ``entity_id`` (step 5's "none of them"): the
    matching never offers them as that person again, and their answers
    stay as they were. Returns the ``run``, the person's canonical
    ``entity_id`` and how many ``faces`` were newly refused."""
    faces = sorted(_faces(con, face_ids))
    _person(con, entity_id, None)
    person = int(
        con.execute(
            "SELECT COALESCE(canonical_id, id) FROM entities WHERE id = ?",
            (entity_id,),
        ).fetchone()[0]
    )
    run = _run("match")
    written = _refuse(con, faces, person, by, run)
    con.commit()
    return {"run": run, "entity_id": person, "faces": written}


@_serialized
def judge_faces(
    con: sqlite3.Connection, face_ids: list[int], *, verdict: str, by: str = PERSON
) -> dict[str, Any]:
    """These faces are no face at all (``not_face``) or a face with nobody
    to name (``nobody``); an earlier answer is ended, and with it a
    ``depicts`` edge it alone held up. Returns the ``run`` and counts."""
    if verdict not in FACE_VERDICTS or verdict == "person":
        raise ValueError("a verdict is not_face or nobody; a person is named")
    faces = _faces(con, face_ids)
    run = _run("faces")
    ended = _end_answers(con, sorted(faces), run)
    con.executemany(
        "INSERT INTO face_names (face_id, verdict, by, run, said_at)"
        " VALUES (?,?,?,?,?)",
        [(face, verdict, by, run, now()) for face in sorted(faces)],
    )
    con.commit()
    return {"run": run, "verdict": verdict, "faces": len(faces), "ended": ended}


@_serialized
def unname_faces(
    con: sqlite3.Connection,
    face_ids: list[int] | None = None,
    *,
    run: str | None = None,
) -> dict[str, Any]:
    """Take answers back: those of these faces, or every answer of one
    action (``run``), and the refusals with them. The faces are unanswered
    again, and a ``depicts`` edge no answer holds up is ended. A picture
    marked suspected stays so: the face was named once. Returns how many
    ``faces``, ``ended`` edges and ``refusals`` ended."""
    if run is not None:
        ids = [
            int(r[0])
            for r in con.execute(
                "SELECT face_id FROM face_names WHERE run = ? AND ended_at IS NULL",
                (run,),
            )
        ]
        said_not = [
            int(r[0])
            for r in con.execute(
                "SELECT face_id FROM face_refusals WHERE run = ? AND ended_at IS NULL",
                (run,),
            )
        ]
        if not ids and not said_not:
            raise KeyError(f"no live answers of run {run}")
        _faces(con, ids + said_not, gone=True)
        refusals = con.execute(
            "UPDATE face_refusals SET ended_at = ? WHERE run = ? AND ended_at IS NULL",
            (now(), run),
        ).rowcount
    else:
        ids = sorted(_faces(con, face_ids or []))
        refusals = _end_refusals(con, ids)
    ended = _end_answers(con, ids, _run("unname")) if ids else 0
    con.commit()
    return {"faces": len(ids), "ended": ended, "refusals": refusals}


@_reading
def face_answers(con: sqlite3.Connection, face_ids: list[int]) -> dict[int, Any]:
    """The live answer for each of these faces that has one: its
    ``verdict``, and for a person its ``entity_id`` and ``name``, and the
    ``run``. A face of a picture the viewer may not see is left out."""
    out: dict[int, Any] = {}
    for r in con.execute(
        # the person as they are now: a merge folds the entity named
        "SELECT n.face_id, n.verdict, e.id AS entity_id, n.run, f.doc_id, e.name"
        " FROM face_names n JOIN faces f ON f.id = n.face_id"
        " LEFT JOIN entities x ON x.id = n.entity_id"
        " LEFT JOIN entities e ON e.id = COALESCE(x.canonical_id, x.id)"
        " WHERE n.ended_at IS NULL AND n.face_id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted({int(f) for f in face_ids})),),
    ):
        if document_hidden(con, int(r["doc_id"])):
            continue
        out[int(r["face_id"])] = {
            "verdict": r["verdict"],
            "entity_id": r["entity_id"],
            "name": r["name"],
            "run": r["run"],
        }
    return out


# ------------------------------------------------- a person's name, a person twice
# What the person page changes: a name, and two entities that are one
# person. Both are claims under a run of their own, which ``unmerge_run``
# takes back whole; the faces follow, since a face is named as an entity
# and the reads show its canonical one.


def _canonical_person(con: sqlite3.Connection, entity_id: int) -> sqlite3.Row:
    row = con.execute(
        "SELECT c.id, c.name, c.type FROM entities e"
        " JOIN entities c ON c.id = COALESCE(e.canonical_id, e.id)"
        " WHERE e.id = ?",
        (entity_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"no such entity: {entity_id}")
    if row["type"] not in person_types():
        raise ValueError(f"entity {entity_id} is a {row['type']}, not a person")
    return row  # type: ignore[no-any-return]


@_serialized
def rename_person(
    con: sqlite3.Connection, entity_id: int, name: str, *, by: str = PERSON
) -> dict[str, Any]:
    """A person called something else: the new name preferred in the
    language the library shows, the old one kept as a name they are also
    known by and marked as what they were called (``was``), both under one
    run that ``unmerge_run`` takes back. A name another entity of the type
    already carries is a ValueError naming it: two things may not show one
    name, and when they are one person ``merge_people`` says so. Returns
    ``entity_id`` (the canonical one), ``name``, ``was`` and ``run`` (None
    when the name was the same)."""
    row = _canonical_person(con, entity_id)
    eid, old = int(row["id"]), str(row["name"])
    title = " ".join((name or "").split())[:FACE_NAME_CHARS]
    if not title:
        raise ValueError("a person needs a name")
    if title == old:
        return {"entity_id": eid, "name": old, "was": old, "run": None}
    other = con.execute(
        "SELECT COALESCE(canonical_id, id) FROM entities"
        " WHERE name = ? AND type = ? AND COALESCE(canonical_id, id) != ?",
        (title, row["type"], eid),
    ).fetchone()
    if other is not None:
        raise ValueError(
            f"another {row['type']} (#{other[0]}) is called {title!r}: if they"
            " are one person, say so with 'same person as'"
        )
    run = _run("rename")
    # a name only an entity folded into this person shows: it is theirs,
    # so the folded one's cached name steps aside (its label stays, and a
    # link of that name lands on the person, who answers to it)
    for alias in con.execute(
        "SELECT id FROM entities WHERE name = ? AND type = ? AND canonical_id = ?",
        (title, row["type"], eid),
    ).fetchall():
        aid = int(alias[0])
        add_label(con, aid, title, producer=by)
        con.execute(
            "UPDATE entities SET name = ? WHERE id = ?", (f"{title} (#{aid})", aid)
        )
    marked = con.execute(
        "UPDATE entity_labels SET was = 1, run = ?"
        " WHERE entity_id = ? AND label = ? COLLATE NOCASE",
        (run, eid, old),
    ).rowcount
    if not marked:
        add_label(con, eid, old, kind="alt", producer=by, run=run, was=True)
    add_label(
        con, eid, title, lang=display_language(), kind="pref", producer=by, run=run
    )
    shown = con.execute("SELECT name FROM entities WHERE id = ?", (eid,)).fetchone()
    con.commit()
    return {"entity_id": eid, "name": str(shown[0]), "was": old, "run": run}


@_serialized
def merge_people(
    con: sqlite3.Connection, other: int, into: int, *, by: str = PERSON
) -> dict[str, Any]:
    """Two entities that are one person: ``other`` folds into ``into``,
    whose name stays (``merge_entities``, under a run of its own that
    ``unmerge_run`` takes back). Its faces, pictures and facts are then
    the survivor's wherever a read follows the canonical entity. Returns
    ``kept``, ``merged`` and ``run``."""
    keep = _canonical_person(con, into)
    fold = _canonical_person(con, other)
    if int(keep["id"]) == int(fold["id"]):
        raise ValueError("that is the same person already")
    run = _run("merge")
    merge_entities(
        con,
        int(fold["id"]),
        int(keep["id"]),
        across_types=True,  # two person types (a person, an author) are one
        producer=by,
        run=run,
    )
    return {"kept": int(keep["id"]), "merged": int(fold["id"]), "run": run}
